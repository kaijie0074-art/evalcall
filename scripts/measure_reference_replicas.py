#!/usr/bin/env python3
"""Measure structural similarity between reference product UI and local replicas.

This intentionally avoids treating a whole-image SSIM score as the acceptance
criterion. Product UI replicas can use different icons and text while matching
the operational layout. We therefore compare spatial color grids, dark/light
masks, edge-density grids, axis edge projections, dominant palettes, and named
layout landmarks.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = ROOT / "design-reference" / "product-ui"
REPLICA_DIR = ROOT / "runs" / "reference-replica-validation"
OUTPUT = REPLICA_DIR / "metrics.json"


@dataclass(frozen=True)
class Landmark:
    name: str
    axis: str
    search: tuple[float, float]


TARGETS = {
    "linear-my-issues": {
        "view": "linear",
        "landmarks": [
            Landmark("window_left", "x", (0.04, 0.16)),
            Landmark("sidebar_divider", "x", (0.33, 0.39)),
            Landmark("window_top", "y", (0.05, 0.16)),
            Landmark("context_bottom", "y", (0.16, 0.24)),
            Landmark("tabs_bottom", "y", (0.285, 0.325)),
            Landmark("filter_bottom", "y", (0.35, 0.44)),
        ],
    },
    "vanta-risk-register": {
        "view": "vanta",
        "landmarks": [
            Landmark("global_sidebar_divider", "x", (0.10, 0.12)),
            Landmark("module_sidebar_divider", "x", (0.205, 0.245)),
            Landmark("toolbar_bottom", "y", (0.07, 0.095)),
            Landmark("table_header_bottom", "y", (0.10, 0.17)),
            Landmark("first_row_bottom", "y", (0.21, 0.25)),
        ],
    },
    "retool-admin-panel": {
        "view": "retool",
        "landmarks": [
            Landmark("upper_form_preview_divider", "x", (0.45, 0.49)),
            Landmark("upper_inspector_divider", "x", (0.74, 0.79)),
            Landmark("frame_right", "x", (0.94, 0.995)),
            Landmark("frame_top", "y", (0.005, 0.07)),
            Landmark("utility_bottom", "y", (0.06, 0.12)),
            Landmark("upper_lower_divider", "y", (0.55, 0.61)),
            Landmark("frame_bottom", "y", (0.955, 0.985)),
        ],
    },
}


def load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.float32)


def resize_rgb(array: np.ndarray, width: int = 480) -> np.ndarray:
    height = max(1, round(array.shape[0] * width / array.shape[1]))
    image = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), "RGB")
    return np.asarray(image.resize((width, height), Image.Resampling.LANCZOS), dtype=np.float32)


def luminance(array: np.ndarray) -> np.ndarray:
    return 0.2126 * array[..., 0] + 0.7152 * array[..., 1] + 0.0722 * array[..., 2]


def grid_means(array: np.ndarray, rows: int = 8, columns: int = 12) -> np.ndarray:
    height, width = array.shape[:2]
    values = []
    for row in range(rows):
        y0, y1 = round(row * height / rows), round((row + 1) * height / rows)
        row_values = []
        for column in range(columns):
            x0, x1 = round(column * width / columns), round((column + 1) * width / columns)
            row_values.append(array[y0:y1, x0:x1].mean(axis=(0, 1)))
        values.append(row_values)
    return np.asarray(values)


def gradient_fields(array: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    gray = luminance(array)
    gx = np.zeros_like(gray)
    gy = np.zeros_like(gray)
    gx[:, 1:] = np.abs(np.diff(gray, axis=1))
    gy[1:, :] = np.abs(np.diff(gray, axis=0))
    return gx, gy, np.hypot(gx, gy)


def smooth(vector: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return vector
    kernel = np.ones(window, dtype=np.float32) / window
    return np.convolve(vector, kernel, mode="same")


def normalized_projection(array: np.ndarray, axis: str) -> np.ndarray:
    gx, gy, _ = gradient_fields(array)
    projection = gx.mean(axis=0) if axis == "x" else gy.mean(axis=1)
    window = max(3, round(len(projection) * 0.009))
    if window % 2 == 0:
        window += 1
    projection = smooth(projection, window)
    deviation = projection.std()
    if deviation < 1e-6:
        return np.zeros_like(projection)
    return (projection - projection.mean()) / deviation


def correlation(left: np.ndarray, right: np.ndarray) -> float:
    if len(left) != len(right):
        positions = np.linspace(0, len(right) - 1, len(left))
        right = np.interp(positions, np.arange(len(right)), right)
    if left.std() < 1e-6 or right.std() < 1e-6:
        return 0.0
    return float(np.clip(np.corrcoef(left, right)[0, 1], -1.0, 1.0))


def histogram_intersection(reference: np.ndarray, replica: np.ndarray, bins: int = 8) -> float:
    hist_reference, _ = np.histogramdd(
        reference.reshape(-1, 3), bins=bins, range=((0, 256), (0, 256), (0, 256))
    )
    hist_replica, _ = np.histogramdd(
        replica.reshape(-1, 3), bins=bins, range=((0, 256), (0, 256), (0, 256))
    )
    hist_reference /= max(hist_reference.sum(), 1)
    hist_replica /= max(hist_replica.sum(), 1)
    return float(np.minimum(hist_reference, hist_replica).sum())


def dominant_palette(array: np.ndarray, colors: int = 8) -> list[dict[str, object]]:
    image = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), "RGB")
    image.thumbnail((320, 320), Image.Resampling.LANCZOS)
    quantized = image.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette()
    counts = sorted(quantized.getcolors() or [], reverse=True)
    total = max(image.width * image.height, 1)
    result = []
    for count, index in counts:
        rgb = tuple(palette[index * 3 : index * 3 + 3])
        result.append({"hex": "#%02x%02x%02x" % rgb, "share": round(count / total, 4)})
    return result


def edge_density_grid(array: np.ndarray) -> np.ndarray:
    _, _, magnitude = gradient_fields(array)
    scale = np.percentile(magnitude, 92)
    if scale < 1e-6:
        scale = 1.0
    normalized = np.clip(magnitude / scale, 0, 1)[..., None]
    return grid_means(normalized)


def mask_grid(array: np.ndarray) -> np.ndarray:
    gray = luminance(array)
    # Separate the dark application chrome and white workspace regions. Keeping
    # these as soft masks makes the metric resilient to anti-aliasing.
    dark = np.clip((90 - gray) / 90, 0, 1)
    light = np.clip((gray - 190) / 65, 0, 1)
    return np.concatenate((grid_means(dark[..., None]), grid_means(light[..., None])), axis=-1)


def similarity_from_mae(left: np.ndarray, right: np.ndarray, scale: float) -> float:
    return float(np.clip(1 - np.abs(left - right).mean() / scale, 0, 1))


def axis_boundary_signal(array: np.ndarray, axis: str) -> np.ndarray:
    if axis == "x":
        pixel_difference = np.abs(np.diff(array, axis=1)).mean(axis=2)
        coverage = (pixel_difference > 6).mean(axis=0)
        intensity = np.clip(pixel_difference / 32, 0, 1).mean(axis=0)
    else:
        pixel_difference = np.abs(np.diff(array, axis=0)).mean(axis=2)
        coverage = (pixel_difference > 6).mean(axis=1)
        intensity = np.clip(pixel_difference / 32, 0, 1).mean(axis=1)
    # A divider runs through a large share of the relevant axis. Coverage is
    # therefore more reliable than raw contrast, which can be dominated by a
    # single line of high-contrast text.
    raw = coverage * 0.8 + intensity * 0.2
    window = max(3, round(len(raw) * 0.003))
    if window % 2 == 0:
        window += 1
    return smooth(raw, window)


def locate_landmark(array: np.ndarray, landmark: Landmark) -> dict[str, float]:
    signal = axis_boundary_signal(array, landmark.axis)
    lower = max(0, math.floor(landmark.search[0] * len(signal)))
    upper = min(len(signal), math.ceil(landmark.search[1] * len(signal)))
    segment = signal[lower:upper]
    index = lower + int(np.argmax(segment))
    strength = float(signal[index] / max(signal.mean(), 1e-6))
    return {"position": round(index / len(signal), 4), "strength": round(strength, 3)}


def image_stats(array: np.ndarray) -> dict[str, object]:
    gray = luminance(array)
    return {
        "mean_rgb": [round(float(value), 2) for value in array.mean(axis=(0, 1))],
        "mean_luminance": round(float(gray.mean()), 2),
        "dark_pixel_share": round(float((gray < 55).mean()), 4),
        "light_pixel_share": round(float((gray > 235).mean()), 4),
        "dominant_palette": dominant_palette(array),
    }


def compare(target_id: str, landmarks: Iterable[Landmark]) -> dict[str, object]:
    reference_path = REFERENCE_DIR / f"{target_id}.png"
    replica_path = REPLICA_DIR / f"{target_id}.png"
    if not reference_path.exists():
        raise FileNotFoundError(f"Missing reference: {reference_path}")
    if not replica_path.exists():
        raise FileNotFoundError(f"Missing replica screenshot: {replica_path}")

    reference_original = load_rgb(reference_path)
    replica_original = load_rgb(replica_path)
    dimensions_match = reference_original.shape == replica_original.shape
    if not dimensions_match:
        replica_image = Image.fromarray(replica_original.astype(np.uint8), "RGB")
        replica_image = replica_image.resize(
            (reference_original.shape[1], reference_original.shape[0]), Image.Resampling.LANCZOS
        )
        replica_original = np.asarray(replica_image, dtype=np.float32)

    reference = resize_rgb(reference_original)
    replica = resize_rgb(replica_original)

    reference_rgb_grid = grid_means(reference)
    replica_rgb_grid = grid_means(replica)
    reference_luma_grid = grid_means(luminance(reference)[..., None])
    replica_luma_grid = grid_means(luminance(replica)[..., None])
    projection_x = correlation(normalized_projection(reference, "x"), normalized_projection(replica, "x"))
    projection_y = correlation(normalized_projection(reference, "y"), normalized_projection(replica, "y"))

    component_scores = {
        "spatial_rgb_grid": similarity_from_mae(reference_rgb_grid, replica_rgb_grid, 255),
        "spatial_luminance_grid": similarity_from_mae(reference_luma_grid, replica_luma_grid, 255),
        "dark_light_region_masks": similarity_from_mae(mask_grid(reference), mask_grid(replica), 1),
        "edge_density_grid": similarity_from_mae(edge_density_grid(reference), edge_density_grid(replica), 1),
        "vertical_edge_projection": max(0.0, projection_x),
        "horizontal_edge_projection": max(0.0, projection_y),
        "palette_histogram": histogram_intersection(reference, replica),
    }

    weights = {
        "spatial_rgb_grid": 0.18,
        "spatial_luminance_grid": 0.18,
        "dark_light_region_masks": 0.14,
        "edge_density_grid": 0.13,
        "vertical_edge_projection": 0.10,
        "horizontal_edge_projection": 0.10,
        "palette_histogram": 0.17,
    }
    structural_score = sum(component_scores[key] * weight for key, weight in weights.items())

    landmark_results = {}
    landmark_errors = []
    for landmark in landmarks:
        reference_position = locate_landmark(reference_original, landmark)
        replica_position = locate_landmark(replica_original, landmark)
        error = abs(reference_position["position"] - replica_position["position"])
        landmark_errors.append(error)
        landmark_results[landmark.name] = {
            "axis": landmark.axis,
            "search_range": list(landmark.search),
            "reference": reference_position,
            "replica": replica_position,
            "absolute_error_fraction": round(error, 4),
            "absolute_error_percent": round(error * 100, 2),
            "within_4_percent": error <= 0.04,
        }

    return {
        "reference": str(reference_path.relative_to(ROOT)),
        "replica": str(replica_path.relative_to(ROOT)),
        "reference_dimensions": {
            "width": int(reference_original.shape[1]),
            "height": int(reference_original.shape[0]),
        },
        "replica_dimensions": {
            "width": int(replica_original.shape[1]),
            "height": int(replica_original.shape[0]),
        },
        "dimensions_match": dimensions_match,
        "reference_stats": image_stats(reference),
        "replica_stats": image_stats(replica),
        "component_scores": {key: round(value, 4) for key, value in component_scores.items()},
        "structural_score": round(structural_score, 4),
        "landmark_mean_error_percent": round(float(np.mean(landmark_errors)) * 100, 2),
        "landmark_max_error_percent": round(float(np.max(landmark_errors)) * 100, 2),
        "landmarks": landmark_results,
    }


def main() -> None:
    results = {
        target_id: compare(target_id, definition["landmarks"])
        for target_id, definition in TARGETS.items()
    }
    accepted = {}
    for target_id, result in results.items():
        checks = {
            "structural_score_at_least_0_88": result["structural_score"] >= 0.88,
            "every_landmark_within_4_percent": result["landmark_max_error_percent"] <= 4.0,
            "vertical_edge_projection_at_least_0_55": result["component_scores"]["vertical_edge_projection"] >= 0.55,
            "horizontal_edge_projection_at_least_0_55": result["component_scores"]["horizontal_edge_projection"] >= 0.55,
        }
        result["acceptance_checks"] = checks
        result["accepted"] = all(checks.values())
        accepted[target_id] = result["accepted"]
    payload = {
        "schema": "reference-replica-structural-validation/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": {
            "summary": "Spatial grids + region masks + edge density + edge projections + palette + named landmarks",
            "structural_score_weights": {
                "spatial_rgb_grid": 0.18,
                "spatial_luminance_grid": 0.18,
                "dark_light_region_masks": 0.14,
                "edge_density_grid": 0.13,
                "vertical_edge_projection": 0.10,
                "horizontal_edge_projection": 0.10,
                "palette_histogram": 0.17,
            },
            "acceptance_guidance": {
                "minimum_structural_score": 0.88,
                "maximum_landmark_error_percent": 4.0,
                "minimum_axis_edge_projection": 0.55,
                "note": "Manual screenshot review remains required; text and icon glyphs are intentionally not pixel-matched.",
            },
        },
        "results": results,
        "summary": {
            "mean_structural_score": round(
                float(np.mean([result["structural_score"] for result in results.values()])), 4
            ),
            "all_dimensions_match": all(result["dimensions_match"] for result in results.values()),
            "accepted": accepted,
        },
    }
    REPLICA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    for target_id, result in results.items():
        print(
            f"{target_id}: structural={result['structural_score']:.4f}, "
            f"landmark mean={result['landmark_mean_error_percent']:.2f}%, "
            f"max={result['landmark_max_error_percent']:.2f}%"
        )


if __name__ == "__main__":
    main()
