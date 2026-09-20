"""Compute the terrain and hydrology derivatives from the vendored DEM.

    python scripts/build_derived.py

Runs entirely offline against ``data/raw`` and writes ``data/derived``. Every
output is a standard geomorphometric computation - no constants stand in for a
surface, and the same DEM always produces the same result.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Self

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from astra.data import osm, terrain
from astra.data.rasters import read_raster, write_manifest, write_raster
from astra.data.study_area import get_study_area
from astra.settings import get_settings

#: Upstream area at which a cell is treated as a channel. 0.5 km2 is a
#: conventional first-order channel threshold in mountainous terrain and is
#: recorded in the manifest so the choice is inspectable.
CHANNEL_THRESHOLD_KM2 = 0.5


class Stage:
    def __init__(self, label: str) -> None:
        self.label = label

    def __enter__(self) -> Self:
        self.started = time.perf_counter()
        print(f"  {self.label} ...", end="", flush=True)
        return self

    def __exit__(self, *exc: object) -> None:
        print(f" {time.perf_counter() - self.started:.1f}s")


def _write_terrain_preview(
    path: Path, dem: np.ndarray, shade: np.ndarray, target_width: int = 1400
) -> int:
    """Hillshade tinted by elevation, for the study-area preview panel.

    A rendering of the real DEM, not decoration: the panel places habitations and
    sites on it by their actual coordinates.
    """
    from PIL import Image

    step = max(1, dem.shape[1] // target_width)
    relief = shade[::step, ::step] / 255.0
    elevation = dem[::step, ::step]
    low, high = float(np.percentile(elevation, 2)), float(np.percentile(elevation, 98))
    normalised = np.clip((elevation - low) / max(high - low, 1.0), 0.0, 1.0)

    # Valley floors read cool and dark, high ground warm and pale.
    ramp = np.stack(
        [
            0.10 + 0.72 * normalised,
            0.15 + 0.62 * normalised,
            0.22 + 0.52 * normalised,
        ],
        axis=-1,
    )
    shaded = np.clip(ramp * (0.35 + 0.85 * relief[..., None]), 0.0, 1.0)
    image = Image.fromarray((shaded * 255).astype("uint8"), mode="RGB")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="JPEG", quality=90, optimize=True, progressive=True)
    return path.stat().st_size


def main() -> int:
    settings = get_settings()
    area = get_study_area()
    derived = settings.derived_dir
    derived.mkdir(parents=True, exist_ok=True)

    dem_path = settings.raw_dir / "dem" / "copernicus_dem_30m_alaknanda.tif"
    landcover_path = settings.raw_dir / "landcover" / "esa_worldcover_2021_alaknanda.tif"
    if not dem_path.exists():
        print(f"missing {dem_path}; run `python scripts/ingest.py` first")
        return 2

    dem_layer = read_raster(dem_path, name="elevation")
    dem = np.nan_to_num(dem_layer.data, nan=float(np.nanmin(dem_layer.data)))
    cell = dem_layer.cell_size
    transform, crs = dem_layer.transform, dem_layer.crs
    print(
        f"DEM {dem.shape[0]} x {dem.shape[1]} cells, "
        f"{cell.x_m:.1f} m x {cell.y_m:.1f} m, "
        f"elevation {dem.min():.0f}-{dem.max():.0f} m"
    )

    outputs: dict[str, dict] = {}

    def emit(name: str, data: np.ndarray, dtype: str, scale: float, unit: str, note: str) -> None:
        size = write_raster(
            derived / f"{name}.tif", data, transform, crs, dtype=dtype, scale=scale
        )
        outputs[name] = {
            "file": f"{name}.tif",
            "dtype": dtype,
            "stored_scale": scale,
            "unit": unit,
            "bytes": size,
            "min": float(np.nanmin(data)),
            "max": float(np.nanmax(data)),
            "mean": float(np.nanmean(data)),
            "note": note,
        }

    with Stage("slope (Horn 1981)"):
        slope = terrain.slope_degrees(dem, cell)
    emit("slope_deg", slope, "uint8", 2.0, "degrees", "stored at 0.5 degree precision")

    with Stage("terrain ruggedness index (Riley 1999)"):
        tri = terrain.terrain_ruggedness_index(dem)
    emit("ruggedness", tri, "uint16", 10.0, "m", "RMS elevation difference to neighbours")

    with Stage("depression filling (Priority-Flood)"):
        filled = terrain.fill_depressions(dem)

    with Stage("D8 flow routing"):
        receivers = terrain.d8_receivers(filled, cell)

    with Stage("flow accumulation"):
        accumulation = terrain.flow_accumulation(filled, receivers)

    upstream_km2 = accumulation * cell.area_m2 / 1e6
    emit(
        "upstream_area_km2",
        np.minimum(upstream_km2, 6553.0),
        "uint16",
        10.0,
        "km2",
        "contributing area draining through each cell",
    )

    threshold_cells = CHANNEL_THRESHOLD_KM2 * 1e6 / cell.area_m2
    channels = terrain.drainage_mask(accumulation, threshold_cells)
    emit(
        "drainage",
        channels.astype("float64"),
        "uint8",
        1.0,
        "boolean",
        f"cells with at least {CHANNEL_THRESHOLD_KM2} km2 upstream area",
    )
    print(f"  channel cells: {int(channels.sum()):,} ({channels.mean() * 100:.2f}% of grid)")

    with Stage("height above nearest drainage"):
        hand = terrain.height_above_nearest_drainage(dem, receivers, channels)
    emit("hand_m", np.minimum(hand, 6553.0), "uint16", 10.0, "m", "Renno/Nobre HAND")

    with Stage("distance to drainage"):
        distance = terrain.distance_to_drainage(channels, cell)
    emit(
        "drainage_distance_m",
        np.minimum(distance, 65530.0),
        "uint16",
        1.0,
        "m",
        "Euclidean distance to the nearest channel cell",
    )

    with Stage("drainage density"):
        density = terrain.drainage_density(channels, cell)
    emit(
        "drainage_density",
        np.clip(density, 0.0, 6553.0),
        "uint16",
        10.0,
        "km/km2",
        "channel length per unit area over a 1 km moving window",
    )

    with Stage("confluence density"):
        confluences = terrain.confluence_density(receivers, channels, cell)
    emit(
        "confluence_density",
        np.clip(confluences, 0.0, 6553.0),
        "uint16",
        10.0,
        "confluences/km2",
        "channel junctions per unit area over a 1.5 km moving window",
    )

    with Stage("catchment mean slope"):
        catchment_slope = terrain.catchment_mean_slope(filled, receivers, slope)
    emit(
        "catchment_slope_deg",
        catchment_slope,
        "uint8",
        2.0,
        "degrees",
        "mean slope of the area draining through each cell",
    )

    with Stage("hillshade"):
        shade = terrain.hillshade(dem, cell)
    emit("hillshade", shade, "uint8", 1.0, "0-255", "azimuth 315, altitude 45")

    if landcover_path.exists():
        with Stage("land cover reclassification"):
            landcover_layer = read_raster(landcover_path, name="landcover")
            landcover = np.nan_to_num(landcover_layer.data, nan=0).astype("int32")
            buildable = terrain.buildable_mask(landcover)
            instability = terrain.landcover_instability(landcover)
            infiltration = terrain.landcover_infiltration(landcover)
        size = write_raster(
            derived / "landcover_buildable.tif",
            buildable.astype("float64"),
            landcover_layer.transform,
            landcover_layer.crs,
            dtype="uint8",
        )
        outputs["landcover_buildable"] = {
            "file": "landcover_buildable.tif",
            "dtype": "uint8",
            "stored_scale": 1.0,
            "unit": "boolean",
            "bytes": size,
            "min": 0.0,
            "max": 1.0,
            "mean": float(buildable.mean()),
            "note": (
                "ESA WorldCover classes "
                f"{sorted(terrain.BUILDABLE_CLASSES)} treated as buildable; "
                f"{sorted(terrain.PROTECTED_CLASSES)} excluded"
            ),
        }
        size = write_raster(
            derived / "landcover_instability.tif",
            instability,
            landcover_layer.transform,
            landcover_layer.crs,
            dtype="uint8",
            scale=100.0,
        )
        outputs["landcover_instability"] = {
            "file": "landcover_instability.tif",
            "dtype": "uint8",
            "stored_scale": 100.0,
            "unit": "index 0-1",
            "bytes": size,
            "min": float(instability.min()),
            "max": float(instability.max()),
            "mean": float(instability.mean()),
            "note": "vegetation stability proxy per land cover class",
        }
        size = write_raster(
            derived / "landcover_infiltration.tif",
            infiltration,
            landcover_layer.transform,
            landcover_layer.crs,
            dtype="uint8",
            scale=100.0,
        )
        outputs["landcover_infiltration"] = {
            "file": "landcover_infiltration.tif",
            "dtype": "uint8",
            "stored_scale": 100.0,
            "unit": "index 0-1",
            "bytes": size,
            "min": float(infiltration.min()),
            "max": float(infiltration.max()),
            "mean": float(infiltration.mean()),
            "note": "infiltration capacity per land cover class, used inverted by the flood model",
        }
        print(f"  buildable land cover: {buildable.mean() * 100:.1f}% of the corridor")

    osm_path = settings.raw_dir / "osm" / "osm_alaknanda_network.json"
    if osm_path.exists():
        with Stage("distance to road network"):
            ways = osm.load_ways(osm_path)
            road_ways = osm.roads(ways)
            road_distance = osm.distance_to_ways(road_ways, transform, dem.shape, cell)
        emit(
            "road_distance_m",
            np.minimum(road_distance, 65530.0),
            "uint16",
            1.0,
            "m",
            f"distance to the nearest of {len(road_ways)} OSM road ways",
        )
        with Stage("distance to mapped waterways"):
            water_ways = osm.waterways(ways)
            water_distance = osm.distance_to_ways(water_ways, transform, dem.shape, cell)
        emit(
            "waterway_distance_m",
            np.minimum(water_distance, 65530.0),
            "uint16",
            1.0,
            "m",
            f"distance to the nearest of {len(water_ways)} OSM waterway ways",
        )
        print(
            f"  road network: {len(road_ways)} ways, "
            f"{float((road_distance < 500).mean() * 100):.1f}% of the corridor within 500 m"
        )

    with Stage("terrain preview image"):
        preview_path = derived / "terrain_preview.jpg"
        preview_size = _write_terrain_preview(preview_path, dem, shade)
    outputs["terrain_preview"] = {
        "file": "terrain_preview.jpg",
        "dtype": "uint8 RGB, JPEG",
        "stored_scale": 1.0,
        "unit": "image",
        "bytes": preview_size,
        "min": float(dem.min()),
        "max": float(dem.max()),
        "mean": float(dem.mean()),
        "note": (
            "Hillshade shaded by elevation, downsampled for the study-area preview. "
            "Georeferenced by the study bounding box."
        ),
    }

    manifest = {
        "study_area": area.id,
        "bbox": area.bbox.as_list(),
        "source_dem": dem_path.name,
        "grid": {
            "rows": int(dem.shape[0]),
            "cols": int(dem.shape[1]),
            "cell_x_m": round(cell.x_m, 3),
            "cell_y_m": round(cell.y_m, 3),
            "cell_area_m2": round(cell.area_m2, 2),
        },
        "channel_threshold_km2": CHANNEL_THRESHOLD_KM2,
        "methods": {
            "slope": "Horn (1981) third-order finite difference",
            "ruggedness": "Riley, DeGloria and Elliot (1999) terrain ruggedness index",
            "depression_filling": "Priority-Flood (Barnes, Lehman and Mulla, 2014)",
            "flow_routing": "D8 steepest descent (O'Callaghan and Mark, 1984)",
            "hand": "Renno et al. (2008), Nobre et al. (2011)",
        },
        "layers": outputs,
    }
    write_manifest(derived / "manifest.json", manifest)
    total = sum(entry["bytes"] for entry in outputs.values())
    print(f"wrote {len(outputs)} derived layers, {total / 1e6:.1f} MB, plus manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
