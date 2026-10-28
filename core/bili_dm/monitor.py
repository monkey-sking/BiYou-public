"""Bilibili private message monitor with auto-reply for resource links.

Polls B站's private message inbox every 5 minutes.
When a new message contains resource-related keywords, searches the local
knowledge base for matching video links and auto-replies.
"""

import json
import os
import re
import subprocess
import sys
import time

from core.bili_dm.client import BiliDMClient
from core.kb.database import search_links
from core.services.bili_api import get_bilibili_credentials
from core.utils.config import PROJECT_ROOT
from core.utils.logger import log

# 资源总表飞书文档配置真源（可变参数不硬编码）
RESOURCE_DOC_CONFIG = os.path.join(PROJECT_ROOT, "config", "resource_doc.json")  # 资源总表（飞书多维表格）


def get_resource_doc_url() -> str:
    """返回资源总表飞书文档链接。

    优先级：环境变量 BILI_RESOURCE_DOC_URL > config/resource_doc.json > 空串。
    未配置时调用方回退到「直接发链接」的老行为。
    """
    url = (os.environ.get("BILI_RESOURCE_DOC_URL") or "").strip()
    if url:
        return url
    try:
        with open(RESOURCE_DOC_CONFIG, encoding="utf-8") as f:
            return (json.load(f).get("url") or "").strip()
    except Exception:
        return ""

# Keywords that trigger resource link search + auto-reply
#
# 2026-09-22 根因修复（用户反馈「B站有些是乱回复的」）：
# 旧词表塞进了大量**通用词**（文件/分享/在哪/大佬/有没有/合集/工程…），
# 导致正常寒暄也被判成"求资源"，然后走兜底模板回一句
# 「抱歉，暂时没有找到这条视频的资源链接。请尝试回复具体视频标题关键词…」。
# 实测误触发：「大佬你好」→query='你好'、「谢谢大佬」→query='谢谢'、
# 「这个视频做得不错」→query='做得不错'，三条全部会发出牛头不对马嘴的回复。
#
# 新规则：**分两级**
#   ① 强信号（STRONG）—— 明确的求资源意图，单独命中即可触发
#   ② 弱信号（WEAK）—— 只是"可能相关"的词，**必须与数据类名词同现**才触发
#   ③ 纯寒暄/致谢/评价（GREETING）—— 一律不触发，不回复
RESOURCE_KEYWORDS_STRONG_RE = re.compile(
    r"(资源|素材|源文件|工程文件|项目文件|project\s*files?|source\s*code"
    r"|求个|求分享|发我|给我|求发|整一份|来一份|打包|白嫖|归档"
    r"|hip文件|blend文件|fbx文件"
    r"|下载链接|网盘|链接发|发下链接|发个链接)",
    re.IGNORECASE,
)

# 弱信号：单独出现说明不了什么（「在哪」「有没有」「文件」日常对话也用）
RESOURCE_KEYWORDS_WEAK_RE = re.compile(
    r"(在哪|哪里|获取|有没有|有吗|合集|出处|分享|链接|下载"
    r"|hip|blend|fbx|高清|清晰|原版|无压缩|无水印|完整版"
    r"|三连|视频没|有视频|哪个视频|这条视频|这个视频)",
    re.IGNORECASE,
)

# 数据类名词：弱信号必须搭上这类词才算求资源
RESOURCE_NOUN_RE = re.compile(
    r"(视频|教程|模型|文件|工程|素材|资源|链接|hip|blend|fbx|工程文件|项目|案例|合集|全集|整合)",
    re.IGNORECASE,
)

# 纯寒暄/致谢/寒暄式评价 —— 命中即不触发（避免"大佬你好"被当成求资源）
GREETING_ONLY_RE = re.compile(
    r"^\s*(大佬|博主|up主|up|老师|哥|姐)?\s*"
    r"(你好|您好|哈喽|hello|hi|在吗|在么|早上好|晚上好|下午好|谢谢|感谢|多谢|辛苦了|"
    r"关注了|已关注|关注你了|做得不错|做得很好|支持|加油|太好了|厉害了|牛|赞|已三连|已投币|催更)\s*"
    r"(大佬|博主|up主|up|老师|哥|姐)?\s*[!！~～。.,，]*\s*$",
    re.IGNORECASE,
)

