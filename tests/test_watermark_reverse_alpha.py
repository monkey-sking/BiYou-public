import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from core.services.watermark.reverse_alpha import ReverseAlphaWatermarkService


class TestReverseAlphaWatermarkService(unittest.TestCase):
    def setUp(self):
        self.service = ReverseAlphaWatermarkService()

    def _build_base_image(self, width: int, height: int) -> np.ndarray:
        xx = np.arange(width, dtype=np.float32)[None, :]
        yy = np.arange(height, dtype=np.float32)[:, None]
        base = np.zeros((height, width, 3), dtype=np.float32)
        base[:, :, 0] = 72 + 0.10 * xx + 0.05 * yy + 14 * np.sin(xx / 17.0) + 8 * np.cos(yy / 13.0)
        base[:, :, 1] = 88 + 0.08 * xx + 0.03 * yy + 10 * np.sin((xx + yy) / 21.0)
        base[:, :, 2] = 118 + 0.06 * xx + 0.02 * yy + 12 * np.cos(xx / 19.0)
        return np.clip(np.rint(base), 0, 255).astype(np.uint8)

    def _overlay_watermark(self, image: np.ndarray, size: int, margin_r: int, margin_b: int):
        alpha_map = self.service._alpha_map_for_size(size)
        out = image.astype(np.float32).copy()
        height, width = image.shape[:2]
        x = width - margin_r - size
        y = height - margin_b - size
        roi = out[y:y + size, x:x + size, :]
        out[y:y + size, x:x + size, :] = roi * (1.0 - alpha_map[:, :, None]) + 255.0 * alpha_map[:, :, None]
        return np.clip(np.rint(out), 0, 255).astype(np.uint8), (x, y, size)

    @staticmethod
    def _region_mae(lhs: np.ndarray, rhs: np.ndarray, x: int, y: int, size: int) -> float:
        lhs_region = lhs[y:y + size, x:x + size, :3].astype(np.float32)
        rhs_region = rhs[y:y + size, x:x + size, :3].astype(np.float32)
        return float(np.abs(lhs_region - rhs_region).mean())

    def _run_service(self, watermarked: np.ndarray):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.png"
            output_path = Path(tmpdir) / "output.png"
            Image.fromarray(watermarked, mode="RGB").save(input_path)
            result = self.service.remove_watermark(str(input_path), str(output_path))
            processed = np.asarray(Image.open(output_path).convert("RGB"), dtype=np.uint8) if output_path.exists() else None
        return result, processed

    def test_remove_standard_48px_watermark(self):
        base = self._build_base_image(900, 900)
        watermarked, (x, y, size) = self._overlay_watermark(base, size=48, margin_r=32, margin_b=32)

        result, processed = self._run_service(watermarked)

        self.assertEqual(result.status, "removed")
        self.assertIsNotNone(processed)
        before_mae = self._region_mae(watermarked, base, x, y, size)
        after_mae = self._region_mae(processed, base, x, y, size)
        self.assertLess(after_mae, before_mae * 0.35)
        self.assertLess(after_mae, 4.0)

    def test_remove_nonstandard_size_and_offset_watermark(self):
        base = self._build_base_image(900, 900)
        watermarked, (x, y, size) = self._overlay_watermark(base, size=60, margin_r=40, margin_b=38)

        result, processed = self._run_service(watermarked)

        self.assertEqual(result.status, "removed")
        self.assertIsNotNone(processed)
        before_mae = self._region_mae(watermarked, base, x, y, size)
        after_mae = self._region_mae(processed, base, x, y, size)
        self.assertLess(after_mae, before_mae * 0.45)
        self.assertLess(after_mae, 8.0)

    def test_skip_clean_image(self):
        base = self._build_base_image(900, 900)

        result, processed = self._run_service(base)

        self.assertEqual(result.status, "not_found")
        self.assertIsNone(processed)


if __name__ == "__main__":
    unittest.main()
