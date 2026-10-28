#!/usr/bin/env python3
"""
Bilibili 自动评论及置顶模块
"""
import requests
import json
import os
import time
from core.utils.i18n import print_t
from core.publishing.records import get_bilibili_publish_ids
from core.utils.effects import record_effect, find_effect, get_effects, is_enabled

def _match_keywords(text, keywords):
    if not text or not keywords:
        return False
    text_lower = text.lower()
    for kw in keywords:
        if not kw:
            continue
        if str(kw).lower() in text_lower:
            return True
    return False

def _normalize_str_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    v = str(value).strip()
    return [v] if v else []

def _rule_matches(rule, text):
    text_lower = (text or "").lower()
    match_any = _normalize_str_list(rule.get("match_any"))
    match_all = _normalize_str_list(rule.get("match_all"))
    exclude_any = _normalize_str_list(rule.get("exclude_any"))

    if exclude_any:
        for kw in exclude_any:
            if kw.lower() in text_lower:
                return False

    if match_any:
        if not any(kw.lower() in text_lower for kw in match_any):
            return False

    if match_all:
        if not all(kw.lower() in text_lower for kw in match_all):
            return False

    return True

def _has_strict_plugin_term(text):
    """检查是否明确提到了本插件（Quick Event Function Creator），而不是泛泛提及插件"""
    if not text:
        return False
    text_lower = text.lower()
    # 只匹配确切的插件名称，避免把"UE5插件"、"蓝图插件"等泛称算作本插件
    specific_names = ["quick event function creator", "quick event", "function creator 蓝图",
                      "热键弹输入框", "一键创建函数", "蓝图小工具", "函数创建器"]
    return any(name in text_lower for name in specific_names)

def _rule_match_strength(rule, primary_text, secondary_text):
    if _rule_matches(rule, primary_text):
        name = str(rule.get("name", "")).lower()
        if name == "plugin" and not _has_strict_plugin_term(primary_text):
            return "weak"
        return "strong"
    if secondary_text and _rule_matches(rule, secondary_text):
        return "weak"
    return "none"


def _pick_hook(rule):
    """文案池：hook 支持字符串或列表,列表时随机选一个变体(2026-09-05)。
    兼容旧配置(单个字符串),新配置可给 hook 传 YAML 列表实现轮换。"""
    hook = rule.get("hook")
    if isinstance(hook, list) and hook:
        import random
        return random.choice(hook)
    return hook

