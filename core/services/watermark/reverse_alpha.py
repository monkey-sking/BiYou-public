import base64
import io
from dataclasses import dataclass, replace
from typing import Dict, Optional, Tuple

import numpy as np
from PIL import Image

from core.utils.logger import log
from .base import BaseWatermarkService, WatermarkResult
from .masks import BG_48_B64, BG_96_B64


@dataclass
class _CandidateConfig:
    size: int
    margin_r: int
    margin_b: int
    x: int
    y: int
    alpha_map: np.ndarray
    alpha_grad: np.ndarray
    source: str = "standard"
    confidence: Optional[float] = None
    spatial_score: Optional[float] = None
    gradient_score: Optional[float] = None


@dataclass
class _CandidateEvaluation:
    candidate: _CandidateConfig
    image_rgb: np.ndarray
    accepted: bool
    direct_match: bool
    score_before: float
    score_after: float
    gradient_before: float
    gradient_after: float
    improvement: float
    gradient_increase: float
    near_black_ratio: float
    near_black_increase: float
    texture_penalty: float
    hard_reject: bool
    alpha_gain: float
    validation_cost: float


class ReverseAlphaWatermarkService(BaseWatermarkService):
    """
    Gemini watermark remover based on reverse alpha blending:
    original = (watermarked - alpha * 255) / (1 - alpha)
    """

    ALPHA_NOISE_FLOOR = 3.0 / 255.0
    ALPHA_THRESHOLD = 0.002
    MAX_ALPHA = 0.99

    STANDARD_DIRECT_MATCH_MIN_SPATIAL_SCORE = 0.30
    STANDARD_DIRECT_MATCH_MIN_GRADIENT_SCORE = 0.12
    ADAPTIVE_DIRECT_MATCH_MIN_CONFIDENCE = 0.50
    ADAPTIVE_DIRECT_MATCH_MIN_SPATIAL_SCORE = 0.45
    ADAPTIVE_DIRECT_MATCH_MIN_GRADIENT_SCORE = 0.12
    ADAPTIVE_DIRECT_MATCH_MIN_SIZE = 40
    ADAPTIVE_DIRECT_MATCH_MAX_SIZE = 192
    ADAPTIVE_SEARCH_THRESHOLD = 0.18

    SWITCH_SCORE_THRESHOLD = 0.25
    SWITCH_SCORE_DELTA = 0.08
    VALIDATION_MIN_IMPROVEMENT = 0.08
    VALIDATION_TARGET_RESIDUAL = 0.22
    VALIDATION_MAX_GRADIENT_INCREASE = 0.04
    SEARCH_RESIDUAL_THRESHOLD = 0.08
    MAX_NEAR_BLACK_RATIO_INCREASE = 0.05
    TEXTURE_REFERENCE_MARGIN = 1.0
    TEXTURE_STD_FLOOR_RATIO = 0.80
    MIN_SUPPRESSION_GAIN = 0.12

    RESIDUAL_RECALIBRATION_THRESHOLD = 0.50
    MIN_SUPPRESSION_FOR_SKIP_RECALIBRATION = 0.18
    MIN_RECALIBRATION_SCORE_DELTA = 0.18
    MULTIPASS_RESIDUAL_THRESHOLD = 0.25
    MULTIPASS_MAX_PASSES = 4

    TEMPLATE_ALIGN_SHIFTS = (-0.5, -0.25, 0.0, 0.25, 0.5)
    TEMPLATE_ALIGN_SCALES = (0.99, 1.0, 1.01)
    STANDARD_NEARBY_SHIFTS = (-12, -8, -4, 0, 4, 8, 12)
    STANDARD_SIZE_JITTERS = (-12, -10, -8, -6, -4, -2, 2, 4, 6, 8, 10, 12)
    ALPHA_GAIN_CANDIDATES = (
        1.05, 1.12, 1.20, 1.28, 1.36, 1.45, 1.52, 1.60,
        1.70, 1.85, 2.00, 2.20, 2.40, 2.60
    )

    def __init__(self):
        self._alpha_cache: Dict[int, np.ndarray] = {}
        self._alpha_grad_cache: Dict[int, np.ndarray] = {}

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _decode_b64_png(data_uri: str) -> np.ndarray:
        payload = data_uri.split(",", 1)[1] if "," in data_uri else data_uri
        raw = base64.b64decode(payload)
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        return np.asarray(image, dtype=np.uint8)

    @staticmethod
    def _calc_alpha_map(capture_rgb: np.ndarray) -> np.ndarray:
        return capture_rgb.max(axis=2).astype(np.float32) / 255.0

    @staticmethod
    def _sobel_magnitude(gray: np.ndarray) -> np.ndarray:
        gray = gray.astype(np.float32, copy=False)
        h, w = gray.shape
        grad_x = np.zeros((h, w), dtype=np.float32)
        grad_y = np.zeros((h, w), dtype=np.float32)
        if h < 3 or w < 3:
            return grad_x
        grad_x[1:-1, 1:-1] = (
            -gray[:-2, :-2] - 2 * gray[1:-1, :-2] - gray[2:, :-2]
            + gray[:-2, 2:] + 2 * gray[1:-1, 2:] + gray[2:, 2:]
        )
        grad_y[1:-1, 1:-1] = (
            -gray[:-2, :-2] - 2 * gray[:-2, 1:-1] - gray[:-2, 2:]
            + gray[2:, :-2] + 2 * gray[2:, 1:-1] + gray[2:, 2:]
        )
        return np.sqrt(grad_x * grad_x + grad_y * grad_y).astype(np.float32)

    def _ensure_alpha_maps(self):
        if 48 not in self._alpha_cache:
            self._alpha_cache[48] = self._calc_alpha_map(self._decode_b64_png(BG_48_B64))
            self._alpha_grad_cache[48] = self._sobel_magnitude(self._alpha_cache[48])
        if 96 not in self._alpha_cache:
            self._alpha_cache[96] = self._calc_alpha_map(self._decode_b64_png(BG_96_B64))
            self._alpha_grad_cache[96] = self._sobel_magnitude(self._alpha_cache[96])

    @staticmethod
    def _detect_default_config(width: int, height: int) -> Tuple[int, int, int]:
        if width > 1024 and height > 1024:
            return 96, 64, 64
        return 48, 32, 32

    @staticmethod
    def _position(width: int, height: int, size: int, margin_r: int, margin_b: int):
        return width - margin_r - size, height - margin_b - size, size, size

    @staticmethod
    def _is_inside(width: int, height: int, x: int, y: int, size: int) -> bool:
        return x >= 0 and y >= 0 and (x + size) <= width and (y + size) <= height

    @staticmethod
    def _to_gray(image_rgb: np.ndarray) -> np.ndarray:
        image_rgb = image_rgb.astype(np.float32, copy=False)
        return (
            0.2126 * image_rgb[:, :, 0]
            + 0.7152 * image_rgb[:, :, 1]
            + 0.0722 * image_rgb[:, :, 2]
        ) / 255.0

    @staticmethod
    def _region_gray(image_rgb: np.ndarray, x: int, y: int, size: int) -> np.ndarray:
        region = image_rgb[y:y + size, x:x + size, :3].astype(np.float32)
        return (
            0.2126 * region[:, :, 0]
            + 0.7152 * region[:, :, 1]
            + 0.0722 * region[:, :, 2]
        ) / 255.0

    @staticmethod
    def _region_gray_rect(image_rgb: np.ndarray, x: int, y: int, width: int, height: int) -> np.ndarray:
        region = image_rgb[y:y + height, x:x + width, :3].astype(np.float32)
        return (
            0.2126 * region[:, :, 0]
            + 0.7152 * region[:, :, 1]
            + 0.0722 * region[:, :, 2]
        ) / 255.0

    @staticmethod
    def _ncc(a: np.ndarray, b: np.ndarray) -> float:
        if a.shape != b.shape or a.size == 0:
            return 0.0
        av = a.reshape(-1).astype(np.float32)
        bv = b.reshape(-1).astype(np.float32)
        am = float(av.mean())
        bm = float(bv.mean())
        ac = av - am
        bc = bv - bm
        denom = float(np.sqrt((ac * ac).mean() * (bc * bc).mean()))
        if denom < 1e-8:
            return 0.0
        return float((ac * bc).mean() / denom)

    @staticmethod
    def _sample_bilinear(data: np.ndarray, x: float, y: float) -> float:
        size = data.shape[0]
        x0 = int(np.floor(x))
        y0 = int(np.floor(y))
        fx = x - x0
        fy = y - y0
        ix0 = int(max(0, min(size - 1, x0)))
        iy0 = int(max(0, min(size - 1, y0)))
        ix1 = int(max(0, min(size - 1, x0 + 1)))
        iy1 = int(max(0, min(size - 1, y0 + 1)))
        p00 = float(data[iy0, ix0])
        p10 = float(data[iy0, ix1])
        p01 = float(data[iy1, ix0])
        p11 = float(data[iy1, ix1])
        top = p00 + (p10 - p00) * fx
        bottom = p01 + (p11 - p01) * fx
        return top + (bottom - top) * fy

    def _interpolate_alpha_map(self, source_alpha: np.ndarray, source_size: int, target_size: int) -> np.ndarray:
        if target_size <= 0:
            return np.zeros((0, 0), dtype=np.float32)
        if source_size == target_size:
            return source_alpha.copy()
        out = np.zeros((target_size, target_size), dtype=np.float32)
        scale = (source_size - 1) / max(1, target_size - 1)
        for y in range(target_size):
            sy = y * scale
            for x in range(target_size):
                sx = x * scale
                out[y, x] = self._sample_bilinear(source_alpha, sx, sy)
        return out

    def _warp_alpha_map(self, alpha_map: np.ndarray, size: int, dx: float = 0.0, dy: float = 0.0, scale: float = 1.0) -> np.ndarray:
        if size <= 0 or not np.isfinite(dx) or not np.isfinite(dy) or not np.isfinite(scale) or scale <= 0:
            return np.zeros((0, 0), dtype=np.float32)
        if dx == 0 and dy == 0 and scale == 1:
            return alpha_map.copy()
        out = np.zeros((size, size), dtype=np.float32)
        center = (size - 1) / 2.0
        for y in range(size):
            for x in range(size):
                sx = (x - center) / scale + center + dx
                sy = (y - center) / scale + center + dy
                out[y, x] = self._sample_bilinear(alpha_map, sx, sy)
        return out

    def _alpha_map_for_size(self, size: int) -> np.ndarray:
        self._ensure_alpha_maps()
        if size not in self._alpha_cache:
            self._alpha_cache[size] = self._interpolate_alpha_map(self._alpha_cache[96], 96, size)
            self._alpha_grad_cache[size] = self._sobel_magnitude(self._alpha_cache[size])
        return self._alpha_cache[size]

    def _alpha_grad_for_size(self, size: int) -> np.ndarray:
        self._alpha_map_for_size(size)
        return self._alpha_grad_cache[size]

    def _score_region(self, image_rgb: np.ndarray, alpha_map: np.ndarray, alpha_grad: np.ndarray, x: int, y: int, size: int):
        gray = self._region_gray(image_rgb, x, y, size)
        spatial = self._ncc(gray, alpha_map)
        gradient = self._ncc(self._sobel_magnitude(gray), alpha_grad)
        return spatial, gradient

    @staticmethod
    def _calculate_variance_score(gray_image: np.ndarray, x: int, y: int, size: int) -> float:
        if y <= 8:
            return 0.0
        ref_y = max(0, y - size)
        ref_h = min(size, y - ref_y)
        if ref_h <= 8:
            return 0.0
        wm_std = float(gray_image[y:y + size, x:x + size].std())
        ref_std = float(gray_image[ref_y:ref_y + ref_h, x:x + size].std())
        if ref_std <= 1e-6:
            return 0.0
        return float(max(0.0, min(1.0, 1.0 - wm_std / ref_std)))

    def _score_search_candidate(self, gray_image: np.ndarray, grad_image: np.ndarray, x: int, y: int, size: int):
        if y < 0 or x < 0 or y + size > gray_image.shape[0] or x + size > gray_image.shape[1]:
            return None
        alpha_map = self._alpha_map_for_size(size)
        alpha_grad = self._alpha_grad_for_size(size)
        gray_region = gray_image[y:y + size, x:x + size]
        grad_region = grad_image[y:y + size, x:x + size]
        spatial = self._ncc(gray_region, alpha_map)
        gradient = self._ncc(grad_region, alpha_grad)
        variance = self._calculate_variance_score(gray_image, x, y, size)
        confidence = max(0.0, spatial) * 0.5 + max(0.0, gradient) * 0.3 + variance * 0.2
        return {
            "confidence": float(max(0.0, min(1.0, confidence))),
            "spatial": spatial,
            "gradient": gradient,
        }

    @staticmethod
    def _calculate_near_black_ratio(image_rgb: np.ndarray, x: int, y: int, size: int) -> float:
        region = image_rgb[y:y + size, x:x + size, :3]
        if region.size == 0:
            return 0.0
        near_black = np.logical_and.reduce((region[:, :, 0] <= 5, region[:, :, 1] <= 5, region[:, :, 2] <= 5))
        return float(near_black.mean())

    def _assess_reference_texture(self, original_rgb: np.ndarray, candidate_rgb: np.ndarray, x: int, y: int, size: int):
        ref_y = y - size
        if ref_y < 0:
            return 0.0, False, False, False
        ref = self._region_gray_rect(original_rgb, x, ref_y, size, size)
        cand = self._region_gray(candidate_rgb, x, y, size)
        ref_mean = float(ref.mean() * 255.0)
        ref_std = float(ref.std() * 255.0)
        cand_mean = float(cand.mean() * 255.0)
        cand_std = float(cand.std() * 255.0)
        darkness_penalty = max(0.0, ref_mean - cand_mean - self.TEXTURE_REFERENCE_MARGIN) / max(1.0, ref_mean)
        flatness_penalty = max(0.0, ref_std * self.TEXTURE_STD_FLOOR_RATIO - cand_std) / max(1.0, ref_std)
        too_dark = darkness_penalty > 0
        too_flat = flatness_penalty > 0
        texture_penalty = darkness_penalty * 2.0 + flatness_penalty * 2.0
        return texture_penalty, too_dark, too_flat, too_dark and too_flat

    def _build_candidate(self, width: int, height: int, size: int, margin_r: int, margin_b: int, source: str = "standard"):
        x, y, _, _ = self._position(width, height, size, margin_r, margin_b)
        if not self._is_inside(width, height, x, y, size):
            return None
        return _CandidateConfig(
            size=size,
            margin_r=margin_r,
            margin_b=margin_b,
            x=x,
            y=y,
            alpha_map=self._alpha_map_for_size(size),
            alpha_grad=self._alpha_grad_for_size(size),
            source=source,
        )

    def _resolve_initial_standard_seeds(self, image_rgb: np.ndarray):
        height, width = image_rgb.shape[:2]
        default_size, default_mr, default_mb = self._detect_default_config(width, height)
        primary = self._build_candidate(width, height, default_size, default_mr, default_mb, "standard")
        if default_size == 96:
            alternate = self._build_candidate(width, height, 48, 32, 32, "standard+catalog")
        else:
            alternate = self._build_candidate(width, height, 96, 64, 64, "standard+catalog")
        if not primary:
            return [candidate for candidate in (alternate,) if candidate]
        primary_spatial, _ = self._score_region(
            image_rgb, primary.alpha_map, primary.alpha_grad, primary.x, primary.y, primary.size
        )
        if not alternate:
            return [primary]
        alternate_spatial, _ = self._score_region(
            image_rgb, alternate.alpha_map, alternate.alpha_grad, alternate.x, alternate.y, alternate.size
        )
        should_switch = alternate_spatial >= self.SWITCH_SCORE_THRESHOLD and alternate_spatial > (primary_spatial + self.SWITCH_SCORE_DELTA)
        if should_switch:
            primary, alternate = alternate, primary
            primary.source = "standard"
            alternate.source = "standard+catalog"
        else:
            primary.source = "standard"
            alternate.source = "standard+catalog"
        return [candidate for candidate in (primary, alternate) if candidate]

    def _search_adaptive_candidate(self, image_rgb: np.ndarray, default_candidate: _CandidateConfig):
        height, width = image_rgb.shape[:2]
        gray_image = self._to_gray(image_rgb)
        grad_image = self._sobel_magnitude(gray_image)
        seed_candidates = []
        for seed in self._resolve_initial_standard_seeds(image_rgb):
            score = self._score_search_candidate(gray_image, grad_image, seed.x, seed.y, seed.size)
            if not score:
                continue
            seed_candidates.append({
                "x": seed.x,
                "y": seed.y,
                "size": seed.size,
                "confidence": score["confidence"],
                "spatial": score["spatial"],
                "gradient": score["gradient"],
            })

        best_seed = None
        for candidate in seed_candidates:
            if best_seed is None or candidate["confidence"] > best_seed["confidence"]:
                best_seed = candidate

        if best_seed and best_seed["confidence"] >= self.ADAPTIVE_SEARCH_THRESHOLD + 0.08:
            return self._candidate_from_search_result(width, height, best_seed, "adaptive")

        base_size = default_candidate.size
        min_size = int(self._clamp(round(base_size * 0.65), 24, 144))
        max_size = int(self._clamp(min(round(base_size * 2.8), int(min(width, height) * 0.4)), min_size, 192))
        size_list = sorted(set(range(min_size, max_size + 1, 8)) | ({48} if min_size <= 48 <= max_size else set()) | ({96} if min_size <= 96 <= max_size else set()))
        margin_range = max(32, round(base_size * 0.75))
        min_margin_r = int(self._clamp(default_candidate.margin_r - margin_range, 8, max(8, width - min_size - 1)))
        max_margin_r = int(self._clamp(default_candidate.margin_r + margin_range, min_margin_r, max(8, width - min_size - 1)))
        min_margin_b = int(self._clamp(default_candidate.margin_b - margin_range, 8, max(8, height - min_size - 1)))
        max_margin_b = int(self._clamp(default_candidate.margin_b + margin_range, min_margin_b, max(8, height - min_size - 1)))

        top_candidates = []

        def push_top(candidate_dict):
            top_candidates.append(candidate_dict)
            top_candidates.sort(key=lambda item: item["adjusted_score"], reverse=True)
            del top_candidates[5:]

        for candidate in seed_candidates:
            push_top({
                "x": candidate["x"],
                "y": candidate["y"],
                "size": candidate["size"],
                "adjusted_score": candidate["confidence"] * min(1.0, np.sqrt(candidate["size"] / 96.0)),
            })

        for size in size_list:
            for margin_r in range(min_margin_r, max_margin_r + 1, 8):
                x = width - margin_r - size
                if x < 0:
                    continue
                for margin_b in range(min_margin_b, max_margin_b + 1, 8):
                    y = height - margin_b - size
                    if y < 0:
                        continue
                    score = self._score_search_candidate(gray_image, grad_image, x, y, size)
                    if not score:
                        continue
                    adjusted = score["confidence"] * min(1.0, np.sqrt(size / 96.0))
                    if adjusted < 0.08:
                        continue
                    push_top({"x": x, "y": y, "size": size, "adjusted_score": adjusted})

        best = best_seed or {
            "x": default_candidate.x,
            "y": default_candidate.y,
            "size": default_candidate.size,
            "confidence": 0.0,
            "spatial": 0.0,
            "gradient": 0.0,
        }
        for coarse in top_candidates:
            scale_lo = int(self._clamp(coarse["size"] - 10, min_size, max_size))
            scale_hi = int(self._clamp(coarse["size"] + 10, min_size, max_size))
            for size in range(scale_lo, scale_hi + 1, 2):
                for x in range(coarse["x"] - 8, coarse["x"] + 9, 2):
                    if x < 0 or x + size > width:
                        continue
                    for y in range(coarse["y"] - 8, coarse["y"] + 9, 2):
                        if y < 0 or y + size > height:
                            continue
                        score = self._score_search_candidate(gray_image, grad_image, x, y, size)
                        if score and score["confidence"] > best["confidence"]:
                            best = {
                                "x": x,
                                "y": y,
                                "size": size,
                                "confidence": score["confidence"],
                                "spatial": score["spatial"],
                                "gradient": score["gradient"],
                            }

        if best["confidence"] < self.ADAPTIVE_SEARCH_THRESHOLD:
            return None
        return self._candidate_from_search_result(width, height, best, "adaptive")

    def _candidate_from_search_result(self, width: int, height: int, result: dict, source: str):
        size = int(result["size"])
        x = int(result["x"])
        y = int(result["y"])
        candidate = _CandidateConfig(
            size=size,
            margin_r=width - x - size,
            margin_b=height - y - size,
            x=x,
            y=y,
            alpha_map=self._alpha_map_for_size(size),
            alpha_grad=self._alpha_grad_for_size(size),
            source=source,
            confidence=float(result.get("confidence", 0.0)),
            spatial_score=float(result.get("spatial", 0.0)),
            gradient_score=float(result.get("gradient", 0.0)),
        )
        return candidate

    def _is_reliable_standard_match(self, spatial: float, gradient: float) -> bool:
        return (
            spatial >= self.STANDARD_DIRECT_MATCH_MIN_SPATIAL_SCORE
            and gradient >= self.STANDARD_DIRECT_MATCH_MIN_GRADIENT_SCORE
        )

    def _is_reliable_adaptive_match(self, candidate: _CandidateConfig, spatial: float, gradient: float) -> bool:
        confidence = candidate.confidence
        if confidence is None:
            return False
        return (
            confidence >= self.ADAPTIVE_DIRECT_MATCH_MIN_CONFIDENCE
            and spatial >= self.ADAPTIVE_DIRECT_MATCH_MIN_SPATIAL_SCORE
            and gradient >= self.ADAPTIVE_DIRECT_MATCH_MIN_GRADIENT_SCORE
            and self.ADAPTIVE_DIRECT_MATCH_MIN_SIZE <= candidate.size <= self.ADAPTIVE_DIRECT_MATCH_MAX_SIZE
        )

    def _apply_reverse_alpha(self, image_rgb: np.ndarray, x: int, y: int, alpha_map: np.ndarray, gain: float) -> np.ndarray:
        size = alpha_map.shape[0]
        out = image_rgb.copy()
        roi = out[y:y + size, x:x + size, :3].astype(np.float32)
        alpha_signal = np.maximum(0.0, alpha_map - self.ALPHA_NOISE_FLOOR) * gain
        active_mask = alpha_signal >= self.ALPHA_THRESHOLD
        alpha = np.minimum(alpha_map * gain, self.MAX_ALPHA).astype(np.float32)
        one_minus = np.where((1.0 - alpha) <= 1e-6, 1e-6, 1.0 - alpha)
        restored = (roi - alpha[:, :, None] * 255.0) / one_minus[:, :, None]
        restored = np.clip(np.rint(restored), 0, 255).astype(np.uint8)
        if active_mask.any():
            roi_uint8 = roi.astype(np.uint8)
            roi_uint8[active_mask] = restored[active_mask]
            out[y:y + size, x:x + size, :3] = roi_uint8
        return out

    def _evaluate_candidate(self, original_rgb: np.ndarray, candidate: _CandidateConfig, alpha_gain: float = 1.0) -> _CandidateEvaluation:
        baseline_near_black = self._calculate_near_black_ratio(original_rgb, candidate.x, candidate.y, candidate.size)
        score_before, gradient_before = self._score_region(
            original_rgb, candidate.alpha_map, candidate.alpha_grad, candidate.x, candidate.y, candidate.size
        )
        candidate_rgb = self._apply_reverse_alpha(
            original_rgb, candidate.x, candidate.y, candidate.alpha_map, alpha_gain
        )
        score_after, gradient_after = self._score_region(
            candidate_rgb, candidate.alpha_map, candidate.alpha_grad, candidate.x, candidate.y, candidate.size
        )
        near_black_ratio = self._calculate_near_black_ratio(candidate_rgb, candidate.x, candidate.y, candidate.size)
        near_black_increase = near_black_ratio - baseline_near_black
        improvement = score_before - score_after
        gradient_increase = gradient_after - gradient_before
        texture_penalty, too_dark, too_flat, hard_reject = self._assess_reference_texture(
            original_rgb, candidate_rgb, candidate.x, candidate.y, candidate.size
        )
        accepted = (
            not hard_reject
            and near_black_increase <= self.MAX_NEAR_BLACK_RATIO_INCREASE
            and improvement >= self.VALIDATION_MIN_IMPROVEMENT
            and (
                abs(score_after) <= self.VALIDATION_TARGET_RESIDUAL
                or gradient_increase <= self.VALIDATION_MAX_GRADIENT_INCREASE
            )
        )
        direct_match = (
            self._is_reliable_adaptive_match(candidate, score_before, gradient_before)
            if "adaptive" in candidate.source
            else self._is_reliable_standard_match(score_before, gradient_before)
        )
        validation_cost = (
            abs(score_after)
            + max(0.0, gradient_after) * 0.6
            + max(0.0, near_black_increase) * 3.0
            + texture_penalty
        )
        return _CandidateEvaluation(
            candidate=candidate,
            image_rgb=candidate_rgb,
            accepted=accepted,
            direct_match=direct_match,
            score_before=score_before,
            score_after=score_after,
            gradient_before=gradient_before,
            gradient_after=gradient_after,
            improvement=improvement,
            gradient_increase=gradient_increase,
            near_black_ratio=near_black_ratio,
            near_black_increase=near_black_increase,
            texture_penalty=texture_penalty,
            hard_reject=hard_reject,
            alpha_gain=alpha_gain,
            validation_cost=validation_cost,
        )

    @staticmethod
    def _pick_better_candidate(current: Optional[_CandidateEvaluation], candidate: Optional[_CandidateEvaluation], min_cost_delta: float = 0.005):
        if candidate is None or not candidate.accepted:
            return current
        if current is None or not current.accepted:
            return candidate
        if candidate.validation_cost < current.validation_cost - min_cost_delta:
            return candidate
        if (
            abs(candidate.validation_cost - current.validation_cost) <= min_cost_delta
            and candidate.improvement > current.improvement + 0.01
        ):
            return candidate
        return current

    def _should_escalate_search(self, evaluation: Optional[_CandidateEvaluation]) -> bool:
        if evaluation is None:
            return True
        return (
            abs(evaluation.score_after) > self.VALIDATION_TARGET_RESIDUAL
            or max(0.0, evaluation.gradient_after) > self.SEARCH_RESIDUAL_THRESHOLD
        )

    def _search_standard_size_jitter(self, original_rgb: np.ndarray, seeds):
        height, width = original_rgb.shape[:2]
        best = None
        for seed in seeds:
            for delta in self.STANDARD_SIZE_JITTERS:
                size = seed.size + delta
                if size <= 24 or size == seed.size:
                    continue
                x, y, _, _ = self._position(width, height, size, seed.margin_r, seed.margin_b)
                if not self._is_inside(width, height, x, y, size):
                    continue
                candidate = _CandidateConfig(
                    size=size,
                    margin_r=seed.margin_r,
                    margin_b=seed.margin_b,
                    x=x,
                    y=y,
                    alpha_map=self._alpha_map_for_size(size),
                    alpha_grad=self._alpha_grad_for_size(size),
                    source=f"{seed.source}+size",
                )
                best = self._pick_better_candidate(best, self._evaluate_candidate(original_rgb, candidate), 0.002)
        return best

    def _search_nearby_standard(self, original_rgb: np.ndarray, seeds):
        height, width = original_rgb.shape[:2]
        best = None
        for seed in seeds:
            for dy in self.STANDARD_NEARBY_SHIFTS:
                for dx in self.STANDARD_NEARBY_SHIFTS:
                    if dx == 0 and dy == 0:
                        continue
                    x = seed.x + dx
                    y = seed.y + dy
                    if not self._is_inside(width, height, x, y, seed.size):
                        continue
                    candidate = replace(seed, x=x, y=y, source=f"{seed.source}+local")
                    best = self._pick_better_candidate(best, self._evaluate_candidate(original_rgb, candidate), 0.002)
        return best

    def _find_best_template_warp(self, original_rgb: np.ndarray, evaluation: _CandidateEvaluation):
        size = evaluation.candidate.size
        if size <= 8:
            return None
        best_spatial = evaluation.score_before
        best_gradient = evaluation.gradient_before
        best_alpha = None
        best_grad = None
        best_shift = None
        best_confidence = max(0.0, best_spatial) * 0.7 + max(0.0, best_gradient) * 0.3
        for scale in self.TEMPLATE_ALIGN_SCALES:
            for dy in self.TEMPLATE_ALIGN_SHIFTS:
                for dx in self.TEMPLATE_ALIGN_SHIFTS:
                    if dx == 0 and dy == 0 and scale == 1.0:
                        continue
                    warped = self._warp_alpha_map(evaluation.candidate.alpha_map, size, dx=dx, dy=dy, scale=scale)
                    warped_grad = self._sobel_magnitude(warped)
                    spatial, gradient = self._score_region(
                        original_rgb, warped, warped_grad, evaluation.candidate.x, evaluation.candidate.y, size
                    )
                    confidence = max(0.0, spatial) * 0.7 + max(0.0, gradient) * 0.3
                    if confidence > best_confidence + 0.01:
                        best_confidence = confidence
                        best_spatial = spatial
                        best_gradient = gradient
                        best_alpha = warped
                        best_grad = warped_grad
                        best_shift = {"dx": dx, "dy": dy, "scale": scale}

        if best_alpha is None:
            return None
        if best_spatial < evaluation.score_before + 0.01 and best_gradient < evaluation.gradient_before + 0.01:
            return None
        warped_candidate = replace(
            evaluation.candidate,
            alpha_map=best_alpha,
            alpha_grad=best_grad,
            source=f"{evaluation.candidate.source}+warp",
        )
        warped_eval = self._evaluate_candidate(original_rgb, warped_candidate, alpha_gain=evaluation.alpha_gain)
        return warped_eval, best_shift

    def _recalibrate_gain(self, original_rgb: np.ndarray, evaluation: _CandidateEvaluation):
        if not (
            evaluation.score_before >= 0.60
            and evaluation.score_after >= self.RESIDUAL_RECALIBRATION_THRESHOLD
            and evaluation.improvement <= self.MIN_SUPPRESSION_FOR_SKIP_RECALIBRATION
        ):
            return evaluation
        best = evaluation
        for gain in self.ALPHA_GAIN_CANDIDATES:
            trial = self._evaluate_candidate(original_rgb, evaluation.candidate, alpha_gain=gain)
            if trial.near_black_increase > self.MAX_NEAR_BLACK_RATIO_INCREASE:
                continue
            if trial.score_after < best.score_after:
                best = trial
        for gain in np.arange(max(1.01, best.alpha_gain - 0.05), min(2.99, best.alpha_gain + 0.051), 0.01):
            trial = self._evaluate_candidate(original_rgb, evaluation.candidate, alpha_gain=float(round(gain, 2)))
            if trial.near_black_increase > self.MAX_NEAR_BLACK_RATIO_INCREASE:
                continue
            if trial.score_after < best.score_after:
                best = trial
        if evaluation.score_after - best.score_after < self.MIN_RECALIBRATION_SCORE_DELTA:
            return evaluation
        return best

    def _apply_multipass(self, evaluation: _CandidateEvaluation):
        current = evaluation
        for _ in range(self.MULTIPASS_MAX_PASSES - 1):
            if abs(current.score_after) <= self.MULTIPASS_RESIDUAL_THRESHOLD:
                break
            baseline_near_black = self._calculate_near_black_ratio(
                current.image_rgb,
                current.candidate.x,
                current.candidate.y,
                current.candidate.size,
            )
            next_rgb = self._apply_reverse_alpha(
                current.image_rgb,
                current.candidate.x,
                current.candidate.y,
                current.candidate.alpha_map,
                current.alpha_gain,
            )
            score_before, gradient_before = self._score_region(
                current.image_rgb,
                current.candidate.alpha_map,
                current.candidate.alpha_grad,
                current.candidate.x,
                current.candidate.y,
                current.candidate.size,
            )
            score_after, gradient_after = self._score_region(
                next_rgb,
                current.candidate.alpha_map,
                current.candidate.alpha_grad,
                current.candidate.x,
                current.candidate.y,
                current.candidate.size,
            )
            near_black_ratio = self._calculate_near_black_ratio(
                next_rgb,
                current.candidate.x,
                current.candidate.y,
                current.candidate.size,
            )
            near_black_increase = near_black_ratio - baseline_near_black
            texture_penalty, _, _, hard_reject = self._assess_reference_texture(
                current.image_rgb,
                next_rgb,
                current.candidate.x,
                current.candidate.y,
                current.candidate.size,
            )
            improvement = abs(score_before) - abs(score_after)
            if near_black_increase > self.MAX_NEAR_BLACK_RATIO_INCREASE or hard_reject or improvement <= 0.01:
                break
            current = _CandidateEvaluation(
                candidate=current.candidate,
                image_rgb=next_rgb,
                accepted=current.accepted,
                direct_match=current.direct_match,
                score_before=evaluation.score_before,
                score_after=score_after,
                gradient_before=evaluation.gradient_before,
                gradient_after=gradient_after,
                improvement=evaluation.score_before - score_after,
                gradient_increase=gradient_after - evaluation.gradient_before,
                near_black_ratio=near_black_ratio,
                near_black_increase=current.near_black_increase + max(0.0, near_black_increase),
                texture_penalty=max(current.texture_penalty, texture_penalty),
                hard_reject=hard_reject,
                alpha_gain=current.alpha_gain,
                validation_cost=abs(score_after) + max(0.0, gradient_after) * 0.6 + max(0.0, current.near_black_increase) * 3.0,
            )
        return current

    def _select_candidate(self, image_rgb: np.ndarray):
        seeds = self._resolve_initial_standard_seeds(image_rgb)
        if not seeds:
            return None

        base = None
        for seed in seeds:
            trial = self._evaluate_candidate(image_rgb, seed)
            if base is None and trial.direct_match:
                base = trial
            else:
                base = self._pick_better_candidate(base, trial)

        if self._should_escalate_search(base):
            size_jitter = self._search_standard_size_jitter(image_rgb, seeds)
            base = self._pick_better_candidate(base, size_jitter, 0.002)

        if self._should_escalate_search(base):
            adaptive_candidate = self._search_adaptive_candidate(image_rgb, seeds[0])
            if adaptive_candidate:
                adaptive_trial = self._evaluate_candidate(image_rgb, adaptive_candidate)
                if adaptive_trial.direct_match and (base is None or not base.direct_match):
                    base = adaptive_trial
                else:
                    base = self._pick_better_candidate(base, adaptive_trial, 0.002)

        if base is None or (not base.direct_match and not base.accepted):
            nearby = self._search_nearby_standard(image_rgb, seeds)
            base = self._pick_better_candidate(base, nearby, 0.002)

        if base is None or (not base.direct_match and not base.accepted):
            return None

        warped = self._find_best_template_warp(image_rgb, base)
        if warped:
            warped_eval, _ = warped
            base = self._pick_better_candidate(base, warped_eval) or base

        if self._should_escalate_search(base):
            best_gain = base
            for gain in self.ALPHA_GAIN_CANDIDATES:
                trial = self._evaluate_candidate(image_rgb, base.candidate, alpha_gain=gain)
                best_gain = self._pick_better_candidate(best_gain, trial) or best_gain
            base = best_gain

        return base

    def remove_watermark(self, image_path: str, output_path: str = None) -> WatermarkResult:
        self._ensure_alpha_maps()
        if not output_path:
            output_path = image_path

        try:
            img = Image.open(image_path)
            original_alpha = np.array(img.getchannel("A"), dtype=np.uint8) if "A" in img.getbands() else None
            image_rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)

            selected = self._select_candidate(image_rgb)
            if selected is None:
                return WatermarkResult(
                    status="not_found",
                    message="No watermark candidate passed validation",
                    method="reverse_alpha",
                )

            selected = self._recalibrate_gain(image_rgb, selected)
            selected = self._apply_multipass(selected)

            if selected.improvement < self.MIN_SUPPRESSION_GAIN:
                log.info(
                    "📍 Reverse-alpha: watermark not detected "
                    f"(source={selected.candidate.source} score={selected.score_before:.3f}->{selected.score_after:.3f})"
                )
                return WatermarkResult(
                    status="not_found",
                    message=(
                        f"Suppression gain too low: {selected.improvement:.3f} "
                        f"(source={selected.candidate.source})"
                    ),
                    method="reverse_alpha",
                    score_before=selected.score_before,
                    score_after=selected.score_after,
                    alpha_gain=selected.alpha_gain,
                )

            out_img = Image.fromarray(selected.image_rgb, mode="RGB")
            if original_alpha is not None and not output_path.lower().endswith((".jpg", ".jpeg")):
                out_img.putalpha(Image.fromarray(original_alpha, mode="L"))

            if output_path.lower().endswith((".jpg", ".jpeg")):
                out_img = out_img.convert("RGB")
                out_img.save(output_path, quality=95, subsampling=0)
            else:
                out_img.save(output_path)

            log.info(
                "✨ Reverse-alpha watermark removed: "
                f"source={selected.candidate.source} size={selected.candidate.size} "
                f"score {selected.score_before:.3f}->{selected.score_after:.3f} gain={selected.alpha_gain:.2f}"
            )
            return WatermarkResult(
                status="removed",
                message=(
                    f"{selected.candidate.source} "
                    f"size={selected.candidate.size} "
                    f"score {selected.score_before:.3f}->{selected.score_after:.3f}"
                ),
                method="reverse_alpha",
                score_before=selected.score_before,
                score_after=selected.score_after,
                alpha_gain=selected.alpha_gain,
            )
        except Exception as e:
            log.error(f"❌ Reverse-alpha watermark removal failed: {e}")
            return WatermarkResult(
                status="error",
                message=str(e),
                method="reverse_alpha",
            )
