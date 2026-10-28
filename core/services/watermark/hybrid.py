from core.utils.logger import log

from .base import BaseWatermarkService, WatermarkResult
from .local_rules_based import LocalRulesBasedWatermarkService
from .reverse_alpha import ReverseAlphaWatermarkService


class HybridWatermarkService(BaseWatermarkService):
    """
    Preferred strategy:
    1) reverse_alpha (precise, low-damage)
    2) local_rules inpaint fallback (robust on edge cases)
    """

    def __init__(self):
        self.primary = ReverseAlphaWatermarkService()
        self.fallback = LocalRulesBasedWatermarkService()

    def remove_watermark(self, image_path: str, output_path: str = None) -> WatermarkResult:
        primary_result = self.primary.remove_watermark(image_path, output_path)
        if primary_result.success:
            return primary_result

        log.info(
            f"🔁 Hybrid watermark: fallback to local_rules "
            f"(primary={primary_result.status}, msg={primary_result.message})"
        )
        fallback_result = self.fallback.remove_watermark(image_path, output_path)

        if fallback_result.success:
            # Keep primary diagnostics in message for troubleshooting if needed.
            fallback_result.message = (
                f"{fallback_result.message}; primary={primary_result.status}: {primary_result.message}"
            )
            return fallback_result

        # Both failed or not found; prefer more explicit status.
        if primary_result.status == "error" and fallback_result.status != "removed":
            return WatermarkResult(
                status=fallback_result.status,
                message=(
                    f"primary error={primary_result.message}; "
                    f"fallback={fallback_result.status}: {fallback_result.message}"
                ),
                method="hybrid"
            )

        if primary_result.status == "not_found" and fallback_result.status == "not_found":
            return WatermarkResult(
                status="not_found",
                message=(
                    f"primary not_found={primary_result.message}; "
                    f"fallback not_found={fallback_result.message}"
                ),
                method="hybrid"
            )

        return WatermarkResult(
            status=fallback_result.status,
            message=(
                f"primary={primary_result.status}: {primary_result.message}; "
                f"fallback={fallback_result.status}: {fallback_result.message}"
            ),
            method="hybrid"
        )