def _build_comment_plan(config, metadata):
    # Rules first (higher priority than resource links)
    rules = config.get("comment_rules") or []
    if not rules:
        # Try resource comment before falling to legacy system
        resource_comment = _build_resource_comment(metadata)
        if resource_comment:
            return resource_comment, []
        return None, None

    title = metadata.get("title", "")
    description = metadata.get("description", "")
    category = metadata.get("category", "")
    tags = metadata.get("tags", [])
    tag_text = " ".join([str(t) for t in tags]) if isinstance(tags, list) else str(tags)
    primary_text = " ".join([title, description]).strip()
    secondary_text = " ".join([category, tag_text]).strip()

    matched = []
    for rule in rules:
        strength = _rule_match_strength(rule, primary_text, secondary_text)
        if strength == "strong":
            matched.append(rule)

    if not matched:
        # Try resource comment before falling to generic
        resource_comment = _build_resource_comment(metadata)
        if resource_comment:
            return resource_comment, []
        return "", []

    def _priority(r):
        try:
            return int(r.get("priority", 100))
        except Exception:
            return 100

    matched.sort(key=_priority)

    main_rule = None
    for r in matched:
        if str(r.get("role", "primary")).lower() == "primary":
            main_rule = r
            break
    if not main_rule:
        for r in matched:
            if str(r.get("role", "")).lower() == "fallback_primary":
                main_rule = r
                break
    if not main_rule:
        main_rule = matched[0]

    # A/B 测试（2026-08-02）：opencode vs ai-memory-hub 同时命中且都作为
    # 默认 primary 时，随机二选一作为置顶主评论，记录日志供效果对比。
    _ab_names = [str(r.get("name", "")).lower() for r in matched]
    if "opencode" in _ab_names and "ai-memory-hub" in _ab_names:
        import random
        _candidates = [r for r in matched if str(r.get("name", "")).lower() in ("opencode", "ai-memory-hub")]
        _ab_chosen = random.choice(_candidates)
        # 仅当 main_rule 也是这两个之一时替换（避免抢占 ai-daily/fitness 等真实 primary）
        if str(main_rule.get("name", "")).lower() in ("opencode", "ai-memory-hub"):
            main_rule = _ab_chosen
        try:
            import datetime
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "logs", "comment_ab_test.log"), "a") as f:
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{ts} | 命中={','.join(_ab_names)} | 选择={main_rule.get('name','?')} | 标题={str(metadata.get('title',''))[:60]}\n")
        except Exception:
            pass

    main_message = (_pick_hook(main_rule) or "").strip()
    if not main_message:
        return "", []

    sub_messages = []
    def add_msg(msg):
        msg = (msg or "").strip()
        if msg and msg not in sub_messages:
            sub_messages.append(msg)

    # 三原则（2026-08-02 用户认可）：
    # 1. 一个视频 = 1 置顶 + 最多 1 条推广子回复
    # 2. 子回复只来自 role=reply 的规则（primary 已用于置顶，不重复）
    # 3. 推广之间互斥：只取优先级最高的那 1 条 reply 规则
    # 注意：main_rule 的 link 不自动加为子回复——置顶文案通常已含链接
    # （hook 内嵌），若主规则是唯一匹配且需要 link 兜底，见下。
    reply_rules = []
    for r in matched:
        if r is main_rule:
            continue
        role = str(r.get("role", "reply")).lower()
        if role == "reply":
            reply_rules.append(r)
    if reply_rules:
        # reply_rules 已按 priority 排序（matched 已排序，这里保持顺序）
        # 三原则要求子回复最多 1 条消息。reply 规则的 hook 文案通常已内嵌
        # 链接（opencode referral / ai-memory-hub github），所以只取 hook，
        # 不再单独追加 link，避免 2 条子回复。
        top = reply_rules[0]
        add_msg(_pick_hook(top))
        if not sub_messages:
            add_msg(top.get("link"))
    else:
        # 没有 reply 规则时，主规则自己的 link 作为唯一子回复兜底
        add_msg(main_rule.get("link"))

    return main_message, sub_messages


def _build_resource_comment(metadata):
    """Build auto-comment for video resources.

    Only posts for tech/dev channels when there are >= 2 real downloadable
    resources (project files, asset packs, Discord, etc.).
    Fitness, cooking, and lifestyle videos should not get resource comments.
    Returns empty string if insufficient resources or wrong channel.
    """
    # Only post resource comments for tech/game/dev channels
    category = str((metadata or {}).get("category") or "").lower()
    title = str((metadata or {}).get("title") or "").lower()
    channel = str((metadata or {}).get("publisher") or "").lower()
    
    non_resource_channels = [
        "fitness", "workout", "cooking", "food", "lifestyle", "dance"
    ]
    if any(kw in channel for kw in non_resource_channels):
        return ""
    
    links = (metadata or {}).get("resource_links") or []
    required = [
        item for item in links
        if isinstance(item, dict) and item.get("classification") == "video_required"
    ]
    # Require at least 2 video_required links
    if len(required) < 2:
        return ""
    title_short = title[:40] if title else "本视频"
    return f"📌 本视频涉及的资源/工具链接已整理，如需获取请私信发送【资源 {title_short}】"


def _default_fitness_comment():
    return (
        "感谢大家支持我的健身翻译频道！💪\n"
        "如果你喜欢此类视频，请三连支持，后续会持续更新全球顶尖健身博主的训练计划。\n"
        "有什么想看的博主也可以在评论区留言哦！✨"
    )