# 评价式措辞：出现在句末的「…做得不错 / …真好看 / …厉害了」等，属纯评价，不是求资源
# 2026-09-22 补：修「这个视频做得不错」误触发（「视频」是数据名词，会撞上弱信号规则）
PRAISE_TAIL_RE = re.compile(
    r"(做得(不错|很好|真好|太棒|挺棒)|(真|太|好)厉害|很好看|真好看|太好看了|"
    r"很不错|挺好的|很棒|太棒了|牛逼|牛啊|赞一个|爱了|收藏了|学到了)\s*[!！~～。.,，]*\s*$",
    re.IGNORECASE,
)


def _jev_allow(incoming: str, draft: str, context: str, timeout: int = 45):
    """过 Jev 放行门。返回 (allowed: bool, info: str)。

    2026-09-22 用户要求：「用 jev 审核，大于 0.9 的就自己发」+「审核 AI 味」。
    真源脚本 ~/.hermes/scripts/jev_reply_gate.py
    退出码 0=allow / 3=review / 4=error（fail-closed）。

    Fail-closed：脚本不存在、超时、任何异常 → 返回 (False, reason)，**不发送**。
    """
    script = os.path.expanduser("~/.hermes/scripts/jev_reply_gate.py")
    if not os.path.exists(script):
        return False, "放行门脚本缺失（fail-closed）"
    try:
        proc = subprocess.run(
            [sys.executable, script,
             "--incoming", incoming[:2000],
             "--draft", draft[:2000],
             "--context", context[:2000],
             "--channel", "bili_dm", "--json"],
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode == 0:
            info = "allow"
            try:
                d = json.loads(proc.stdout)
                info = f"allow conf={d.get('confidence')} ai_taste={d.get('ai_taste')}"
            except Exception:
                pass
            return True, info
        # 3=review / 4=error / 其他 → 不放行
        info = f"exit={proc.returncode}"
        try:
            d = json.loads(proc.stdout)
            info = (f"{d.get('decision')} action={d.get('action')} "
                    f"conf={d.get('confidence')} ai_taste={d.get('ai_taste')} "
                    f"reason={d.get('reason')}")
        except Exception:
            info = f"exit={proc.returncode} {(proc.stdout or proc.stderr or '')[:200]}"
        return False, info
    except Exception as e:
        return False, f"放行门调用异常（fail-closed）{type(e).__name__}: {e}"


def _sent_ledger_path() -> str:
    """已发送回复的本地账本（不依赖 API 回读，避免写后可见性延迟导致重发）。"""
    d = os.path.expanduser("~/.hermes/state")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "bili_dm_sent.json")


