#!/usr/bin/env python3
"""
Effect Sandwich helper (design borrowed from AgentHarness v3's effect-sandwich
pattern: https://github.com/earendil-works/pi/blob/main/packages/agent/docs/harness-v3.md)

外部副作用（B站 submit 发布、发评论/置顶/子回复）被两笔持久化记录包裹：

    intent      — 副作用开始前写入: "我即将做 X"
    settlement  — 副作用完成后写入: "X 已完成，结果是 R"

两次记录之间的进程崩溃 = 未知窗口：副作用可能发生、也可能没发生。
pending intent 会告诉下游"这里不能盲目重跑"，必须先核对外部状态——
这正是历史上一再出现重复视频 / 重复评论的根因。

记录存放在视频自己的 history JSON 的 meta["effects"] 列表里（与
save_history_entry 同一文件，dedup 检查也在读它）。

每条记录:
    {
      "id": "eff_<uuid>",
      "kind": "bili_upload" | "bili_comment_main" | "bili_comment_pin" | "bili_comment_sub",
      "status": "pending" | "settled" | "failed",
      "created_at": ISO8601,
      "settled_at": ISO8601 | null,
      "payload": {...},
      "result": {...}
    }
"""
import json
import os
import re
import uuid
from datetime import datetime, timezone

EFFECT_KINDS = ("bili_upload", "bili_comment_main", "bili_comment_pin", "bili_comment_sub")


def is_enabled(config=None):
    """从配置读取开关 (默认开)。配置: publishing.effects_sandwich."""
    try:
        if config is None:
            from core.utils.config import load_config
            config = load_config()
        return bool((config.get("publishing") or {}).get("effects_sandwich", True))
    except Exception:
        return True


def _history_path(meta, config=None):
    """与 main.save_history_entry 相同的文件定位逻辑。"""
    video_path = meta.get("video_path")
    title = meta.get("title")
    if not video_path and not title:
        return None
    if video_path:
        base_name = os.path.splitext(os.path.basename(video_path))[0]
    else:
        base_name = re.sub(r'[\\/*?:"<>|]', "", title).strip()
    if not base_name:
        return None
    from core.utils.config import PROJECT_ROOT
    history_dir = os.path.join((config or {}).get("project_root", PROJECT_ROOT), "history")
    history_dir = os.path.abspath(history_dir)
    return os.path.join(history_dir, f"{base_name}.json")


def _load_history(path):
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_history(path, data):
    if not path:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _effects_list(data):
    effects = data.get("effects")
    if not isinstance(effects, list):
        return []
    return [e for e in effects if isinstance(e, dict)]


def record_effect(meta, kind, status, payload=None, result=None, effect_id=None, config=None):
    """
    写入一条 effect 记录 (intent=首次调用, settlement=后续用同一 effect_id 更新)。
    - 同步更新传入的 meta["effects"]（内存态）
    - 持久化到该视频的 history JSON（druable 真源，dedup 检查读这里）
    返回 effect_id；失败返回 None（不阻断主流程）。
    """
    if kind not in EFFECT_KINDS:
        kind = str(kind)
    if not is_enabled(config):
        return effect_id
    if not meta or not isinstance(meta, dict):
        return None

    path = _history_path(meta, config)
    if not path:
        return None

    try:
        if not effect_id:
            effect_id = "eff_" + uuid.uuid4().hex[:12]
        now = _now_iso()

        # 内存态
        effects = meta.get("effects")
        if not isinstance(effects, list):
            effects = []
            meta["effects"] = effects
        found = next((e for e in effects if isinstance(e, dict) and e.get("id") == effect_id), None)
        if found is None:
            found = {"id": effect_id, "kind": kind, "status": "pending",
                     "created_at": now, "settled_at": None, "payload": {}, "result": None}
            effects.append(found)
        found["kind"] = kind
        found["status"] = status
        if payload is not None:
            found["payload"] = payload
        if status in ("settled", "failed") or result is not None:
            found["result"] = result
            found["settled_at"] = now

        # 持久化到 history JSON（以文件里的 effects 为准合并，避免覆盖其他字段）
        data = _load_history(path)
        if not isinstance(data, dict):
            data = {}
        file_effects = _effects_list(data)
        file_found = next((e for e in file_effects if e.get("id") == effect_id), None)
        if file_found is None:
            file_found = {"id": effect_id, "kind": kind, "status": "pending",
                          "created_at": now, "settled_at": None, "payload": {}, "result": None}
            file_effects.append(file_found)
        file_found.update(found)
        data["effects"] = file_effects
        # 同步少量定位字段，保证 dedup 能按 id/title 匹配到该文件
        for key in ("id", "title", "video_path"):
            if meta.get(key) is not None:
                data[key] = meta.get(key)
        _save_history(path, data)

        return effect_id
    except Exception:
        return None


def get_effects(meta, config=None):
    """读取该视频历史里已持久化的 effects 列表（优先文件，回退内存）。"""
    path = _history_path(meta, config)
    data = _load_history(path) if path else {}
    effects = _effects_list(data)
    if not effects and isinstance(meta, dict):
        effects = _effects_list(meta)
    return effects


def find_effect(meta, kind, status=None, config=None):
    """返回该 kind 的最新一条记录（可按 status 过滤），无则 None。"""
    effects = get_effects(meta, config)
    matches = [e for e in effects if e.get("kind") == kind]
    if status is not None:
        matches = [e for e in matches if e.get("status") == status]
    if not matches:
        return None
    matches.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    return matches[0]


def find_pending(meta, config=None):
    """是否有任何 pending 的 effect（未知窗口存在）→ 调用方应阻止盲目重跑。"""
    effects = get_effects(meta, config)
    return any(e.get("status") == "pending" for e in effects)