def _build_legacy_comment_plan(config, metadata):
    category = metadata.get("category", "")
    keywords = config.get("auto_comment_keywords", ["健身", "运动", "训练", "Fitness", "Workout"])
    should_comment = any(kw.lower() in category.lower() for kw in keywords)

    hook = config.get('comment_hook', '').strip()
    link = config.get('comment_link', '').strip()
    plugin_hook = config.get('plugin_comment_hook', '').strip()
    plugin_link = config.get('plugin_comment_link', '').strip()
    plugin_keywords = config.get('plugin_comment_keywords', [])
    if isinstance(plugin_keywords, str):
        plugin_keywords = [plugin_keywords]

    title = metadata.get("title", "")
    description = metadata.get("description", "")
    tags = metadata.get("tags", [])
    if isinstance(tags, list):
        tag_text = " ".join([str(t) for t in tags])
    else:
        tag_text = str(tags)
    haystack = " ".join([title, description, category, tag_text]).strip()
    should_add_plugin = _match_keywords(haystack, plugin_keywords)

    if not should_comment and not should_add_plugin:
        return "", []

    if should_comment:
        main_message = hook or _default_fitness_comment()
    else:
        if not plugin_hook:
            return "", []
        main_message = plugin_hook

    sub_messages = []
    if should_comment and link:
        sub_messages.append(link)
    if should_comment and should_add_plugin:
        if plugin_hook:
            sub_messages.append(plugin_hook)
        if plugin_link:
            sub_messages.append(plugin_link)
    if (not should_comment) and should_add_plugin and plugin_link:
        sub_messages.append(plugin_link)

    return main_message, sub_messages

def _has_existing_up_comment(aid, up_mid, cookies, headers):
    """检查视频是否已有UP主的评论（避免重复评论）
    返回: True=已有评论, False=没有, None=检查失败（不确定，跳过发评论）"""
    try:
        # 使用非 WBI 端点 (wbi/main 需要签名，改用 reply/main)
        resp = requests.get(
            "https://api.bilibili.com/x/v2/reply/main",
            params={"type": 1, "oid": aid, "mode": 3, "ps": 5},
            cookies=cookies,
            headers=headers,
            timeout=10
        )
        if resp.status_code != 200:
            return None  # 网络异常 → 不确定，跳过
        data = resp.json()
        if data.get('code') != 0:
            return None  # API 错误 → 不确定，跳过
        
        # 检查置顶评论
        top = data.get('data', {}).get('top')
        if top and str(top.get('mid')) == str(up_mid):
            return True
        
        # 检查普通评论列表
        replies = data.get('data', {}).get('replies') or []
        for r in replies:
            if str(r.get('mid')) == str(up_mid):
                return True
    except Exception:
        return None  # 异常 → 不确定，跳过
    return False


def _default_generic_comment(metadata=None):
    """兜底通用评论：带上视频标题,每条不同(2026-09-05 升级变体池+互动钩子)"""
    title = ""
    if metadata and isinstance(metadata, dict):
        t = metadata.get("title", "").strip()
        if t and len(t) > 3:
            title = t[:60]
    import random
    if title:
        chunks = random.choice([
            f"感谢观看《{title}》！觉得有帮助的话欢迎点赞投币收藏～ 看完有什么疑问或想法，评论区聊聊～ 💬",
            f"这期《{title}》大家看完感觉如何？有用/有踩坑的欢迎评论区分享～ 顺手点个赞支持下～",
            f"感谢观看《{title}》！如果觉得有帮助，欢迎点赞投币收藏，也欢迎在评论区交流～ 💬",
        ])
        return chunks
    return (
        "感谢观看！如果觉得有帮助，欢迎点赞投币收藏，也欢迎在评论区交流～ 💬"
    )


