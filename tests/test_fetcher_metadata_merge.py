import unittest

from core.modules.fetcher import preserve_existing_processing_artifacts


class TestFetcherMetadataMerge(unittest.TestCase):
    def test_preserve_existing_processing_artifacts_keeps_publish_records_and_cover_assets(self):
        existing = {
            "publish_records": {
                "bilibili": {
                    "status": "published",
                    "platform_content_id": "BV1xx",
                }
            },
            "uploaded_bvid": "BV1xx",
            "uploaded_aid": 123,
            "cover_path_16_9": "/tmp/cover_16x9.jpg",
            "cover_path_4_3": "/tmp/cover_4x3.jpg",
            "cover_path": "/tmp/cover_16x9.jpg",
            "cover_assets": {
                "platforms": {
                    "xiaohongshu": {
                        "path": "/tmp/cover_xhs.jpg",
                        "status": "reused_existing",
                    }
                }
            },
            "platform_payloads": {
                "xiaohongshu": {
                    "cover_path": "/tmp/cover_xhs.jpg",
                }
            },
        }
        refreshed = {
            "title": "新标题",
            "description": "新描述",
        }

        merged = preserve_existing_processing_artifacts(existing, refreshed)

        self.assertEqual(
            merged["publish_records"]["bilibili"]["platform_content_id"],
            "BV1xx",
        )
        self.assertEqual(merged["uploaded_bvid"], "BV1xx")
        self.assertEqual(merged["uploaded_aid"], 123)
        self.assertEqual(merged["cover_path_16_9"], "/tmp/cover_16x9.jpg")
        self.assertEqual(merged["cover_path_4_3"], "/tmp/cover_4x3.jpg")
        self.assertEqual(
            merged["cover_assets"]["platforms"]["xiaohongshu"]["path"],
            "/tmp/cover_xhs.jpg",
        )
        self.assertEqual(
            merged["platform_payloads"]["xiaohongshu"]["cover_path"],
            "/tmp/cover_xhs.jpg",
        )


if __name__ == "__main__":
    unittest.main()
