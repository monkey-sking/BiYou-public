import argparse
import json
import os
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main
from core.modules import fetcher


class _FakeCollectionAPI:
    def __init__(self, *args, **kwargs):
        pass

    def get_my_collections(self):
        return [
            {"title": "居家暴汗健身跟练"},
            {"title": "Lucy臀腿塑形"},
        ]


class _FakeGenerativeModel:
    def __init__(self, *args, **kwargs):
        pass

    def generate_content(self, prompt):
        return SimpleNamespace(text="Lucy臀腿塑形")


class TestCollectionSync(unittest.TestCase):
    def test_save_history_entry_preserves_existing_publish_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = os.path.join(tmpdir, "project")
            history_dir = os.path.join(project_root, "history")
            os.makedirs(history_dir, exist_ok=True)
            history_path = os.path.join(history_dir, "sample.json")

            with open(history_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "video_path": "/tmp/sample.mp4",
                        "title": "旧标题",
                        "publish_records": {
                            "douyin": {
                                "status": "published",
                                "platform_content_id": "7627",
                            }
                        },
                        "platform_payloads": {
                            "xiaohongshu": {
                                "title": "旧小红书标题",
                            }
                        },
                    },
                    f,
                    ensure_ascii=False,
                    indent=4,
                )

            main.save_history_entry(
                {
                    "video_path": "/tmp/sample.mp4",
                    "title": "新标题",
                },
                {"project_root": project_root},
            )

            with open(history_path, "r", encoding="utf-8") as f:
                saved = json.load(f)

            self.assertEqual(saved["title"], "新标题")
            self.assertEqual(
                saved["publish_records"]["douyin"]["platform_content_id"],
                "7627",
            )
            self.assertEqual(
                saved["platform_payloads"]["xiaohongshu"]["title"],
                "旧小红书标题",
            )

    def test_save_history_entry_prefers_current_publish_records_over_stale_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = os.path.join(tmpdir, "project")
            history_dir = os.path.join(project_root, "history")
            os.makedirs(history_dir, exist_ok=True)
            history_path = os.path.join(history_dir, "sample.json")

            with open(history_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "video_path": "/tmp/sample.mp4",
                        "title": "旧标题",
                        "publish_records": {
                            "xiaohongshu": {
                                "status": "failed_retryable",
                                "last_error": "old error",
                            }
                        },
                    },
                    f,
                    ensure_ascii=False,
                    indent=4,
                )

            main.save_history_entry(
                {
                    "video_path": "/tmp/sample.mp4",
                    "title": "新标题",
                    "publish_records": {
                        "xiaohongshu": {
                            "status": "failed_manual",
                            "last_error": "new error",
                        }
                    },
                },
                {"project_root": project_root},
            )

            with open(history_path, "r", encoding="utf-8") as f:
                saved = json.load(f)

            self.assertEqual(saved["publish_records"]["xiaohongshu"]["status"], "failed_manual")
            self.assertEqual(saved["publish_records"]["xiaohongshu"]["last_error"], "new error")

    def test_suggest_collection_considers_live_collections_even_when_rule_matches(self):
        google_pkg = types.ModuleType("google")
        genai_mod = types.ModuleType("google.generativeai")
        genai_mod.configure = lambda **kwargs: None
        genai_mod.GenerativeModel = _FakeGenerativeModel
        google_pkg.generativeai = genai_mod

        prompts = {
            "suggest_collection": (
                "Existing Collections:\n{existing_collections}\n"
                "Title: {title}\nDescription: {description}\n"
            )
        }

        with patch.object(fetcher, "CONFIG", {"prompts": prompts}), \
             patch("core.modules.fetcher.classify_video", return_value="居家暴汗健身跟练"), \
             patch("core.services.bili_collection_api.BiliCollectionAPI", _FakeCollectionAPI), \
             patch.dict(sys.modules, {"google": google_pkg, "google.generativeai": genai_mod}):
            suggested = fetcher.suggest_collection("15分钟臀腿燃脂", "更偏向臀腿塑形训练")

        self.assertEqual(suggested, "Lucy臀腿塑形")

    def test_step_fetch_metadata_reprocesses_when_collection_suggestion_is_stale(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, "sample.mp4")
            meta_path = os.path.join(tmpdir, "sample.json")

            with open(video_path, "wb") as f:
                f.write(b"0" * 2048)

            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "title": "旧标题",
                        "description": "旧简介",
                        "video_path": video_path,
                        "suggested_collection": "居家暴汗健身跟练",
                        "mission_id": 123,
                    },
                    f,
                    ensure_ascii=False,
                    indent=4,
                )

            args = argparse.Namespace(skip_fetch=False, yes=True, dry_run=False)
            video_info = {
                "title": "原始标题",
                "description": "原始简介",
                "video_path": video_path,
            }

            with patch("main.get_latest_video", return_value=video_info), \
                 patch("core.services.bili_activity.should_refresh_existing_mission", return_value=False), \
                 patch("main.should_refresh_existing_collection", return_value=True, create=True), \
                 patch("main.process_metadata", return_value="/tmp/reprocessed.json") as process_metadata:
                result = main.step_fetch_metadata(args)

            self.assertEqual(result, "/tmp/reprocessed.json")
            process_metadata.assert_called_once_with(video_info)

    def test_step_fetch_metadata_restores_local_json_from_history_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = os.path.join(tmpdir, "project")
            history_dir = os.path.join(project_root, "history")
            os.makedirs(history_dir, exist_ok=True)

            video_path = os.path.join(tmpdir, "sample.mp4")
            meta_path = os.path.join(tmpdir, "sample.json")
            history_path = os.path.join(history_dir, "sample.json")

            with open(video_path, "wb") as f:
                f.write(b"0" * 2048)

            with open(history_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "title": "历史标题",
                        "description": "历史简介",
                        "video_path": video_path,
                        "mission_id": 123,
                        "publish_records": {
                            "xiaohongshu": {
                                "status": "failed_retryable",
                            }
                        },
                    },
                    f,
                    ensure_ascii=False,
                    indent=4,
                )

            args = argparse.Namespace(skip_fetch=False, yes=True, dry_run=False)
            video_info = {
                "title": "原始标题",
                "description": "原始简介",
                "video_path": video_path,
            }

            with patch("main.get_latest_video", return_value=video_info), \
                 patch.object(main, "PROJECT_ROOT", project_root), \
                 patch("core.services.bili_activity.should_refresh_existing_mission", return_value=False), \
                 patch("main.should_refresh_existing_collection", return_value=False, create=True), \
                 patch("main.process_metadata") as process_metadata:
                result = main.step_fetch_metadata(args)

            self.assertEqual(result, meta_path)
            self.assertFalse(process_metadata.called)
            with open(meta_path, "r", encoding="utf-8") as f:
                restored = json.load(f)
            self.assertEqual(restored["title"], "历史标题")
            self.assertEqual(restored["publish_records"]["xiaohongshu"]["status"], "failed_retryable")

    def test_step_fetch_metadata_merges_history_publish_records_into_local_resume_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = os.path.join(tmpdir, "project")
            history_dir = os.path.join(project_root, "history")
            os.makedirs(history_dir, exist_ok=True)

            video_path = os.path.join(tmpdir, "sample.mp4")
            meta_path = os.path.join(tmpdir, "sample.json")
            history_path = os.path.join(history_dir, "sample.json")

            with open(video_path, "wb") as f:
                f.write(b"0" * 2048)

            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "title": "本地标题",
                        "description": "本地简介",
                        "video_path": video_path,
                        "mission_id": 123,
                    },
                    f,
                    ensure_ascii=False,
                    indent=4,
                )

            with open(history_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "title": "历史标题",
                        "description": "历史简介",
                        "video_path": video_path,
                        "mission_id": 123,
                        "publish_records": {
                            "douyin": {
                                "status": "published",
                                "platform_content_id": "7627",
                            }
                        },
                    },
                    f,
                    ensure_ascii=False,
                    indent=4,
                )

            args = argparse.Namespace(skip_fetch=False, yes=True, dry_run=False)
            video_info = {
                "title": "原始标题",
                "description": "原始简介",
                "video_path": video_path,
            }

            with patch("main.get_latest_video", return_value=video_info), \
                 patch.object(main, "PROJECT_ROOT", project_root), \
                 patch("core.services.bili_activity.should_refresh_existing_mission", return_value=False), \
                 patch("main.should_refresh_existing_collection", return_value=False, create=True), \
                 patch("main.process_metadata") as process_metadata:
                result = main.step_fetch_metadata(args)

            self.assertEqual(result, meta_path)
            self.assertFalse(process_metadata.called)
            with open(meta_path, "r", encoding="utf-8") as f:
                merged = json.load(f)
            self.assertEqual(merged["title"], "本地标题")
            self.assertEqual(merged["publish_records"]["douyin"]["platform_content_id"], "7627")


if __name__ == "__main__":
    unittest.main()
