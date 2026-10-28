import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import main
from core.publishing.records import get_bilibili_publish_ids


class TestPublishRouting(unittest.TestCase):
    def test_get_automation_platform_config_preserves_frame_name_and_extra_keys(self):
        platform_config = main._get_automation_platform_config(
            "wechat_channels",
            adapter_config={
                "automation": {
                    "verify_url": "https://channels.weixin.qq.com/platform/post/list",
                }
            },
            automation_config={
                "wechat_channels": {
                    "publish_url": "https://channels.weixin.qq.com/platform/post/create",
                    "frame_name": "content",
                    "locators": {
                        "title": ["input"],
                        "description": ["[contenteditable='true']"],
                        "video_upload": ["input[type='file']"],
                        "publish_button": ["button"],
                    },
                }
            },
        )

        self.assertEqual(
            platform_config["publish_url"],
            "https://channels.weixin.qq.com/platform/post/create",
        )
        self.assertEqual(platform_config["frame_name"], "content")
        self.assertEqual(
            platform_config["verify_url"],
            "https://channels.weixin.qq.com/platform/post/list",
        )
        self.assertIn("locators", platform_config)

    def test_step_upload_routes_to_publish_manager_when_enabled(self):
        metadata = {
            "video_path": "/tmp/video.mp4",
            "title": "Demo title",
            "description": "Demo description",
            "cover_path_16_9": "/tmp/cover_16_9.jpg",
            "cover_path_4_3": "/tmp/cover_4_3.jpg",
            "tags": ["tag1", "tag2"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "meta.json")
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

            args = SimpleNamespace(skip_upload=False, dry_run=True, yes=True, season=None, auto_season=False)
            mock_config = {
                "publishing": {"enabled": True, "default_targets": ["bilibili"]},
            }

            with mock.patch.object(main, "CONFIG", mock_config), \
                mock.patch.object(main, "run_multi_platform_publish") as run_publish, \
                mock.patch.object(main, "run_upload") as run_upload, \
                mock.patch("core.modules.uploader.validate_upload_assets", return_value=(True, [])) as validate_upload_assets, \
                mock.patch.object(main, "post_comment_flow") as post_comment:
                run_publish.return_value = True

                result = main.step_upload(args, schedule_time=0, metadata_path=metadata_path)

            self.assertTrue(result)
            validate_upload_assets.assert_called_once_with(metadata, targets=["bilibili"])
            run_publish.assert_called_once_with(metadata, metadata_path, schedule_time=0, dry_run=True)
            run_upload.assert_not_called()
            post_comment.assert_not_called()

    def test_step_upload_validates_non_bilibili_targets_without_4x3_requirement(self):
        metadata = {
            "video_path": "/tmp/video.mp4",
            "title": "Demo title",
            "description": "Demo description",
            "cover_path_16_9": "/tmp/cover_16_9.jpg",
            "tags": ["tag1", "tag2"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "meta.json")
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

            args = SimpleNamespace(skip_upload=False, dry_run=True, yes=True, season=None, auto_season=False)
            mock_config = {
                "publishing": {"enabled": True, "default_targets": ["douyin", "wechat_channels"]},
            }

            with mock.patch.object(main, "CONFIG", mock_config), \
                mock.patch.object(main, "run_multi_platform_publish") as run_publish, \
                mock.patch.object(main, "run_upload") as run_upload, \
                mock.patch("core.modules.uploader.validate_upload_assets", return_value=(True, [])) as validate_upload_assets, \
                mock.patch.object(main, "post_comment_flow") as post_comment:
                run_publish.return_value = True

                result = main.step_upload(args, schedule_time=0, metadata_path=metadata_path)

            self.assertTrue(result)
            validate_upload_assets.assert_called_once_with(
                metadata,
                targets=["douyin", "wechat_channels"],
            )
            run_publish.assert_called_once_with(metadata, metadata_path, schedule_time=0, dry_run=True)
            run_upload.assert_not_called()
            post_comment.assert_not_called()

    def test_run_multi_platform_publish_honors_configured_default_targets(self):
        metadata = {
            "video_path": "/tmp/video.mp4",
            "title": "Demo title",
            "description": "Demo description",
            "cover_path_16_9": "/tmp/cover_16_9.jpg",
            "cover_path_4_3": "/tmp/cover_4_3.jpg",
            "tags": ["tag1", "tag2"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "meta.json")
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

            mock_config = {
                "publishing": {
                    "enabled": True,
                    "default_targets": ["douyin", "wechat_channels"],
                    "adapters": {
                        "douyin": {
                            "enabled": True,
                            "mode": "api",
                            "access_token": "douyin-token",
                        },
                        "wechat_channels": {
                            "enabled": True,
                            "mode": "automation",
                        },
                    },
                },
                "publishing_automation": {
                    "wechat_channels": {
                        "publish_url": "https://channels.weixin.qq.com",
                        "locators": {
                            "title": ["textarea"],
                            "description": ["[contenteditable='true']"],
                            "video_upload": ["input[type='file']"],
                            "cover_upload": ["input[type='file']"],
                            "publish_button": ["button"],
                        },
                    },
                },
            }

            with mock.patch.object(main, "CONFIG", mock_config), \
                mock.patch.object(main, "DouyinAdapter", create=True) as douyin_adapter, \
                mock.patch.object(main, "WechatChannelsAdapter", create=True) as wechat_adapter, \
                mock.patch.object(main, "BilibiliAdapter") as bilibili_adapter, \
                mock.patch.object(main, "build_registry") as build_registry, \
                mock.patch.object(main, "PublishManager") as publish_manager:
                douyin_instance = object()
                wechat_instance = object()
                douyin_adapter.return_value = douyin_instance
                wechat_adapter.return_value = wechat_instance
                publish_manager.return_value.execute.return_value = metadata

                result = main.run_multi_platform_publish(
                    metadata,
                    metadata_path,
                    schedule_time=0,
                    dry_run=False,
                )

            self.assertTrue(result)
            build_registry.assert_called_once_with(douyin_instance, wechat_instance)

    def test_run_multi_platform_publish_dry_run_still_builds_configured_registry(self):
        metadata = {
            "video_path": "/tmp/video.mp4",
            "title": "Demo title",
            "description": "Demo description",
            "cover_path_16_9": "/tmp/cover_16_9.jpg",
            "cover_path_4_3": "/tmp/cover_4_3.jpg",
            "tags": ["tag1", "tag2"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "meta.json")
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

            mock_config = {
                "publishing": {
                    "enabled": True,
                    "default_targets": ["wechat_channels"],
                    "adapters": {
                        "wechat_channels": {
                            "enabled": True,
                            "mode": "automation",
                        },
                    },
                },
                "publishing_automation": {
                    "wechat_channels": {
                        "publish_url": "https://channels.weixin.qq.com",
                        "locators": {
                            "title": ["textarea"],
                            "description": ["[contenteditable='true']"],
                            "video_upload": ["input[type='file']"],
                            "cover_upload": ["input[type='file']"],
                            "publish_button": ["button"],
                        },
                    },
                },
            }

            with mock.patch.object(main, "CONFIG", mock_config), \
                mock.patch.object(main, "WechatChannelsAdapter", create=True) as wechat_adapter, \
                mock.patch.object(main, "build_registry") as build_registry, \
                mock.patch.object(main, "PublishManager") as publish_manager:
                wechat_instance = object()
                wechat_adapter.return_value = wechat_instance

                result = main.run_multi_platform_publish(
                    metadata,
                    metadata_path,
                    schedule_time=0,
                    dry_run=True,
                )

            self.assertTrue(result)
            wechat_adapter.assert_called_once_with(config=mock.ANY, selectors=mock.ANY)
            build_registry.assert_called_once_with(wechat_instance)

    def test_run_multi_platform_publish_builds_platform_payloads_from_cover_assets(self):
        metadata = {
            "video_path": "/tmp/video.mp4",
            "title": "居家40分钟全身燃脂：无需器械，练就紧致线条",
            "description": "Demo description",
            "cover_path_16_9": "/tmp/cover_16_9.jpg",
            "cover_path_4_3": "/tmp/cover_4_3.jpg",
            "tags": ["健身", "燃脂"],
            "cover_assets": {
                "platforms": {
                    "xiaohongshu": {"path": "/tmp/cover_xhs.jpg"},
                }
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "meta.json")
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

            mock_config = {
                "publishing": {
                    "enabled": True,
                    "default_targets": ["bilibili", "xiaohongshu"],
                    "adapters": {
                        "bilibili": {"enabled": True, "mode": "api"},
                        "xiaohongshu": {"enabled": True, "mode": "automation"},
                    },
                },
                "publishing_automation": {
                    "xiaohongshu": {
                        "publish_url": "https://creator.xiaohongshu.com",
                        "locators": {
                            "title": ["input"],
                            "description": ["textarea"],
                            "video_upload": ["input[type='file']"],
                            "cover_upload": ["input[type='file']"],
                            "publish_button": ["button"],
                        },
                    }
                },
            }

            with mock.patch.object(main, "CONFIG", mock_config), \
                mock.patch.object(main, "BilibiliAdapter") as bilibili_adapter, \
                mock.patch.object(main, "XiaohongshuAdapter", create=True) as xhs_adapter, \
                mock.patch.object(main, "build_registry") as build_registry, \
                mock.patch.object(main, "PublishManager") as publish_manager:
                bilibili_instance = object()
                xhs_instance = object()
                bilibili_adapter.return_value = bilibili_instance
                xhs_adapter.return_value = xhs_instance
                publish_manager.return_value.execute.return_value = metadata

                result = main.run_multi_platform_publish(
                    metadata,
                    metadata_path,
                    schedule_time=0,
                    dry_run=False,
                )

            self.assertTrue(result)
            job = publish_manager.return_value.execute.call_args.args[0]
            self.assertEqual(job.platform_payloads["bilibili"]["cover_path_4_3"], "/tmp/cover_4_3.jpg")
            self.assertEqual(job.platform_payloads["xiaohongshu"]["cover_path"], "/tmp/cover_xhs.jpg")
            self.assertEqual(job.platform_payloads["xiaohongshu"]["cover_text"], "居家燃脂跟练")
            build_registry.assert_called_once_with(bilibili_instance, xhs_instance)

    def test_run_multi_platform_publish_skips_targets_already_marked_published(self):
        metadata = {
            "video_path": "/tmp/video.mp4",
            "title": "Demo title",
            "description": "Demo description",
            "cover_path_16_9": "/tmp/cover_16_9.jpg",
            "cover_path_4_3": "/tmp/cover_4_3.jpg",
            "tags": ["tag1", "tag2"],
            "publish_records": {
                "bilibili": {
                    "status": "published",
                    "platform_content_id": "BV1done",
                }
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "meta.json")
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

            mock_config = {
                "publishing": {
                    "enabled": True,
                    "default_targets": ["bilibili", "douyin"],
                    "adapters": {
                        "bilibili": {"enabled": True, "mode": "api"},
                        "douyin": {
                            "enabled": True,
                            "mode": "api",
                            "access_token": "douyin-token",
                        },
                    },
                },
            }

            with mock.patch.object(main, "CONFIG", mock_config), \
                mock.patch.object(main, "BilibiliAdapter") as bilibili_adapter, \
                mock.patch.object(main, "DouyinAdapter", create=True) as douyin_adapter, \
                mock.patch.object(main, "build_registry") as build_registry, \
                mock.patch.object(main, "PublishManager") as publish_manager:
                douyin_instance = object()
                douyin_adapter.return_value = douyin_instance
                publish_manager.return_value.execute.return_value = metadata

                result = main.run_multi_platform_publish(
                    metadata,
                    metadata_path,
                    schedule_time=0,
                    dry_run=False,
                )

            self.assertTrue(result)
            bilibili_adapter.assert_not_called()
            douyin_adapter.assert_called_once()
            build_registry.assert_called_once_with(douyin_instance)

    def test_run_multi_platform_publish_skips_bilibili_when_legacy_bvid_exists(self):
        metadata = {
            "video_path": "/tmp/video.mp4",
            "title": "Demo title",
            "description": "Demo description",
            "cover_path_16_9": "/tmp/cover_16_9.jpg",
            "cover_path_4_3": "/tmp/cover_4_3.jpg",
            "tags": ["tag1", "tag2"],
            "uploaded_bvid": "BV1legacy",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "meta.json")
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

            mock_config = {
                "publishing": {
                    "enabled": True,
                    "default_targets": ["bilibili", "douyin"],
                    "adapters": {
                        "bilibili": {"enabled": True, "mode": "api"},
                        "douyin": {
                            "enabled": True,
                            "mode": "api",
                            "access_token": "douyin-token",
                        },
                    },
                },
            }

            with mock.patch.object(main, "CONFIG", mock_config), \
                mock.patch.object(main, "BilibiliAdapter") as bilibili_adapter, \
                mock.patch.object(main, "DouyinAdapter", create=True) as douyin_adapter, \
                mock.patch.object(main, "build_registry") as build_registry, \
                mock.patch.object(main, "PublishManager") as publish_manager:
                douyin_instance = object()
                douyin_adapter.return_value = douyin_instance
                publish_manager.return_value.execute.return_value = metadata

                result = main.run_multi_platform_publish(
                    metadata,
                    metadata_path,
                    schedule_time=0,
                    dry_run=False,
                )

            self.assertTrue(result)
            bilibili_adapter.assert_not_called()
            douyin_adapter.assert_called_once()
            build_registry.assert_called_once_with(douyin_instance)

    def test_run_multi_platform_publish_instantiates_all_enabled_adapters_from_config(self):
        metadata = {
            "video_path": "/tmp/video.mp4",
            "title": "Demo title",
            "description": "Demo description",
            "cover_path_16_9": "/tmp/cover_16_9.jpg",
            "cover_path_4_3": "/tmp/cover_4_3.jpg",
            "tags": ["tag1", "tag2"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "meta.json")
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

            mock_config = {
                "publishing": {
                    "enabled": True,
                    "default_targets": [
                        "bilibili",
                        "douyin",
                        "kuaishou",
                        "wechat_channels",
                        "xiaohongshu",
                    ],
                    "adapters": {
                        "bilibili": {"enabled": True, "mode": "api"},
                        "douyin": {
                            "enabled": True,
                            "mode": "api",
                            "access_token": "douyin-token",
                        },
                        "kuaishou": {
                            "enabled": True,
                            "mode": "api",
                            "access_token": "kuaishou-token",
                            "app_id": "kuaishou-app",
                        },
                        "wechat_channels": {
                            "enabled": True,
                            "mode": "automation",
                        },
                        "xiaohongshu": {
                            "enabled": True,
                            "mode": "automation",
                        },
                    },
                },
                "publishing_automation": {
                    "wechat_channels": {
                        "publish_url": "https://channels.weixin.qq.com",
                        "locators": {
                            "title": ["textarea"],
                            "description": ["[contenteditable='true']"],
                            "video_upload": ["input[type='file']"],
                            "cover_upload": ["input[type='file']"],
                            "publish_button": ["button"],
                        },
                    },
                    "xiaohongshu": {
                        "publish_url": "https://creator.xiaohongshu.com",
                        "locators": {
                            "title": ["input"],
                            "description": ["textarea"],
                            "video_upload": ["input[type='file']"],
                            "cover_upload": ["input[type='file']"],
                            "publish_button": ["button"],
                        },
                    },
                },
            }

            with mock.patch.object(main, "CONFIG", mock_config), \
                mock.patch.object(main, "BilibiliAdapter") as bilibili_adapter, \
                mock.patch.object(main, "DouyinAdapter", create=True) as douyin_adapter, \
                mock.patch.object(main, "KuaishouAdapter", create=True) as kuaishou_adapter, \
                mock.patch.object(main, "WechatChannelsAdapter", create=True) as wechat_adapter, \
                mock.patch.object(main, "XiaohongshuAdapter", create=True) as xhs_adapter, \
                mock.patch.object(main, "build_registry") as build_registry, \
                mock.patch.object(main, "PublishManager") as publish_manager:
                bilibili_instance = object()
                douyin_instance = object()
                kuaishou_instance = object()
                wechat_instance = object()
                xhs_instance = object()
                bilibili_adapter.return_value = bilibili_instance
                douyin_adapter.return_value = douyin_instance
                kuaishou_adapter.return_value = kuaishou_instance
                wechat_adapter.return_value = wechat_instance
                xhs_adapter.return_value = xhs_instance
                publish_manager.return_value.execute.return_value = metadata

                result = main.run_multi_platform_publish(
                    metadata,
                    metadata_path,
                    schedule_time=0,
                    dry_run=False,
                )

            self.assertTrue(result)
            douyin_adapter.assert_called_once_with(client=mock.ANY)
            kuaishou_adapter.assert_called_once_with(client=mock.ANY)
            wechat_adapter.assert_called_once_with(config=mock.ANY, selectors=mock.ANY)
            xhs_adapter.assert_called_once_with(config=mock.ANY, selectors=mock.ANY)
            build_registry.assert_called_once_with(
                bilibili_instance,
                douyin_instance,
                kuaishou_instance,
                wechat_instance,
                xhs_instance,
            )

    def test_run_multi_platform_publish_selects_douyin_and_kuaishou_automation_mode(self):
        metadata = {
            "video_path": "/tmp/video.mp4",
            "title": "Demo title",
            "description": "Demo description",
            "cover_path_16_9": "/tmp/cover_16_9.jpg",
            "cover_path_4_3": "/tmp/cover_4_3.jpg",
            "tags": ["tag1", "tag2"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "meta.json")
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

            mock_config = {
                "publishing": {
                    "enabled": True,
                    "default_targets": ["douyin", "kuaishou"],
                    "adapters": {
                        "douyin": {
                            "enabled": True,
                            "mode": "automation",
                        },
                        "kuaishou": {
                            "enabled": True,
                            "mode": "automation",
                        },
                    },
                },
                "publishing_automation": {
                    "douyin": {
                        "publish_url": "https://creator.douyin.com",
                        "locators": {
                            "title": ["input"],
                            "description": ["textarea"],
                            "video_upload": ["input[type='file']"],
                            "publish_button": ["button"],
                        },
                    },
                    "kuaishou": {
                        "publish_url": "https://cp.kuaishou.com",
                        "locators": {
                            "title": ["input"],
                            "description": ["textarea"],
                            "video_upload": ["input[type='file']"],
                            "cover_upload": ["input[type='file']"],
                            "publish_button": ["button"],
                        },
                    },
                },
            }

            with mock.patch.object(main, "CONFIG", mock_config), \
                mock.patch.object(main, "DouyinAutomationAdapter", create=True) as douyin_automation_adapter, \
                mock.patch.object(main, "KuaishouAutomationAdapter", create=True) as kuaishou_automation_adapter, \
                mock.patch.object(main, "DouyinAdapter", create=True) as douyin_api_adapter, \
                mock.patch.object(main, "KuaishouAdapter", create=True) as kuaishou_api_adapter, \
                mock.patch.object(main, "build_registry") as build_registry, \
                mock.patch.object(main, "PublishManager") as publish_manager:
                douyin_automation_instance = object()
                kuaishou_automation_instance = object()
                douyin_automation_adapter.return_value = douyin_automation_instance
                kuaishou_automation_adapter.return_value = kuaishou_automation_instance
                publish_manager.return_value.execute.return_value = metadata

                result = main.run_multi_platform_publish(
                    metadata,
                    metadata_path,
                    schedule_time=0,
                    dry_run=False,
                )

            self.assertTrue(result)
            douyin_api_adapter.assert_not_called()
            kuaishou_api_adapter.assert_not_called()
            douyin_automation_adapter.assert_called_once_with(config=mock.ANY, selectors=mock.ANY)
            kuaishou_automation_adapter.assert_called_once_with(config=mock.ANY, selectors=mock.ANY)
            build_registry.assert_called_once_with(
                douyin_automation_instance,
                kuaishou_automation_instance,
            )

    def test_run_multi_platform_publish_dry_run_builds_douyin_and_kuaishou_automation_adapters(self):
        metadata = {
            "video_path": "/tmp/video.mp4",
            "title": "Demo title",
            "description": "Demo description",
            "cover_path_16_9": "/tmp/cover_16_9.jpg",
            "cover_path_4_3": "/tmp/cover_4_3.jpg",
            "tags": ["tag1", "tag2"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "meta.json")
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

            mock_config = {
                "publishing": {
                    "enabled": True,
                    "default_targets": ["douyin", "kuaishou"],
                    "adapters": {
                        "douyin": {
                            "enabled": True,
                            "mode": "automation",
                        },
                        "kuaishou": {
                            "enabled": True,
                            "mode": "automation",
                        },
                    },
                },
                "publishing_automation": {
                    "douyin": {
                        "publish_url": "https://creator.douyin.com",
                        "locators": {
                            "title": ["input"],
                            "description": ["textarea"],
                            "video_upload": ["input[type='file']"],
                            "publish_button": ["button"],
                        },
                    },
                    "kuaishou": {
                        "publish_url": "https://cp.kuaishou.com",
                        "locators": {
                            "title": ["input"],
                            "description": ["textarea"],
                            "video_upload": ["input[type='file']"],
                            "cover_upload": ["input[type='file']"],
                            "publish_button": ["button"],
                        },
                    },
                },
            }

            with mock.patch.object(main, "CONFIG", mock_config), \
                mock.patch.object(main, "DouyinAutomationAdapter", create=True) as douyin_automation_adapter, \
                mock.patch.object(main, "KuaishouAutomationAdapter", create=True) as kuaishou_automation_adapter, \
                mock.patch.object(main, "build_registry") as build_registry, \
                mock.patch.object(main, "PublishManager") as publish_manager:
                douyin_automation_instance = object()
                kuaishou_automation_instance = object()
                douyin_automation_adapter.return_value = douyin_automation_instance
                kuaishou_automation_adapter.return_value = kuaishou_automation_instance

                result = main.run_multi_platform_publish(
                    metadata,
                    metadata_path,
                    schedule_time=0,
                    dry_run=True,
                )

            self.assertTrue(result)
            douyin_automation_adapter.assert_called_once_with(config=mock.ANY, selectors=mock.ANY)
            kuaishou_automation_adapter.assert_called_once_with(config=mock.ANY, selectors=mock.ANY)
            build_registry.assert_called_once_with(
                douyin_automation_instance,
                kuaishou_automation_instance,
            )

    def test_get_bilibili_publish_ids_prefers_publish_records(self):
        metadata = {
            "publish_records": {
                "bilibili": {
                    "status": "published",
                    "platform_content_id": "BV1abc123",
                    "aid": 987654,
                }
            },
            "uploaded_bvid": "legacy-bvid",
            "uploaded_aid": 123456,
        }

        bvid, aid = get_bilibili_publish_ids(metadata)

        self.assertEqual(bvid, "BV1abc123")
        self.assertEqual(aid, 987654)


if __name__ == "__main__":
    unittest.main()
