import os
import tempfile
import unittest

from core.modules.uploader import validate_upload_assets


class TestUploadAssetValidation(unittest.TestCase):
    def _write_file(self, path: str, size: int) -> str:
        with open(path, "wb") as f:
            f.write(b"x" * size)
        return path

    def _make_metadata(self, tmpdir: str) -> dict:
        return {
            "video_path": self._write_file(os.path.join(tmpdir, "video.mp4"), 2 * 1024 * 1024),
            "title": "Demo title",
            "description": "Demo description",
            "tags": ["tag1", "tag2"],
            "cover_path_16_9": self._write_file(os.path.join(tmpdir, "cover_16_9.jpg"), 4096),
            "cover_path_4_3": self._write_file(os.path.join(tmpdir, "cover_4_3.jpg"), 4096),
        }

    def test_validate_upload_assets_requires_4x3_for_bilibili(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = self._make_metadata(tmpdir)
            os.remove(metadata["cover_path_4_3"])

            is_valid, missing = validate_upload_assets(metadata, targets=["bilibili"])

        self.assertFalse(is_valid)
        self.assertIn(
            "4:3 Cover (Not found: {})".format(metadata["cover_path_4_3"]),
            missing,
        )

    def test_validate_upload_assets_skips_4x3_for_non_bilibili_targets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = self._make_metadata(tmpdir)
            os.remove(metadata["cover_path_4_3"])

            is_valid, missing = validate_upload_assets(
                metadata,
                targets=["douyin", "kuaishou", "wechat_channels", "xiaohongshu"],
            )

        self.assertTrue(is_valid)
        self.assertEqual(missing, [])

    def test_validate_upload_assets_detects_missing_platform_specific_cover_asset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = self._make_metadata(tmpdir)
            metadata["cover_assets"] = {
                "platforms": {
                    "xiaohongshu": {
                        "path": os.path.join(tmpdir, "missing_xhs_cover.jpg"),
                    }
                }
            }

            is_valid, missing = validate_upload_assets(
                metadata,
                targets=["xiaohongshu"],
            )

        self.assertFalse(is_valid)
        self.assertIn(
            f"xiaohongshu Cover (Not found: {os.path.join(tmpdir, 'missing_xhs_cover.jpg')})",
            missing,
        )

    def test_validate_upload_assets_allows_failed_platform_specific_cover_asset_to_fall_back(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = self._make_metadata(tmpdir)
            metadata["cover_assets"] = {
                "platforms": {
                    "xiaohongshu": {
                        "path": os.path.join(tmpdir, "missing_xhs_cover.jpg"),
                        "status": "failed",
                    }
                }
            }

            is_valid, missing = validate_upload_assets(
                metadata,
                targets=["xiaohongshu"],
            )

        self.assertTrue(is_valid)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
