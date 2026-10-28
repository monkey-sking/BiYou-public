#!/usr/bin/env python3
"""
Bilibili Series (系列) API Service

系列 = 旧版「视频列表」，用于**按内容主题**归类稿件；
合集 = season，用于**按搬运源 UP 主**归类稿件（见 bili_collection_api.py）。

二者可同时存在：一条稿件最多属于 1 个合集 + 1 个系列。

⚠️ 重要实测结论（2026-09-26）：
- 系列的增删**必须一次只传一个 aid**。批量传多个 aids 时 B 站返回 code=0
  但只落一部分甚至零落（实测 55 条只落 34）。故本模块只做单条操作。
- 限流返回 `256000 / 合集编辑过于频繁`，需退避重试。
"""
import time
from typing import Optional

from core.utils.logger import log
from core.services.bili_api import get_bilibili_credentials, create_session

# 系列名 -> series_id（2026-09-26 建立，见 docs/bilibili-合集系列调整方案-20260926.md）
SERIES_IDS = {
    "AI 前沿": 5175245,
    "UE 引擎 / 游戏开发": 5175243,
    "游戏美术与行业": 5175246,
    "健身跟练": 5175242,
    "美食": 5175219,
    "3D 建模 / Blender": 5175249,
    "其他": 5175248,
}

SERIES_KEYWORDS = {
    "AI 前沿": "AI,大模型,人工智能",
    "UE 引擎 / 游戏开发": "UE5,虚幻引擎,游戏开发",
    "游戏美术与行业": "游戏美术,3A,独立游戏",
    "健身跟练": "健身,HIIT,燃脂",
    "美食": "美食,食谱,空气炸锅",
    "3D 建模 / Blender": "Blender,3D建模",
    "其他": "精选",
}


class BiliSeriesAPI:
    """系列 API 封装（只做单条增删，见模块 docstring）"""

    def __init__(self, session=None):
        if session:
            self.session = session
        else:
            cookie_data = get_bilibili_credentials()
            if not cookie_data:
                raise RuntimeError("无法获取 Bilibili 凭证，请确保已登录")
            self.session = create_session(cookie_data)
        self.csrf = self.session.cookies.get("bili_jct", "")
        self._mid = None

    @property
    def mid(self) -> int:
        if self._mid is None:
            try:
                r = self.session.get("https://api.bilibili.com/x/web-interface/nav", timeout=10).json()
                self._mid = int(r["data"]["mid"]) if r.get("code") == 0 else 0
            except Exception as e:
                log.warning(f"获取用户 mid 失败: {e}")
                self._mid = 0
        return self._mid

    def _in_series(self, series_id: int, aid: int, max_pages: int = 3) -> bool:
        """回查系列归属（权威口径）。用于校验 addArchives 的 code 0 是否真的生效。"""
        try:
            for pn in range(1, max_pages + 1):
                r = self.session.get(
                    "https://api.bilibili.com/x/series/archives",
                    params={"mid": self.mid, "series_id": series_id, "only_normal": "true",
                            "sort": "desc", "pn": pn, "ps": 30},
                    headers={"Referer": f"https://space.bilibili.com/{self.mid}/lists"},
                    timeout=15,
                ).json()
                aids = ((r.get("data") or {}).get("aids") or [])
                if aid in aids:
                    return True
                if len(aids) < 30:
                    break
                time.sleep(0.3)
        except Exception as e:
            log.warning(f"校验系列归属异常: {e}")
        return False

    def add_video(self, series_name: str, aid: int, retries: int = 3) -> bool:
        """把单条稿件加入指定系列。系列不存在时会尝试创建。"""
        if not aid:
            return False
        sid = SERIES_IDS.get(series_name)
        if not sid:
            log.warning(f"⚠️ 未知系列名: {series_name}（未加入系列）")
            return False

        for attempt in range(retries):
            try:
                r = self.session.post(
                    "https://api.bilibili.com/x/series/series/addArchives",
                    data={"mid": self.mid, "series_id": sid, "csrf": self.csrf, "aids": [aid]},
                    timeout=20,
                ).json()
            except Exception as e:
                log.warning(f"加入系列异常({series_name}): {e}")
                r = {}

            code = r.get("code")
            if code == 0:
                # 2026-09-27 实测坑：刚投稿的稿件立刻加系列，B站会返回 code 0 但实际没加进去
                # （实证 BV1Sta867EtJ / av117342331737714：日志报「✅ 已加入系列」，
                #   平台侧查 3 个系列全都不含它；手动重打同一 API 立刻成功）。
                # 所以 code 0 不算成功，必须回查系列归属，不在就退避重试。
                if self._in_series(sid, aid):
                    log.info(f"✅ 已加入系列「{series_name}」: av{aid}")
                    return True
                log.warning(f"⚠️ 系列接口返回 0 但未实际入列({series_name})，重试 {attempt + 1}/{retries}")
                time.sleep(3 + attempt * 4)
                continue
            if code == 256000:
                time.sleep(35)
                continue
            # 系列可能已被删除/改名 -> 兜底重建
            log.warning(f"⚠️ 加入系列失败({series_name}): {code} {r.get('message')}，尝试重建系列")
            if self._create_series(series_name, aid):
                return True
            break
        return False

    def _create_series(self, series_name: str, aid: int) -> bool:
        try:
            r = self.session.post(
                "https://api.bilibili.com/x/series/series/createAndAddArchives",
                data={
                    "mid": self.mid,
                    "name": series_name,
                    "keywords": SERIES_KEYWORDS.get(series_name, ""),
                    "description": f"{series_name}·按内容分类",
                    "csrf": self.csrf,
                    "aids": [aid],
                },
                timeout=25,
            ).json()
            if r.get("code") == 0:
                new_id = (r.get("data") or {}).get("series_id")
                if new_id:
                    SERIES_IDS[series_name] = new_id
                    log.info(f"✅ 系列重建成功「{series_name}」-> {new_id}")
                return True
            log.error(f"❌ 系列重建失败({series_name}): {r.get('message')}")
        except Exception as e:
            log.error(f"❌ 系列重建异常({series_name}): {e}")
        return False


def add_video_to_series(series_name: str, aid: int, session=None) -> bool:
    """便捷函数：把稿件加入某内容主题系列。失败只记日志，不阻断上传流程。"""
    try:
        return BiliSeriesAPI(session).add_video(series_name, aid)
    except Exception as e:
        log.warning(f"⚠️ 加入系列整体失败({series_name}): {e}")
        return False