def post_comment_flow(dry_run=False, metadata_path=None, cookies=None):
    """
    自动对刚上传的视频发表评论并置顶
    """
    if dry_run:
        print_t('comm_dry_run')
        return True

    if not metadata_path or not os.path.exists(metadata_path):
        print_t('comm_fail_meta')
        return False

    try:
        from core.utils.config import load_config
        config = load_config()
        
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        
        bvid, aid = get_bilibili_publish_ids(metadata)

        if not bvid or not aid:
            print_t('comment_no_bvid')
            return False

        # 优先走可扩展的规则系统（comment_rules）
        main_message, sub_messages = _build_comment_plan(config, metadata)
        if main_message is not None:
            if not main_message:
                category = metadata.get("category", "")
                # Bug 3 fix: 没匹配到规则时也发一条通用评论，而不是跳过
                print_t('comm_fallback_norule', category)
                main_message = _default_generic_comment(metadata)
                sub_messages = []
        else:
            main_message, sub_messages = _build_legacy_comment_plan(config, metadata)
            if not main_message:
                category = metadata.get("category", "")
                # Bug 3 fix: 旧系统也没匹配到，发通用评论兜底
                print_t('comm_fallback_legacy', category)
                main_message = _default_generic_comment(metadata)
                sub_messages = []

        if not cookies:
            print_t('comment_no_cookies')
            return False

        # 提取 cookies、csrf 和 UP主 mid（DedeUserID）
        req_cookies = {}
        csrf = ""
        up_mid = ""

        cookie_list = []
        if isinstance(cookies, dict) and 'cookie_info' in cookies:
            cookie_list = cookies['cookie_info'].get('cookies', [])
        elif isinstance(cookies, list):
            cookie_list = cookies
            
        for c in cookie_list:
            name = c.get('name')
            value = c.get('value')
            req_cookies[name] = value
            if name == 'bili_jct':
                csrf = value
            if name == 'DedeUserID':
                up_mid = value

        if not csrf:
            print_t('comment_no_csrf')
            return False

        from core.utils.cookie import get_user_agent
        referer_url = f'https://www.bilibili.com/video/{bvid}' if bvid else f'https://www.bilibili.com/video/av{aid}'
        headers = {
            'User-Agent': get_user_agent(),
            'Referer': referer_url
        }

        # 检查是否已有UP主评论/置顶，避免重复评论
        existing = _has_existing_up_comment(aid, up_mid, req_cookies, headers)
        if existing is True:
            print_t('comm_exists', f"aid={aid}")
            return True
        if existing is None:
            # API 检查失败，不确定是否已有评论 → 安全起见跳过本次评论
            print_t('comm_skip_check_failed', f"aid={aid}")
            return True

        # Effect sandwich: 断点续跑 + intent 记录（崩溃恢复用）
        try:
            effects_on = is_enabled()
        except Exception:
            effects_on = False

        # 1. 发表主评论
        print_t('comm_start', aid)

        effect_id_main = None
        rpid = None
        session = requests.Session()
        if effects_on:
            settled_main = find_effect(metadata, "bili_comment_main", status="settled")
            if settled_main and (settled_main.get("result") or {}).get("rpid"):
                # 上次已发主评论并结算 → 复用 rpid，跳过重复发帖
                rpid = settled_main["result"]["rpid"]
                print_t('comm_resume_main', rpid)
            elif settled_main:
                # 已结算但无 rpid（例如之前的恢复路径）→ 无法置顶/子回复
                print_t('comm_resume_no_rpid')
                return True
            else:
                pending_main = find_effect(metadata, "bili_comment_main", status="pending")
                if pending_main:
                    # 未知窗口：上次可能已发出（existing 检查已确认没有 → 继续发，复用原 intent）
                    print_t('comm_pending_main')
                    effect_id_main = pending_main.get("id")
                else:
                    effect_id_main = record_effect(
                        metadata, "bili_comment_main", "pending",
                        payload={"aid": aid, "message": main_message})

        # API: https://api.bilibili.com/x/v2/reply/add
        # type 1 代表视频
        if rpid is None:
            data = {
                'oid': aid,
                'type': 1,
                'message': main_message,
                'plat': 1,
                'csrf': csrf
            }

            resp = session.post(
                "https://api.bilibili.com/x/v2/reply/add",
                data=data,
                cookies=req_cookies,
                headers=headers,
                timeout=10
            )
            
            res_json = resp.json()
            if res_json.get('code') != 0:
                if effect_id_main and effects_on:
                    try:
                        record_effect(metadata, "bili_comment_main", "failed",
                                      result={"error": res_json.get('message', 'Unknown')},
                                      effect_id=effect_id_main)
                    except Exception:
                        pass
                print_t('comment_hook_fail', res_json.get('message', 'Unknown'))
                return False
                
            rpid = res_json.get('data', {}).get('rpid')
            if effect_id_main and effects_on:
                try:
                    record_effect(metadata, "bili_comment_main", "settled",
                                  result={"rpid": rpid}, effect_id=effect_id_main)
                except Exception as _effect_err:
                    print_t('comm_effect_fail', _effect_err)
            print_t('comment_ok_main', rpid)
        
        # 2. 置顶评论
        # B站后端同步可能有延迟，等待并重试
        print_t('comm_top_start')

        if effects_on and find_effect(metadata, "bili_comment_pin", status="settled"):
            pinned = True
            print("   (置顶已在之前完成，跳过)")
        else:
            effect_id_pin = None
            if effects_on:
                effect_id_pin = record_effect(
                    metadata, "bili_comment_pin", "pending",
                    payload={"aid": aid, "rpid": rpid})
            pinned = False
            for i in range(3): # 重试3次
                time.sleep(3 + i*2) 
                top_data = {
                    'oid': aid,
                    'type': 1,
                    'rpid': rpid,
                    'action': 1,
                    'csrf': csrf
                }
                top_resp = session.post(
                    "https://api.bilibili.com/x/v2/reply/top",
                    data=top_data,
                    cookies=req_cookies,
                    headers=headers,
                    timeout=10
                )
                top_json = top_resp.json()
                if top_json.get('code') == 0:
                    print_t('comm_ok_top')
                    pinned = True
                    if effect_id_pin and effects_on:
                        try:
                            record_effect(metadata, "bili_comment_pin", "settled",
                                          result={}, effect_id=effect_id_pin)
                        except Exception:
                            pass
                    break
                else:
                    msg = top_json.get('message', 'Unknown')
                    if i < 2:
                        print(f"   (Attempt {i+1}) {msg}, retrying...")
                    else:
                        print_t('comm_fail_top', f"{msg} (Code: {top_json.get('code')})")
                        if effect_id_pin and effects_on:
                            try:
                                record_effect(metadata, "bili_comment_pin", "failed",
                                              result={"error": msg}, effect_id=effect_id_pin)
                            except Exception:
                                pass

        # 3. 发表子评论 (链接) -> 方便用户复制
        if sub_messages:
            # 已结算/挂起的子评论不重发（避免重复推广回复）
            settled_sub_msgs = set()
            for msg in sub_messages:
                print_t('comm_sub_start')

                effect_id_sub = None
                if effects_on:
                    for e in get_effects(metadata):
                        if (e.get("kind") == "bili_comment_sub"
                                and e.get("status") in ("settled", "pending")
                                and e.get("payload", {}).get("message") == msg):
                            settled_sub_msgs.add(msg)
                            break
                    if msg in settled_sub_msgs:
                        print_t('comm_skip_sub_resumed')
                        continue
                    effect_id_sub = record_effect(
                        metadata, "bili_comment_sub", "pending",
                        payload={"aid": aid, "message": msg})
                
                for i in range(3): # 重试3次
                    time.sleep(2 + i)
                    sub_data = {
                        'oid': aid,
                        'type': 1,
                        'root': rpid,
                        'parent': rpid,
                        'message': msg,
                        'plat': 1,
                        'csrf': csrf
                    }
                    sub_resp = session.post(
                        "https://api.bilibili.com/x/v2/reply/add",
                        data=sub_data,
                        cookies=req_cookies,
                        headers=headers,
                        timeout=10
                    )
                    sub_json = sub_resp.json()
                    if sub_json.get('code') == 0:
                        print_t('comm_ok_sub')
                        if effect_id_sub and effects_on:
                            try:
                                record_effect(metadata, "bili_comment_sub", "settled",
                                              result={}, effect_id=effect_id_sub)
                            except Exception:
                                pass
                        break
                    else:
                        err = sub_json.get('message', 'Unknown')
                        if i < 2:
                            print(f"   (Attempt {i+1}) {err}, retrying...")
                        else:
                            print_t('comm_fail_sub', f"{err} (Code: {sub_json.get('code')})")
                            if effect_id_sub and effects_on:
                                try:
                                    record_effect(metadata, "bili_comment_sub", "failed",
                                                  result={"error": err}, effect_id=effect_id_sub)
                                except Exception:
                                    pass
            
        return True

    except Exception as e:
        print_t('comm_err', e)
        return False
