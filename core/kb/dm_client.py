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
        if payload.get("code") != 0:
            raise RuntimeError(f"Bilibili API error {payload.get('code')}: {payload.get('message')}")
        return payload.get("data") or {}

    def _sign_wbi(self, params: dict) -> dict:
        params = dict(params)
        params["wts"] = int(time.time())
        query = urllib.parse.urlencode(dict(sorted(params.items())))
        key = self._get_wbi_key()
        params["w_rid"] = hashlib.md5((query + key).encode("utf-8")).hexdigest()
        return params

    def _get_wbi_key(self) -> str:
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
        data = self._request(
            "GET",
            "api.vc.bilibili.com",
            "/session_svr/v1/session_svr/new_sessions",
            params={"begin_ts": self.begin_ts},
        )
        sessions = data.get("session_list") or []
        messages = []
        max_ts = self.begin_ts
        for item in sessions:
            session_ts = int(item.get("session_ts") or 0)
            max_ts = max(max_ts, session_ts)
            if int(item.get("unread_count") or 0) <= 0:
                continue
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
                messages.append({"uid": msg.get("sender_uid"), "content": content, "ts": msg.get("timestamp")})
            self._request(
                "POST",
                "api.vc.bilibili.com",
                "/session_svr/v1/session_svr/update_ack",
                data={
                    "talker_id": talker_id,
                    "session_type": 1,
                    "ack_seqno": end_seq,
                    "csrf_token": self.csrf,
                    "csrf": self.csrf,
                },
            )
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

