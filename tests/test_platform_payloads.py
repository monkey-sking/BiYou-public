import os
import tempfile
import unittest

from core.publishing.platform_payloads import (
    build_platform_cover_style,
    build_platform_cover_output_path,
    build_platform_cover_text,
    build_platform_description,
    build_platform_cover_variant_specs,
    build_platform_payloads,
    build_platform_tags,
    build_platform_title,
    hydrate_cover_metadata_from_disk,
    should_refresh_platform_cover_asset,
)


class TestPlatformPayloads(unittest.TestCase):
    def _metadata(self):
        return {
            "title": "居家40分钟全身燃脂：无需器械，练就紧致线条",
            "description": (
                "各位好！家人们，今天带来这套40分钟超长待机的全身训练，在家就能练，"
                "完全不需要任何器械。这套跟练涵盖了全身所有大肌肉群。\n\n"
                "💪 进阶计划\n"
                "想要更系统的改变？点击下方链接加入完整训练营："
                "[https://trybe.do/christiancruzfitness](https://trybe.do/christiancruzfitness)\n\n"
                "喜欢的点个三连支持一下！"
            ),
            "tags": ["健身", "居家健身", "燃脂", "全身训练", "减肥", "HIIT"],
            "cover_path_16_9": "/tmp/workout_cover_16x9.jpg",
            "cover_path_4_3": "/tmp/workout_cover_4x3.jpg",
        }

    def test_build_platform_title_applies_platform_limits(self):
        metadata = self._metadata()

        self.assertEqual(
            build_platform_title("wechat_channels", metadata["title"], metadata["tags"]),
            "居家40分钟全身燃脂",
        )
        self.assertEqual(
            build_platform_title("xiaohongshu", metadata["title"], metadata["tags"]),
            "居家40分钟全身燃脂跟练",
        )

    def test_build_platform_cover_text_uses_platform_specific_style(self):
        metadata = self._metadata()

        self.assertEqual(
            build_platform_cover_text("douyin", metadata["title"], metadata["tags"]),
            "全身燃脂",
        )
        self.assertEqual(
            build_platform_cover_text("kuaishou", metadata["title"], metadata["tags"]),
            "无需器械燃脂",
        )
        self.assertEqual(
            build_platform_cover_text("xiaohongshu", metadata["title"], metadata["tags"]),
            "居家燃脂跟练",
        )

    def test_build_platform_description_strips_bilibili_style_and_adds_platform_tone(self):
        metadata = self._metadata()

        douyin_description = build_platform_description(
            "douyin",
            metadata["title"],
            metadata["description"],
            metadata["tags"],
        )
        xiaohongshu_description = build_platform_description(
            "xiaohongshu",
            metadata["title"],
            metadata["description"],
            metadata["tags"],
        )

        self.assertIn("适合居家跟练", douyin_description)
        self.assertNotIn("各位好", douyin_description)
        self.assertNotIn("三连", douyin_description)
        self.assertNotIn("https://", douyin_description)
        self.assertTrue(xiaohongshu_description.startswith("今天分享"))
        self.assertIn("先收藏再练", xiaohongshu_description)

    def test_build_platform_tags_and_cover_style_apply_platform_limits(self):
        metadata = self._metadata()

        self.assertEqual(
            build_platform_tags("douyin", metadata["title"], metadata["tags"]),
            ["健身", "居家健身", "燃脂", "全身训练", "减肥"],
        )
        self.assertEqual(
            build_platform_tags("kuaishou", metadata["title"], metadata["tags"]),
            ["健身", "居家健身", "燃脂", "全身训练"],
        )
        self.assertIn(
            "训练收益",
            build_platform_cover_style("douyin", metadata["title"], metadata["tags"]),
        )

    def test_build_platform_cover_output_path_reuses_base_stem(self):
        output_path = build_platform_cover_output_path(
            "/tmp/workout_cover_16x9.jpg",
            "xiaohongshu",
        )

        self.assertEqual(output_path, "/tmp/workout_cover_xiaohongshu.jpg")

    def test_build_platform_cover_variant_specs_skips_bilibili_and_builds_paths(self):
        specs = build_platform_cover_variant_specs(
            self._metadata(),
            ["bilibili", "douyin", "wechat_channels", "xiaohongshu"],
        )

        self.assertNotIn("bilibili", specs)
        self.assertEqual(specs["douyin"]["output_path"], "/tmp/workout_cover_douyin.jpg")
        self.assertEqual(specs["douyin"]["aspect_ratio"], "16:9")
        self.assertEqual(specs["douyin"]["display_crop_ratio"], "3:4")
        self.assertEqual(specs["wechat_channels"]["display_crop_ratio"], "6:7")
        self.assertEqual(specs["xiaohongshu"]["aspect_ratio"], "3:4")
        self.assertIn("3:4", specs["xiaohongshu"]["cover_layout"])
        self.assertEqual(specs["xiaohongshu"]["cover_text"], "居家燃脂跟练")

    def test_should_refresh_platform_cover_asset_detects_legacy_or_mismatched_spec(self):
        metadata = self._metadata()
        spec = build_platform_cover_variant_specs(metadata, ["douyin"])["douyin"]

        self.assertTrue(should_refresh_platform_cover_asset({}, spec))
        self.assertTrue(
            should_refresh_platform_cover_asset(
                {
                    "source_base": "16_9",
                    "aspect_ratio": "16:9",
                    "display_crop_ratio": "",
                    "cover_text": spec["cover_text"],
                    "style": spec["cover_style"],
                },
                spec,
            )
        )
        self.assertFalse(
            should_refresh_platform_cover_asset(
                {
                    "variant_version": spec["variant_version"],
                    "source_base": spec["source_base"],
                    "aspect_ratio": spec["aspect_ratio"],
                    "display_crop_ratio": spec["display_crop_ratio"],
                    "cover_text": spec["cover_text"],
                    "style": spec["cover_style"],
                },
                spec,
            )
        )

    def test_build_platform_payloads_prefers_platform_assets_then_base_cover(self):
        metadata = self._metadata()
        metadata["cover_assets"] = {
            "platforms": {
                "xiaohongshu": {
                    "path": "/tmp/workout_cover_xiaohongshu.jpg",
                }
            }
        }

        payloads = build_platform_payloads(
            metadata,
            ["bilibili", "douyin", "kuaishou", "xiaohongshu"],
        )

        self.assertEqual(payloads["bilibili"]["cover_path_4_3"], "/tmp/workout_cover_4x3.jpg")
        self.assertEqual(payloads["douyin"]["cover_path"], "/tmp/workout_cover_16x9.jpg")
        self.assertEqual(payloads["xiaohongshu"]["cover_path"], "/tmp/workout_cover_xiaohongshu.jpg")
        self.assertEqual(payloads["xiaohongshu"]["cover_text"], "居家燃脂跟练")
        self.assertEqual(
            payloads["kuaishou"]["tags"],
            ["健身", "居家健身", "燃脂", "全身训练"],
        )
        self.assertIn("适合居家跟练", payloads["douyin"]["description"])
        self.assertIn("先收藏再练", payloads["xiaohongshu"]["description"])

    def test_build_platform_payloads_ignores_failed_platform_cover_asset_and_falls_back_to_base(self):
        metadata = self._metadata()
        metadata["cover_assets"] = {
            "platforms": {
                "xiaohongshu": {
                    "path": "/tmp/workout_cover_xiaohongshu.jpg",
                    "status": "failed",
                }
            }
        }

        payloads = build_platform_payloads(metadata, ["xiaohongshu"])

        self.assertEqual(payloads["xiaohongshu"]["cover_path"], "/tmp/workout_cover_16x9.jpg")

    def test_build_platform_payloads_refreshes_stale_base_cover_path_when_platform_asset_exists(self):
        metadata = self._metadata()
        metadata["cover_assets"] = {
            "platforms": {
                "xiaohongshu": {
                    "path": "/tmp/workout_cover_xiaohongshu.jpg",
                    "status": "generated",
                }
            }
        }
        metadata["platform_payloads"] = {
            "xiaohongshu": {
                "cover_path": "/tmp/workout_cover_16x9.jpg",
            }
        }

        payloads = build_platform_payloads(metadata, ["xiaohongshu"])

        self.assertEqual(payloads["xiaohongshu"]["cover_path"], "/tmp/workout_cover_xiaohongshu.jpg")

    def test_build_platform_payloads_refreshes_null_cover_path_when_platform_asset_exists(self):
        metadata = self._metadata()
        metadata["cover_assets"] = {
            "platforms": {
                "douyin": {
                    "path": "/tmp/workout_cover_douyin.jpg",
                    "status": "generated",
                }
            }
        }
        metadata["platform_payloads"] = {
            "douyin": {
                "cover_path": None,
            }
        }

        payloads = build_platform_payloads(metadata, ["douyin"])

        self.assertEqual(payloads["douyin"]["cover_path"], "/tmp/workout_cover_douyin.jpg")

    def test_build_platform_payloads_refreshes_null_bilibili_cover_fields(self):
        metadata = self._metadata()
        metadata["platform_payloads"] = {
            "bilibili": {
                "cover_path": None,
                "cover_path_4_3": None,
            }
        }

        payloads = build_platform_payloads(metadata, ["bilibili"])

        self.assertEqual(payloads["bilibili"]["cover_path"], "/tmp/workout_cover_16x9.jpg")
        self.assertEqual(payloads["bilibili"]["cover_path_4_3"], "/tmp/workout_cover_4x3.jpg")

    def test_build_platform_payloads_refreshes_string_none_cover_fields(self):
        metadata = self._metadata()
        metadata["cover_assets"] = {
            "platforms": {
                "douyin": {
                    "path": "/tmp/workout_cover_douyin.jpg",
                    "status": "generated",
                }
            }
        }
        metadata["platform_payloads"] = {
            "bilibili": {
                "cover_path": "None",
                "cover_path_4_3": "None",
            },
            "douyin": {
                "cover_path": "None",
            },
        }

        payloads = build_platform_payloads(metadata, ["bilibili", "douyin"])

        self.assertEqual(payloads["bilibili"]["cover_path"], "/tmp/workout_cover_16x9.jpg")
        self.assertEqual(payloads["bilibili"]["cover_path_4_3"], "/tmp/workout_cover_4x3.jpg")
        self.assertEqual(payloads["douyin"]["cover_path"], "/tmp/workout_cover_douyin.jpg")

    def test_build_platform_payloads_refreshes_legacy_long_description_and_tags(self):
        metadata = self._metadata()
        metadata["platform_payloads"] = {
            "douyin": {
                "title": "居家40分钟全身燃脂",
                "description": metadata["description"],
                "tags": metadata["tags"] + ["塑形", "暴汗", "训练计划"],
            }
        }

        payloads = build_platform_payloads(metadata, ["douyin"])

        self.assertEqual(payloads["douyin"]["title"], "居家40分钟全身燃脂")
        self.assertLessEqual(len(payloads["douyin"]["description"]), 80)
        self.assertIn("适合居家跟练", payloads["douyin"]["description"])
        self.assertEqual(
            payloads["douyin"]["tags"],
            ["健身", "居家健身", "燃脂", "全身训练", "减肥"],
        )

    def test_build_platform_payloads_refreshes_legacy_bracket_artifact_title(self):
        metadata = self._metadata()
        metadata["title"] = "【UE5 x AI】这就是目前CG短片的极限？MetaHuman 联动可灵 Kling 3.0 实机演示"
        metadata["tags"] = ["UE5", "AI", "可灵", "Kling", "MetaHuman"]
        metadata["platform_payloads"] = {
            "xiaohongshu": {
                "title": "【UE5",
            },
            "wechat_channels": {
                "title": "【UE5",
            },
        }

        payloads = build_platform_payloads(metadata, ["xiaohongshu", "wechat_channels"])

        self.assertEqual(payloads["xiaohongshu"]["title"], "这就是目前CG短片的极限")
        self.assertEqual(payloads["wechat_channels"]["title"], "这就是目前CG短片的极限")

    def test_build_platform_payloads_merges_platform_options_and_bilibili_top_level_fields(self):
        metadata = self._metadata()
        metadata.update(
            {
                "category": "运动-健身",
                "mission_id": 4058195,
                "topic_keyword": "健身",
                "suggested_collection": "居家暴汗健身跟练",
                "copyright": 2,
                "platform_options": {
                    "douyin": {
                        "visibility": "friends",
                        "allow_comment": False,
                    }
                },
            }
        )

        payloads = build_platform_payloads(metadata, ["bilibili", "douyin"])

        self.assertEqual(payloads["bilibili"]["category"], "运动-健身")
        self.assertEqual(payloads["bilibili"]["mission_id"], 4058195)
        self.assertEqual(payloads["bilibili"]["topic_keyword"], "健身")
        self.assertEqual(payloads["bilibili"]["suggested_collection"], "居家暴汗健身跟练")
        self.assertEqual(payloads["bilibili"]["copyright"], 2)
        self.assertEqual(payloads["douyin"]["visibility"], "friends")
        self.assertFalse(payloads["douyin"]["allow_comment"])

    def test_build_platform_title_skips_bracket_prefix_fragments(self):
        title = "【UE5 x AI】这就是目前CG短片的极限？MetaHuman 联动可灵 Kling 3.0 实机演示"

        self.assertEqual(
            build_platform_title("douyin", title, ["UE5", "AI"]),
            "这就是目前CG短片的极限",
        )
        self.assertEqual(
            build_platform_title("wechat_channels", title, ["UE5", "AI"]),
            "这就是目前CG短片的极限",
        )

    def test_build_platform_payloads_refreshes_english_title_when_base_is_chinese(self):
        """When metadata title is translated to Chinese but old payloads still have
        English titles/tags, the refresh logic should detect the language mismatch
        and regenerate payloads from the current Chinese base data."""
        metadata = self._metadata()
        # Simulate a translated title: base is now Chinese
        metadata["title"] = "【UE5】告别廉价感！Epic官方白嫖Niagara顶级特效"
        metadata["tags"] = ["UE5", "虚幻引擎", "Niagara", "游戏特效", "游戏开发"]
        # But old payloads still have stale English data
        metadata["platform_payloads"] = {
            "bilibili": {
                "title": "Your Unreal Engine 5 Game Looks Indie.",
                "tags": ["games", "videojuegos", "unreal engine", "unreal engine 5"],
            },
            "douyin": {
                "title": "Niagara",
                "tags": ["games", "unreal engine", "unreal engine 5", "ue5", "niagara"],
            },
        }

        payloads = build_platform_payloads(
            metadata, ["bilibili", "douyin", "kuaishou"]
        )

        # Bilibili title should be refreshed to the Chinese translation
        self.assertEqual(payloads["bilibili"]["title"], metadata["title"])
        # Bilibili tags should be refreshed to Chinese tags
        self.assertEqual(
            payloads["bilibili"]["tags"],
            ["UE5", "虚幻引擎", "Niagara", "游戏特效", "游戏开发"],
        )
        # Douyin title should be rebuilt from Chinese title (picks longest clause)
        self.assertIn("Niagara", payloads["douyin"]["title"])
        # Douyin tags should be refreshed
        self.assertIn("UE5", payloads["douyin"]["tags"])
        self.assertNotIn("games", payloads["douyin"]["tags"])

    def test_build_platform_payloads_keeps_payload_when_language_matches(self):
        """When existing payload title language matches the base title language,
        the refresh logic should NOT force a refresh just because of language."""
        metadata = self._metadata()
        metadata["platform_payloads"] = {
            "douyin": {
                "title": "居家40分钟全身燃脂",
                "description": "适合居家跟练",
                "tags": ["健身", "燃脂"],
            }
        }

        payloads = build_platform_payloads(metadata, ["douyin"])

        # Title should stay the same — no language mismatch
        self.assertEqual(payloads["douyin"]["title"], "居家40分钟全身燃脂")
        # Tags should be kept since they're within limit and same language
        # (they may get refreshed due to tag count logic, but NOT due to language)
        self.assertIsInstance(payloads["douyin"]["tags"], list)

    def test_clean_path_text_preserves_internal_double_spaces(self):
        """_clean_path_text must preserve internal whitespace (e.g. double spaces
        in YouTube titles with full-width pipe) so file paths remain valid."""
        from core.publishing.platform_payloads import _clean_path_text

        path_with_double_space = (
            "/tmp/Unreal Engine 5 vs Generative AI (Kling) ｜  Shot-by-shot cover.jpg"
        )
        result = _clean_path_text(path_with_double_space)
        self.assertIn("｜  ", result)
        self.assertNotIn("｜ ", result.replace("｜  ", ""))
        # Also verify _clean_text would have collapsed it (regression guard)
        from core.publishing.platform_payloads import _clean_text
        collapsed = _clean_text(path_with_double_space)
        self.assertIn("｜ ", collapsed)
        self.assertNotIn("｜  ", collapsed)

    def test_should_refresh_payload_cover_path_detects_nonexistent_file(self):
        """If the current cover_path points to a file that doesn't exist on disk,
        _should_refresh_payload_cover_path must return True so the stale path
        gets replaced with a valid one."""
        from core.publishing.platform_payloads import _should_refresh_payload_cover_path

        with tempfile.TemporaryDirectory() as tmpdir:
            real_file = os.path.join(tmpdir, "real_cover.jpg")
            open(real_file, "wb").close()
            fake_file = os.path.join(tmpdir, "nonexistent_cover.jpg")

            # Current path doesn't exist → should refresh
            self.assertTrue(
                _should_refresh_payload_cover_path(
                    fake_file,
                    asset_cover_path=real_file,
                    base_cover_16_9=real_file,
                    base_cover_4_3="",
                )
            )
            # Current path exists → should NOT refresh just because it differs
            self.assertFalse(
                _should_refresh_payload_cover_path(
                    real_file,
                    asset_cover_path=real_file,
                    base_cover_16_9="",
                    base_cover_4_3="",
                )
            )

    def test_build_platform_payloads_refreshes_bilibili_cover_when_file_missing(self):
        """Bilibili cover_path/cover_path_4_3 must be overwritten when the
        existing path doesn't point to a real file on disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, "demo.mp4")
            open(video_path, "wb").close()
            cover_16x9 = os.path.join(tmpdir, "demo_cover_16x9.jpg")
            cover_4x3 = os.path.join(tmpdir, "demo_cover_4x3.jpg")
            for p in (cover_16x9, cover_4x3):
                open(p, "wb").close()

            metadata = {
                "title": "测试视频",
                "description": "描述",
                "tags": ["测试"],
                "video_path": video_path,
                "cover_path_16_9": cover_16x9,
                "cover_path_4_3": cover_4x3,
                "platform_payloads": {
                    "bilibili": {
                        "cover_path": os.path.join(tmpdir, "stale_cover.jpg"),  # doesn't exist
                        "cover_path_4_3": os.path.join(tmpdir, "stale_cover_4x3.jpg"),  # doesn't exist
                    }
                },
            }

            payloads = build_platform_payloads(metadata, ["bilibili"])

            self.assertEqual(payloads["bilibili"]["cover_path"], cover_16x9)
            self.assertEqual(payloads["bilibili"]["cover_path_4_3"], cover_4x3)

    def test_hydrate_cover_metadata_from_disk_restores_existing_cover_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, "demo.mp4")
            open(video_path, "wb").close()
            cover_16x9 = os.path.join(tmpdir, "demo_cover_16x9.jpg")
            cover_4x3 = os.path.join(tmpdir, "demo_cover_4x3.jpg")
            cover_xhs = os.path.join(tmpdir, "demo_cover_xiaohongshu.jpg")
            for path in (cover_16x9, cover_4x3, cover_xhs):
                open(path, "wb").close()

            metadata = {
                "title": "居家40分钟全身燃脂：无需器械，练就紧致线条",
                "description": "今天继续跟练。",
                "tags": ["健身", "燃脂"],
                "video_path": video_path,
                "cover_path_16_9": "",
                "cover_path_4_3": "",
                "cover_path": "",
                "platform_payloads": {
                    "bilibili": {
                        "cover_path": None,
                        "cover_path_4_3": None,
                    },
                    "xiaohongshu": {
                        "cover_path": None,
                    },
                },
            }

            hydrated = hydrate_cover_metadata_from_disk(
                metadata,
                ["bilibili", "xiaohongshu"],
            )

            self.assertEqual(hydrated["cover_path_16_9"], cover_16x9)
            self.assertEqual(hydrated["cover_path_4_3"], cover_4x3)
            self.assertEqual(hydrated["cover_path"], cover_16x9)
            self.assertEqual(
                hydrated["cover_assets"]["platforms"]["xiaohongshu"]["path"],
                cover_xhs,
            )
            self.assertEqual(
                hydrated["cover_assets"]["platforms"]["xiaohongshu"]["status"],
                "reused_existing",
            )
            self.assertEqual(
                hydrated["platform_payloads"]["bilibili"]["cover_path"],
                cover_16x9,
            )
            self.assertEqual(
                hydrated["platform_payloads"]["bilibili"]["cover_path_4_3"],
                cover_4x3,
            )
            self.assertEqual(
                hydrated["platform_payloads"]["xiaohongshu"]["cover_path"],
                cover_xhs,
            )


if __name__ == "__main__":
    unittest.main()
