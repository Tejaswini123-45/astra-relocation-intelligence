"""Compute the baseline hazard surfaces and analytical red zones.

    python scripts/build_hazard.py

Runs Engine 1 over the vendored and derived data and writes the baseline
artifacts the map and the API serve: per-hazard susceptibility rasters, the
composite, the confidence surface, a colour-mapped overlay image and the zone
polygons. Scenario runs recompute in memory; this is the baseline on disk.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from astra.data.rasters import read_manifest, write_manifest, write_raster
from astra.engines.context import build_context
from astra.engines.hazard import HazardEngine
from astra.engines.zones import derive_zones, zone_summary
from astra.settings import get_settings

#: Severity ramp for the composite overlay. Deep teal through ochre and ember to
#: crimson - red is reserved for the Critical class and nothing else.
SEVERITY_RAMP: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.0, (18, 58, 66)),
    (0.35, (36, 96, 92)),
    (0.55, (150, 132, 62)),
    (0.72, (192, 118, 55)),
    (0.88, (188, 62, 48)),
    (1.0, (150, 28, 28)),
)


def _colourise(composite: np.ndarray, alpha_floor: float = 0.0) -> np.ndarray:
    """Map composite scores to RGBA, transparent below the lowest published class.

    Tinting the whole corridor would be honest but unreadable: the eye has to be
    able to find the classified ground. Cells below the Watch threshold are drawn
    fully transparent, and the terrain shows through unaltered.
    """
    from astra.domain.model_config import MODEL_CONFIG

    watch = MODEL_CONFIG.hazard.zone_threshold_watch.value
    normalised = np.clip(composite / 100.0, 0.0, 1.0)
    visible = np.clip((composite - watch) / max(100.0 - watch, 1.0), 0.0, 1.0)
    stops = np.array([stop for stop, _ in SEVERITY_RAMP])
    colours = np.array([colour for _, colour in SEVERITY_RAMP], dtype="float64")
    rgba = np.zeros((*composite.shape, 4), dtype="uint8")
    for channel in range(3):
        rgba[..., channel] = np.interp(normalised, stops, colours[:, channel]).astype("uint8")
    alpha = np.clip(alpha_floor + (1.0 - alpha_floor) * visible**0.75, 0.0, 1.0)
    rgba[..., 3] = (alpha * 235).astype("uint8")
    return rgba


def _confidence_veil(confidence: np.ndarray) -> np.ndarray:
    """Render the confidence surface as a hatch, not as a fade.

    Fading low-confidence ground would read as *less hazardous*, which is exactly
    the conflation section 5.2 exists to prevent: a cell can be highly
    susceptible on thin evidence. A hatch says "we are less sure here" without
    touching how severe the ground looks, and it survives being printed in
    greyscale - which colour alone does not.

    The hatch is drawn at the surface's own observed range rather than 0-1. In a
    corridor where every cell scores between 0.62 and 0.79, stretching the ramp
    over the full theoretical scale would render a uniform sheet and say nothing.
    The Model & Provenance screen states the observed range beside the legend so
    nobody reads the contrast as wider than it is.
    """
    from astra.domain.model_config import MODEL_CONFIG

    values = np.asarray(confidence, dtype="float64")
    finite = values[np.isfinite(values)]
    rows, cols = values.shape
    image = np.zeros((rows, cols, 4), dtype="uint8")
    if finite.size == 0:
        return image

    low, high = float(finite.min()), float(finite.max())
    span = max(high - low, 1e-6)
    # 0 where confidence is highest observed, 1 where it is lowest observed.
    doubt = np.clip((high - np.nan_to_num(values, nan=high)) / span, 0.0, 1.0)

    row_index, col_index = np.indices((rows, cols))
    # Two hatch pitches: the coarse one appears as soon as confidence dips, the
    # fine one only on the least-supported ground, so the pattern reads as a
    # gradient rather than as a single on/off texture.
    coarse = ((row_index + col_index) % 10) == 0
    fine = ((row_index - col_index) % 10) == 0
    hatch = (coarse & (doubt > 0.25)) | (fine & (doubt > 0.6))

    band_low = MODEL_CONFIG.confidence.band_medium_min.value
    below_band = np.isfinite(values) & (values < band_low)

    alpha = np.zeros((rows, cols), dtype="float64")
    alpha[hatch] = 110.0 + 90.0 * doubt[hatch]
    # Ground below the published Medium band is hatched everywhere, not only on
    # the stripes: "we have almost nothing here" is a different statement from
    # "we are less sure here".
    alpha[below_band] = np.maximum(alpha[below_band], 70.0)

    image[..., 0] = 226
    image[..., 1] = 232
    image[..., 2] = 240
    image[..., 3] = np.clip(alpha, 0, 255).astype("uint8")
    image[~np.isfinite(values)] = 0
    return image


def main() -> int:
    settings = get_settings()
    derived = settings.derived_dir
    derived.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    context = build_context()
    print(
        f"context: {context.grid.rows} x {context.grid.cols} cells at "
        f"{context.grid.cell.x_m:.0f} x {context.grid.cell.y_m:.0f} m, "
        f"{len(context.incidents)} incidents, {len(context.rainfall)} rainfall points "
        f"({time.perf_counter() - started:.1f}s)"
    )

    engine = HazardEngine()
    started = time.perf_counter()
    result = engine.compute(context.surfaces)
    print(f"hazard surfaces computed in {time.perf_counter() - started:.2f}s")

    transform, crs = context.grid.transform, "EPSG:4326"
    outputs: dict[str, dict] = {}

    def emit(name: str, data: np.ndarray, unit: str, note: str, scale: float = 10.0) -> None:
        size = write_raster(
            derived / f"{name}.tif", data, transform, crs, dtype="uint16", scale=scale
        )
        outputs[name] = {
            "file": f"{name}.tif",
            "dtype": "uint16",
            "stored_scale": scale,
            "unit": unit,
            "bytes": size,
            "min": float(np.nanmin(data)),
            "max": float(np.nanmax(data)),
            "mean": float(np.nanmean(data)),
            "note": note,
        }

    for hazard, surface in result.per_hazard.items():
        weights = ", ".join(
            f"{name} {weight:.2f}" for name, weight in sorted(surface.weights.items())
        )
        emit(
            f"hazard_{hazard.value.lower()}",
            surface.score,
            "index 0-100",
            f"weighted overlay of {len(surface.factors)} normalised factors ({weights})",
        )
        print(
            f"  {hazard.value:16s} mean {np.nanmean(surface.score):5.1f}  "
            f"p95 {np.nanpercentile(surface.score, 95):5.1f}  "
            f"max {np.nanmax(surface.score):5.1f}"
        )

    lam = engine.config.hazard.composite_lambda.value
    emit(
        "hazard_composite",
        result.composite,
        "index 0-100",
        f"max_h(HSI_h) + {lam} x second_highest_h(HSI_h), clipped to 100",
    )
    emit(
        "confidence",
        result.confidence,
        "index 0-1",
        "evidence confidence, computed separately and never multiplied into the score",
        scale=1000.0,
    )

    from PIL import Image

    confidence_overlay = Image.fromarray(_confidence_veil(result.confidence), mode="RGBA")
    confidence_path = derived / "confidence.png"
    confidence_overlay.save(confidence_path, format="PNG", optimize=True)
    outputs["confidence_overlay"] = {
        "file": "confidence.png",
        "dtype": "uint8 RGBA",
        "stored_scale": 1.0,
        "unit": "image",
        "bytes": confidence_path.stat().st_size,
        "min": float(np.nanmin(result.confidence)),
        "max": float(np.nanmax(result.confidence)),
        "mean": float(np.nanmean(result.confidence)),
        "note": (
            "evidence confidence rendered as a diagonal hatch whose density rises as "
            "confidence falls; drawn over the hazard surface rather than blended into "
            "it, because confidence is never multiplied into the score"
        ),
    }

    overlay = Image.fromarray(_colourise(result.composite), mode="RGBA")
    overlay_path = derived / "hazard_composite.png"
    overlay.save(overlay_path, format="PNG", optimize=True)
    outputs["hazard_composite_overlay"] = {
        "file": "hazard_composite.png",
        "dtype": "uint8 RGBA",
        "stored_scale": 1.0,
        "unit": "image",
        "bytes": overlay_path.stat().st_size,
        "min": float(np.nanmin(result.composite)),
        "max": float(np.nanmax(result.composite)),
        "mean": float(np.nanmean(result.composite)),
        "note": "colour-mapped composite for the map overlay, aligned to the study bbox",
    }

    started = time.perf_counter()
    zones = derive_zones(result, context.habitations)
    summary = zone_summary(zones)
    print(f"zones derived in {time.perf_counter() - started:.2f}s")
    for zone_class, stats in summary.items():
        if stats["count"]:
            print(
                f"  {zone_class:9s} {stats['count']:4d} polygons  "
                f"{stats['area_km2']:8.1f} km2  "
                f"{stats['population_intersected']:5d} residents"
            )

    collection = {
        "type": "FeatureCollection",
        "features": [zone.as_feature() for zone in zones],
        "properties": {
            "classification_label": "ASTRA analytical classification",
            "model_config_version": result.config_version,
            "engine_version": result.engine_version,
            "summary": summary,
        },
    }
    zones_path = derived / "red_zones.geojson"
    zones_path.write_text(json.dumps(collection), encoding="utf-8")
    outputs["red_zones"] = {
        "file": "red_zones.geojson",
        "dtype": "GeoJSON polygons",
        "stored_scale": 1.0,
        "unit": "polygons",
        "bytes": zones_path.stat().st_size,
        "min": 0.0,
        "max": float(len(zones)),
        "mean": float(len(zones)),
        "note": (
            "thresholded composite, cleaned to the minimum mapping unit, grown by the "
            "configured buffer, exclusive between classes"
        ),
    }

    manifest_path = derived / "manifest.json"
    manifest = read_manifest(manifest_path) if manifest_path.exists() else {"layers": {}}
    manifest["layers"].update(outputs)
    manifest["hazard"] = {
        "grid_rows": context.grid.rows,
        "grid_cols": context.grid.cols,
        "cell_x_m": round(context.grid.cell.x_m, 2),
        "cell_y_m": round(context.grid.cell.y_m, 2),
        "hazards_modelled": [hazard.value for hazard in result.hazards_modelled],
        "incidents_used": len(context.incidents),
        "rainfall_points": len(context.rainfall),
        "zone_summary": summary,
        "model_config_version": result.config_version,
        "engine_version": result.engine_version,
    }
    write_manifest(manifest_path, manifest)
    print(f"wrote {len(outputs)} hazard artifacts and updated the manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
