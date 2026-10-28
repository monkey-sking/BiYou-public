"""Bilibili private message (DM) client.

Authenticated API client for polling and sending B站 private messages.
Reuses BiYou's existing B站 cookie/auth system.

Reference: https://github.com/icyux/bili-auth
"""

import hashlib
import json
import time
import urllib.parse
import uuid

import requests

from core.utils.cookie import get_user_agent


_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


class BiliDMClient:
    """Bilibili private message API client.

    Uses the same cookie/session system as BiYou's existing B站 integration.
    """

    def __init__(self, cookies, begin_ts: int | None = None, session=None):
        self.session = session or requests.Session()
        self.cookies = self._normalize_cookies(cookies)
        self.csrf = self.cookies.get("bili_jct", "")
        self.self_uid = self.cookies.get("DedeUserID", "")
        self.dev_id = str(uuid.uuid4()).upper()
        self.begin_ts = begin_ts or int(time.time() * 1000000)
        self._wbi_key = ""
        self._wbi_key_exp = 0

    @staticmethod
    def _normalize_cookies(cookies) -> dict:
        """Normalize cookie data from various formats (biliup, browser_cookie3, flat dict)."""
        if isinstance(cookies, dict) and "cookie_info" in cookies:
            cookies = cookies["cookie_info"].get("cookies", [])
        if isinstance(cookies, list):
            return {c.get("name"): c.get("value") for c in cookies if c.get("name")}
        if isinstance(cookies, dict):
            return dict(cookies)
        return {}

    def _headers(self) -> dict:
        return {
            "User-Agent": get_user_agent(),
            "Origin": "https://message.bilibili.com",
            "Referer": "https://message.bilibili.com/",
        }

    def _request(self, method: str, host: str, path: str, params=None, data=None, wbi=False):
        params = dict(params or {})
        if wbi:
            params = self._sign_wbi(params)
        resp = self.session.request(
            method,
            f"https://{host}{path}",
            params=params,
            data=data,
            cookies=self.cookies,
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        payload = resp.json()
        code = payload.get("code")
        # -509: rate limited — retry once after 3s
        if code == -509:
            time.sleep(3)
            resp = self.session.request(
                method,
                f"https://{host}{path}",
                params=params,
                data=data,
                cookies=self.cookies,
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            payload = resp.json()
            code = payload.get("code")
        if code != 0:
            raise RuntimeError(
                f"Bilibili API error {code}: {payload.get('message')}"
            )
        return payload.get("data") or {}

    def _sign_wbi(self, params: dict) -> dict:
        """Sign request parameters with WBI signature."""
        params = dict(params)
        params["wts"] = int(time.time())
        query = urllib.parse.urlencode(dict(sorted(params.items())))
        key = self._get_wbi_key()
        params["w_rid"] = hashlib.md5((query + key).encode("utf-8")).hexdigest()
        return params

    def _get_wbi_key(self) -> str:
        """Retrieve and cache WBI signing key."""
        now = int(time.time())
        if self._wbi_key and now < self._wbi_key_exp:
            return self._wbi_key
        resp = self.session.get(
            "https://api.bilibili.com/x/web-interface/nav",
            cookies=self.cookies,
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()["data"]["wbi_img"]
        k1 = data["img_url"].rsplit("/", 1)[1].split(".")[0]
        k2 = data["sub_url"].rsplit("/", 1)[1].split(".")[0]
        raw = k1 + k2
        self._wbi_key = "".join(raw[i] for i in _MIXIN_KEY_ENC_TAB[:32])
        self._wbi_key_exp = now + 3600
        return self._wbi_key

    def fetch_new_messages(self) -> list[dict]:
        """Fetch new unread private messages across all pages.

        Uses get_sessions (lists ALL sessions with unread_count) instead of
        new_sessions (only returns sessions newer than begin_ts), so existing
        unread DMs are not missed on startup.

        Paginates while has_more=1. Early-exits pagination if page 1 has zero
        unread sessions (they're sorted by recency; unread DMs are recent).

        Returns:
            List of message dicts with keys: uid, content, ts.
        """
        messages = []
        max_ts = self.begin_ts
        page = 1
        session_types = [1, 2]  # 1=私信(联系人), 2=陌生人消息

        for session_type in session_types:
            page = 1
            while True:
                data = self._request(
                    "GET",
                    "api.vc.bilibili.com",
                    "/session_svr/v1/session_svr/get_sessions",
                    params={
                        "session_type": session_type,
                        "group_fold": 1,
                        "unfold_fold": 1,
                        "page": page,
                        "page_size": 50,
                    },
                )
                sessions = (data or {}).get("session_list") or []
                has_more = int((data or {}).get("has_more", 0))

                page_has_unread = False
                for item in sessions:
                    session_ts = int(item.get("session_ts") or 0)
                    max_ts = max(max_ts, session_ts)
                    if int(item.get("unread_count") or 0) <= 0:
                        continue
                    page_has_unread = True
                    talker_id = item.get("talker_id")
                    end_seq = item.get("max_seqno")
                    msg_data = self._request(
                        "GET",
                        "api.vc.bilibili.com",
                        "/svr_sync/v1/svr_sync/fetch_session_msgs",
                        params={
                            "talker_id": talker_id,
                            "begin_seqno": item.get("ack_seqno"),
                            "size": 50,
                            "session_type": 1,
                        },
                    )
                    for msg in msg_data.get("messages") or []:
                        if str(msg.get("sender_uid")) == str(self.self_uid):
                            continue
                        content = msg.get("content", "")
                        if msg.get("msg_type") == 1:
                            content = json.loads(content).get("content", "")
                        messages.append({
                            "uid": msg.get("sender_uid"),
                            "content": content,
                            "ts": msg.get("timestamp"),
                            "ack_seqno": end_seq,
                            "talker_id": talker_id,
                        })

                # Early exit: if this page had zero unread AND we're on page 1,
                # skip remaining pages (sessions are recency-sorted; older pages
                # are even less likely to have unread DMs)
                if not page_has_unread and page == 1:
                    break

                if not has_more:
                    break
                page += 1
                time.sleep(0.5)  # rate-limit spacing between pages

        if max_ts > self.begin_ts:
            self.begin_ts = max_ts
            self._request(
                "GET",
                "api.vc.bilibili.com",
                "/session_svr/v1/session_svr/ack_sessions",
                params={"begin_ts": self.begin_ts},
            )
        return messages

    def send_text(self, uid, content: str):
        """Send a text private message to a user.

        Args:
            uid: Recipient user ID.
            content: Message text.
        """
        msg_content = json.dumps({"content": content}, ensure_ascii=False)
        return self._request(
            "POST",
            "api.vc.bilibili.com",
            "/web_im/v1/web_im/send_msg",
            params={
                "w_sender_uid": self.self_uid,
                "w_receiver_id": uid,
                "w_dev_id": self.dev_id,
            },
            data={
                "msg[sender_uid]": self.self_uid,
                "msg[receiver_id]": uid,
                "msg[receiver_type]": 1,
                "msg[msg_type]": 1,
                "msg[msg_status]": 0,
                "msg[content]": msg_content,
                "msg[timestamp]": int(time.time()),
                "msg[new_face_version]": 0,
                "msg[dev_id]": self.dev_id,
                "from_firework": 0,
                "build": 0,
                "mobi_app": "web",
                "csrf_token": self.csrf,
                "csrf": self.csrf,
            },
            wbi=True,
        )

    def _ack_session(self, talker_id, ack_seqno):
        """Ack (mark as read) a session after successfully replying."""
        self._request(
            "POST",
            "api.vc.bilibili.com",
            "/session_svr/v1/session_svr/update_ack",
            data={
                "talker_id": talker_id,
                "session_type": 1,
                "ack_seqno": ack_seqno,
                "csrf_token": self.csrf,
                "csrf": self.csrf,
            },
        )