def _ledger_load() -> dict:
    try:
        with open(_sent_ledger_path(), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _ledger_mark(talker_id, reply: str, ts: float) -> None:
    """记录「已对该 talker 发过这条内容」。"""
    try:
        d = _ledger_load()
        d[str(talker_id)] = {"reply": reply[:200], "ts": int(ts)}
        # 只保留最近 500 条，防膨胀
        if len(d) > 500:
            for k in sorted(d, key=lambda x: d[x].get("ts", 0))[:-500]:
                d.pop(k, None)
        with open(_sent_ledger_path(), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log.warning(f"⚠️ 写发送账本失败（不影响本次发送）: {e}")


def _ledger_recently_sent(talker_id, reply: str, within_seconds: int = 21600) -> bool:
    """6 小时内是否已对同一 talker 发过同一条内容（幂等保护）。"""
    rec = _ledger_load().get(str(talker_id))
    if not rec:
        return False
    if int(time.time()) - int(rec.get("ts") or 0) > within_seconds:
        return False
    prev = (rec.get("reply") or "").strip()
    return bool(prev) and prev == (reply or "").strip()[:200]


def _is_greeting_only(content: str) -> bool:
    """纯寒暄/致谢/寒暄式评价 —— 不该触发资源回复。"""
    t = (content or "").strip()
    if GREETING_ONLY_RE.match(t):
        return True
    # 纯评价收尾且无任何强求取信号 → 不是在要东西
    if PRAISE_TAIL_RE.search(t) and not RESOURCE_KEYWORDS_STRONG_RE.search(t):
        return True
    return False


def has_resource_intent(content: str) -> bool:
    """判定是否真的在求资源链接（两级 + 寒暄排除）。"""
    t = (content or "").strip()
    if not t or _is_greeting_only(t):
        return False
    if RESOURCE_KEYWORDS_STRONG_RE.search(t):
        return True
    # 弱信号必须与数据类名词同现
    if RESOURCE_KEYWORDS_WEAK_RE.search(t) and RESOURCE_NOUN_RE.search(t):
        return True
    # 2026-09-22 补：悬空疑问句（「集成合集在哪下」的「下」、「有xxx的文件吗」的「吗」）
    # 这类没有强信号词，但「某物 + 疑问求取词」结构本身就是求资源
    if re.search(r"(在哪|哪里|怎么|如何|求|有(没有)?|要|能给|发|下|拿|取)", t) and \
       RESOURCE_NOUN_RE.search(t):
        return True
    return False


# 兼容旧引用（旧代码/旧测试可能直接 import 这个正则）
RESOURCE_KEYWORDS_RE = RESOURCE_KEYWORDS_STRONG_RE


def _extract_query(content: str) -> str:
    """Extract a search query from a user message.

    Returns an empty string if the message does not contain resource keywords.

    2026-09-22: 改用 has_resource_intent()（两级判定 + 寒暄排除），
    不再用旧的通用词正则 —— 后者会把「大佬你好」这类寒暄也判成求资源。
    """
    text = (content or "").strip()
    if not has_resource_intent(text):
        return ""
    # Remove the trigger keywords and punctuation to get the actual query
    text = RESOURCE_KEYWORDS_STRONG_RE.sub(" ", text)
    text = re.sub(r"[【】\[\]（）()：:，,。.!！?？]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_system_or_promo(content: str) -> bool:
    """系统通知 / 营销邀约 —— 这类才允许静默 ack（标已读、不回复）。

    2026-09-19: 区分开「系统/营销」与「真人求助」。真人消息即使没命中资源关键词，
    也不该被静默 ack 掉（那是上次「求分享个 hip」被吞的原因）。
    """
    t = content or ""
    promo = re.compile(
        r"(专属带货邀约|带货时间|带货活动|开播提醒|直播邀约|商务合作|合作邀约"
        r"|国家税务总局|纳税义务|个人所得税|收益转入|贝壳账户"
        r"|您的稿件.*已成功参加|稿件.*已.*参加|审核结果|违规处理|创作激励"
        r"|会员购|积分.*即将|系统通知|官方公告|温馨提示.*活动"
        # 2026-09-22 补：实测 biyou.log 里 review 噪声 TOP1 就是这类到账/券类系统通知
        r"|B币券|大会员返|已到账|有效期至|优惠券|红包.*到账"
        r"|带货|稿件.*(已|未).*(通过|发布|参加|审核)|投稿.*提醒)",
        re.IGNORECASE,
    )
    if promo.search(t):
        return True
    # 纯数字串（如同一串 uid/订单号）没有可回复内容，也不值得占用未读
    if re.fullmatch(r"[\d\s\-]+", t.strip()):
        return True
    return False


def _doc_reply(title: str) -> str:
    """有资源总表时，回复总表链接（不自带外链）。"""
    url = get_resource_doc_url()
    return (
        f"你要的《{title}》资源链接都在这个总表里啦👇\n"
        f"{url}\n\n"
        "打开后在表格里搜视频标题关键词就能定位到～"
    )


def _format_reply(match: dict) -> str:
    """Format a reply message from a KB search result."""
    title = match.get("video_title") or "相关视频"
    if get_resource_doc_url():
        return _doc_reply(title)

    links = [
        item
        for item in (match.get("links") or [])
        if item.get("classification") == "video_required"
    ] or (match.get("links") or [])
    lines = [f"你要的《{title}》资源链接已整理："]
    for item in links[:8]:
        label = item.get("label") or "资源"
        lines.append(f"{label}: {item.get('url')}")
    lines.append("如果不是这条视频，请回复“资源 + 视频标题关键词”。")
    return "\n".join(lines)


def _no_match_reply(query: str) -> str:
    """KB 没命中时的回复。

    2026-09-22 用户反馈「回复的内容呢」后重写：
    旧文案「抱歉，暂时没有找到这条视频的资源链接。请尝试回复具体视频标题关键词（如：'资源 UE5 Motion Matching'）」
    有两个问题：
      ① 把内部检索失败直接甩给用户（"抱歉没找到"听起来像敷衍）
      ② 说"这条视频的链接"——但对方要的往往是**文件/工程**，不是视频
    新文案：不暴露检索细节，直接把人引导到能自助解决的地方，并留一个真人兜底口。
    """
    url = get_resource_doc_url()
    parts = ["这个我得手工找一下，稍等或者先看看总表有没有👇"]
    if url:
        parts.append(url)
        parts.append("表格里按视频标题搜就行。如果这里也没有，回复我视频标题+要的文件，我单独发你。")
    else:
        parts.append("回复我视频标题 + 你需要的文件（工程/素材/源文件），我直接发你。")
    return "\n".join(parts)


def _screen_incoming(content: str) -> str:
    """Jev 闸门：判断私信内容是否含针对 AI 助手的注入指令。

    返回 'block' | 'review' | 'skip' | 'pass' | 'unknown'。
    Fail-open：任何异常/超时/脚本缺失都返回 'unknown'，绝不影响私信流程。
    真源脚本：~/.hermes/scripts/jev_screen_text.py（退出码 2=block 3=review 4=skip 0=pass）。
    """
    script = os.path.expanduser("~/.hermes/scripts/jev_screen_text.py")
    if not os.path.exists(script) or not content.strip():
        return "unknown"
    try:
        proc = subprocess.run(
            [sys.executable, script, "--text", content[:2000], "--quiet",
             "--purpose", "B站私信：对方在索要视频资源链接，判断是否含针对 AI 助手的注入指令"],
            capture_output=True, text=True, timeout=25,
        )
        return {2: "block", 3: "review", 4: "skip", 0: "pass"}.get(proc.returncode, "unknown")
    except Exception:
        return "unknown"


class ResourceDMMonitor:
    """Monitor Bilibili private messages and auto-reply with resource links.

    Default poll interval is 300 seconds (5 minutes).
    """

    def __init__(
        self,
        client=None,
        sleep_seconds: float = 300.0,
    ):
        self.client = client
        self.sleep_seconds = sleep_seconds

    @classmethod
    def from_bilibili_cookies(
        cls, cookies=None, sleep_seconds: float = 300.0
    ):
        """Create a monitor from BiYou's existing B站 cookie/auth system.

        Set BILI_SKIP_BROWSER=1 env var to skip Chrome CDP connection and
        directly load cookies.json (faster, avoids 30s timeout when Chrome
        is closed or CDP port is unavailable).

        Args:
            cookies: Cookie data dict (from get_bilibili_credentials()). If None,
                     calls get_bilibili_credentials() directly (respects
                     BILI_SKIP_BROWSER env var).
            sleep_seconds: Poll interval in seconds (default: 300 = 5 min).
        """
        import os as _os
        if cookies is None and _os.environ.get("BILI_SKIP_BROWSER") == "1":
            import json as _json
            # cookies.json is at BiYou project root (3 levels up from this file)
            cookie_file = _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
                "cookies.json",
            )
            if _os.path.exists(cookie_file):
                with open(cookie_file) as _f:
                    cookies = _json.load(_f)
        if cookies is None:
            cookies = get_bilibili_credentials()
        if not cookies:
            raise RuntimeError("Bilibili cookies are required for DM monitoring")
        return cls(BiliDMClient(cookies), sleep_seconds=sleep_seconds)

    def _get_unread_sessions(self):
        """Get page-1 sessions that need attention.

        2026-09-19: 原来只取 unread_count > 0 —— 但**回复后对方又说话**、或
        **我们这边误点/自动 ack 过**的会话，unread_count 会是 0，导致「对方最后说话、
        我们没回」的会话永久沉默（实测 11 个，含当天 09:18 的真实求助）。
        现改为：取本页所有会话（含 unread=0），交给下游按「最后一条是否我方发的」判定。
        """
        candidates = []
        for session_type in [1]:
            data = self.client._request(
                "GET",
                "api.vc.bilibili.com",
                "/session_svr/v1/session_svr/get_sessions",
                params={
                    "session_type": session_type,
                    "group_fold": 1,
                    "unfold_fold": 1,
                    "page": 1,
                    "page_size": 50,
                },
            )
            sessions = (data or {}).get("session_list") or []
            for item in sessions:
                candidates.append({
                    "talker_id": item.get("talker_id"),
                    "ack_seqno": item.get("ack_seqno"),
                    "max_seqno": item.get("max_seqno"),
                    "unread_count": int(item.get("unread_count") or 0),
                    "session_type": session_type,
                })
        return candidates

    def _reply_to_session(self, talker_id, ack_seqno, session_type=1):
        """Fetch latest user message from a session, reply if resource-related.

        Returns True if a reply was sent, False if no reply needed.
        Raises on failure (caller handles ack only on success).
        """
        import json as _json

        msg_data = self.client._request(
            "GET",
            "api.vc.bilibili.com",
            "/svr_sync/v1/svr_sync/fetch_session_msgs",
            params={
                "talker_id": talker_id,
                "begin_seqno": 0,
                "size": 20,
                "session_type": session_type,
            },
        )
        # Find the most recent user message (not from self)
        user_msg = None
        for msg in reversed(msg_data.get("messages") or []):
            if str(msg.get("sender_uid")) != str(self.client.self_uid):
                user_msg = msg
                break
        if not user_msg:
            return False

        # 2026-09-19: **防重复回复**。原来的判定只看「最后一条非我方消息」，
        # 于是刚回过的会话下一轮又满足条件 → 同一条回复发两次（实测 n=2）。
        # 现在改为：取整段消息按时间排序，只有「对方的消息位于最后一条之后」才回。
        ordered = sorted(
            msg_data.get("messages") or [],
            key=lambda m: int(m.get("timestamp") or 0),
        )
        if ordered:
            last_msg = ordered[-1]
            if str(last_msg.get("sender_uid")) == str(self.client.self_uid):
                return False  # 我方已是最后一条 → 无需回复（幂等，避免重复发送）
            # 同日/近期的重复内容保护：若我方在对方这条之后 10 分钟内已发过同样内容，跳过
            for prev in reversed(ordered[:-1]):
                if str(prev.get("sender_uid")) != str(self.client.self_uid):
                    continue
                same_min = abs(int(prev.get("timestamp") or 0) - int(last_msg.get("timestamp") or 0)) <= 600
                if same_min:
                    return False
                break

        content = user_msg.get("content", "")
        if user_msg.get("msg_type") == 1:
            try:
                content = _json.loads(content).get("content", content)
            except Exception:
                pass

        # 2026-09-22 根因修复：系统/营销消息必须在 review 闸门**之前**判定。
        # 原顺序 reaction: verdict=='review' 直接 log.warning 然后落到 _extract_query，
        # 若没命中资源关键词就 return False **不 ack** → 会话永远保持未读 →
        # 每 5 分钟重新进入本函数，无限刷 "需人工确认" 警告
        # （实测 biyou.log 累计 1927 条，TOP 是「B币券已到账」「带货时间即将截止」「稿件已参加活动」）。
        # 系统/营销既不是注入也不是真人求助，应当静默 ack 掉。
        is_system = _is_system_or_promo(content)

        verdict = _screen_incoming(content)
        if verdict == "block":
            log.warning(
                f"🚫 疑似注入/恶意私信，跳过自动回复 talker={talker_id}: {content[:60]!r}"
            )
            self.client._ack_session(talker_id, ack_seqno)  # ack: 不回复也不反复重试
            return False
        if is_system:
            # 系统通知/营销：静默 ack，不进 review 队列、不反复重试
            self.client._ack_session(talker_id, ack_seqno)
            return False
        if verdict == "review":
            log.warning(f"⚠️ 私信内容需人工确认 talker={talker_id}: {content[:60]!r}")

        query = _extract_query(content)
        if not query:
            # 2026-09-19: 原实现「无关键词 → 静默 ack」会把真实求助（如「求分享个 hip」）
            # 直接标已读、永不回复。改为：只有**系统通知/营销**这类才静默 ack，
            # 其他一律不 ack（保持未读，留给人或后续规则处理）。
            log.info(
                f"ℹ️ 未匹配资源关键词，保持未读待处理 talker={talker_id}: {content[:60]!r}"
            )
            return False

        matches = search_links(query, limit=5)
        if matches:
            reply = _format_reply(matches[0])
        else:
            reply = _no_match_reply(query)

        # 2026-09-22 幂等：本地账本判重。
        # 根因：旧逻辑靠「API 回读消息列表」判重，但**发送成功 ≠ 立即可见**，
        # 在写后可见性延迟窗口内下一轮 poll 仍读到「对方是最后一条」→ 重复发送。
        # 实测 8 个会话在 09-19 16:36/16:37 同一分钟连发两条相同回复。
        if _ledger_recently_sent(talker_id, reply):
            log.info(f"⏭️ 6 小时内已发过相同回复，跳过（幂等）talker={talker_id}")
            self.client._ack_session(talker_id, ack_seqno)
            return False

        # 2026-09-22 用户要求：外发回复统一过 Jev 放行门。
        # conf >= 0.9 且无 AI 味 → 放行自动发送；否则不发送（保留未读，交人处理）。
        # Fail-closed：门不可用（脚本缺失/调用失败）时**不发送**。
        allowed, gate_info = _jev_allow(
            incoming=content,
            draft=reply,
            context=(
                f"B站私信。对方原话已确认为求资源意图。"
                f"回复来自资源知识库，命中={'有' if matches else '无'}。"
                f"命中时回复含官方资源总表链接（属产品设计，非广告）；"
                f"未命中时回复兜底引导文案。"
            ),
        )
        if not allowed:
            log.info(
                f"🔶 回复未过 Jev 放行门，保留未读待人处理 talker={talker_id}: {gate_info}"
            )
            return False

        self.client.send_text(str(user_msg["sender_uid"]), reply)
        # 发送成功立即记本地账本（不依赖 API 回读）
        _ledger_mark(talker_id, reply, time.time())
        # Ack only after successful reply
        self.client._ack_session(talker_id, ack_seqno)
        log.info(f"✅ 已回复并通过放行门 talker={talker_id}: {gate_info}")
        return True

    def poll_once(self) -> int:
        """Poll inbox once and reply to any resource-related messages.

        Uses direct sequential processing instead of fetch_new_messages()
        to avoid timeout/rate-limit issues on busy accounts.

        Returns:
            Number of auto-reply messages sent.
        """
        sent = 0
        sessions = self._get_unread_sessions()
        if not sessions:
            return 0

        for sess in sessions:
            try:
                if self._reply_to_session(
                    sess["talker_id"],
                    sess["ack_seqno"],
                    sess["session_type"],
                ):
                    sent += 1
                time.sleep(0.5)  # gentle spacing between sessions
            except Exception as e:
                log.warning(
                    f"⚠️ Failed to process session talker={sess['talker_id']}: {e}"
                )
                # Don't ack on failure — leave unread for next retry
        return sent

    def run_forever(self):
        """Run the monitor indefinitely, polling every sleep_seconds."""
        log.info(
            f"🔍 Bilibili resource DM monitor started (poll every {self.sleep_seconds:.0f}s)"
        )
        while True:
            try:
                self.poll_once()
            except KeyboardInterrupt:
                log.info("👋 Bilibili resource DM monitor stopped.")
                raise
            except Exception as exc:
                log.warning(f"⚠️ Bilibili resource DM monitor error: {exc}")
            time.sleep(self.sleep_seconds)
