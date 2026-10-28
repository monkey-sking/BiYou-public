import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from core.modules.commenter import _build_comment_plan, _build_legacy_comment_plan


def _config():
    return {
        "comment_rules": [
            {
                "name": "fitness",
                "priority": 10,
                "role": "primary",
                "match_any": ["健身", "运动", "训练", "Fitness", "Workout"],
                "hook": "FITNESS",
                "link": "FITNESS_LINK",
            },
            {
                "name": "plugin",
                "priority": 50,
                "role": "fallback_primary",
                "match_any": ["插件", "plugin", "Unreal", "UE", "UE5", "蓝图"],
                "hook": "PLUGIN",
                "link": "PLUGIN_LINK",
            },
        ]
    }


class TestCommenter(unittest.TestCase):
    def test_fitness_strong_match(self):
        metadata = {
            "title": "20分钟健身训练",
            "description": "燃脂 HIIT workout",
            "category": "生活-日常",
            "tags": ["健身", "HIIT"],
        }
        main, subs = _build_comment_plan(_config(), metadata)
        self.assertEqual(main, "FITNESS")
        self.assertIn("FITNESS_LINK", subs)

    def test_plugin_strong_match(self):
        metadata = {
            "title": "UE5 插件开发教程",
            "description": "Unreal plugin workflow",
            "category": "知识-科学科普",
            "tags": ["UE5"],
        }
        main, subs = _build_comment_plan(_config(), metadata)
        self.assertEqual(main, "PLUGIN")
        self.assertIn("PLUGIN_LINK", subs)

    def test_weak_match_from_tags_only_skips(self):
        metadata = {
            "title": "NVIDIA DLSS 5 新闻",
            "description": "Unreal Engine 5 graphics update",
            "category": "生活-健身",
            "tags": ["健身", "运动"],
        }
        main, subs = _build_comment_plan(_config(), metadata)
        self.assertEqual(main, "")

    def test_legacy_comment_plan_builds_default_fitness_comment(self):
        metadata = {
            "title": "20分钟健身训练",
            "description": "燃脂 HIIT workout",
            "category": "生活-健身",
            "tags": ["健身", "HIIT"],
        }
        main, subs = _build_legacy_comment_plan(
            {"auto_comment_keywords": ["健身", "Fitness"]},
            metadata,
        )
        self.assertIn("感谢大家支持我的健身翻译频道", main)
        self.assertEqual(subs, [])

    def test_legacy_comment_plan_skips_non_matching_category_without_plugin_config(self):
        metadata = {
            "title": "UE5 Niagara 教程",
            "description": "Unreal Engine VFX breakdown",
            "category": "游戏-单机游戏",
            "tags": ["UE5", "Niagara"],
        }
        main, subs = _build_legacy_comment_plan(
            {"auto_comment_keywords": ["健身", "Fitness"]},
            metadata,
        )
        self.assertEqual(main, "")
        self.assertEqual(subs, [])

    @patch("core.modules.commenter.time.sleep", return_value=None)
    @patch("core.utils.config.load_config")
    @patch("core.modules.commenter.requests.Session")
    def test_post_comment_flow_uses_legacy_default_comment_when_only_keywords_configured(
        self,
        session_cls,
        load_config,
        _sleep,
    ):
        metadata = {
            "title": "20分钟健身训练",
            "description": "燃脂 HIIT workout",
            "category": "生活-健身",
            "tags": ["健身", "HIIT"],
            "publish_records": {
                "bilibili": {
                    "status": "published",
                    "platform_content_id": "BV1xx411c7mD",
                    "platform_url": "https://www.bilibili.com/video/BV1xx411c7mD",
                    "aid": 123456,
                }
            },
        }
        cookies = {
            "cookie_info": {
                "cookies": [
                    {"name": "SESSDATA", "value": "sess"},
                    {"name": "bili_jct", "value": "csrf-token"},
                ]
            }
        }
        load_config.return_value = {"auto_comment_keywords": ["健身", "Fitness"]}

        add_response = Mock()
        add_response.json.return_value = {"code": 0, "data": {"rpid": 42}}
        top_response = Mock()
        top_response.json.return_value = {"code": 0}
        session = Mock()
        session.post.side_effect = [add_response, top_response]
        session_cls.return_value = session

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(metadata, fh, ensure_ascii=False)
            metadata_path = fh.name

        try:
            from core.modules.commenter import post_comment_flow

            self.assertTrue(
                post_comment_flow(
                    dry_run=False,
                    metadata_path=metadata_path,
                    cookies=cookies,
                )
            )
        finally:
            os.unlink(metadata_path)

        first_call = session.post.call_args_list[0]
        self.assertEqual(
            first_call.args[0],
            "https://api.bilibili.com/x/v2/reply/add",
        )
        self.assertIn("感谢大家支持我的健身翻译频道", first_call.kwargs["data"]["message"])


if __name__ == "__main__":
    unittest.main()
