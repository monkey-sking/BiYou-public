from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class WatermarkResult:
    status: str  # removed | not_found | error
    message: str = ""
    method: str = ""
    score_before: Optional[float] = None
    score_after: Optional[float] = None
    alpha_gain: float = 1.0

    @property
    def success(self) -> bool:
        return self.status == "removed"


class BaseWatermarkService(ABC):
    @abstractmethod
    def remove_watermark(self, image_path: str, output_path: str = None) -> WatermarkResult:
        """
        Remove watermark from the image.
        :param image_path: Path to input image
        :param output_path: Path to save result (None = overwrite)
        :return: WatermarkResult
        """
        pass
