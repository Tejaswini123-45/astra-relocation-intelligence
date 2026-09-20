"""Sentinel-2 surface reflectance for the study corridor.

This is the input to the one machine-learning component in ASTRA (CLAUDE.md
section 5.4): a small Random Forest over spectral indices that refines the
land-cover derived usable-area estimate. The primary path remains the published
ESA WorldCover product; this is a second opinion, and the agreement between the
two is reported as a confidence signal rather than either being presented as
truth.

The scenes are found through the public Earth Search STAC catalogue and read as
decimated windows from the cloud-optimised GeoTIFFs, so what lands on disk is a
30 m composite of the corridor rather than a full Sentinel-2 tile.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.warp import Resampling, reproject

from astra.data.connectors.base import Connector, ConnectorError, FetchResult
from astra.domain.enums import ConfidenceBand, ProvenanceClass
from astra.domain.models import DatasetRecord

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif,.tiff")

STAC_ENDPOINT = "https://earth-search.aws.element84.com/v1/search"

#: Bands the refinement uses: blue, green, red, near infrared and shortwave
#: infrared. Enough for NDVI, NDBI and NDWI, which is what separates vegetation,
#: built and bare surfaces from water.
BANDS = ("blue", "green", "red", "nir", "swir16")

#: A dry, low-cloud window. Post-monsoon imagery in this corridor is cloud-free
#: and snow-free below the treeline, which is where every candidate site sits.
SEARCH_START = "2023-10-15T00:00:00Z"
SEARCH_END = "2023-12-15T00:00:00Z"
MAX_CLOUD_COVER = 8.0

#: Output resolution. The refinement is compared against surfaces on the DEM
#: grid, and a 10 m composite of the corridor would be a quarter of a gigabyte.
OUTPUT_RESOLUTION_DEG = 1.0 / 3600.0


class SentinelCompositeConnector(Connector):
    """A cloud-free Sentinel-2 composite over the corridor, at DEM resolution."""

    dataset_id = "sentinel2-composite"
    SOURCE_URL = STAC_ENDPOINT

    @property
    def artifact_path(self) -> Path:
        return self.raw_dir / "sentinel" / "sentinel2_composite_alaknanda.tif"

    def _search(self) -> list[dict]:
        payload = {
            "collections": ["sentinel-2-l2a"],
            "bbox": self.bbox.as_list(),
            "datetime": f"{SEARCH_START}/{SEARCH_END}",
            "query": {"eo:cloud_cover": {"lt": MAX_CLOUD_COVER}},
            "limit": 20,
            "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
        }
        try:
            response = requests.post(STAC_ENDPOINT, json=payload, timeout=180)
            response.raise_for_status()
            features = response.json().get("features", [])
        except Exception as exc:  # noqa: BLE001 - reported as a connector failure
            raise ConnectorError(f"STAC search failed: {exc}") from exc
        if not features:
            raise ConnectorError(
                "no cloud-free Sentinel-2 scene found for the study window"
            )
        return features

    @staticmethod
    def _best_per_tile(features: list[dict]) -> list[dict]:
        """One scene per MGRS tile: the least cloudy that covers it."""
        chosen: dict[str, dict] = {}
        for feature in features:
            tile = feature["properties"].get("grid:code") or feature["id"].split("_")[1]
            current = chosen.get(tile)
            if current is None or feature["properties"].get(
                "eo:cloud_cover", 100
            ) < current["properties"].get("eo:cloud_cover", 100):
                chosen[tile] = feature
        return list(chosen.values())

    def fetch(self, *, force: bool = False) -> FetchResult:
        if not self._prepare(force):
            return self._cached_result()

        scenes = self._best_per_tile(self._search())
        bbox = self.bbox
        cols = max(1, int(round((bbox.max_lon - bbox.min_lon) / OUTPUT_RESOLUTION_DEG)))
        rows = max(1, int(round((bbox.max_lat - bbox.min_lat) / OUTPUT_RESOLUTION_DEG)))
        transform = rasterio.transform.from_bounds(
            bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat, cols, rows
        )

        stack = np.zeros((len(BANDS), rows, cols), dtype="float32")
        coverage = np.zeros((rows, cols), dtype="float32")

        for scene in scenes:
            scene_stack = np.zeros((len(BANDS), rows, cols), dtype="float32")
            scene_valid = np.zeros((rows, cols), dtype=bool)
            for index, band in enumerate(BANDS):
                href = scene["assets"][band]["href"]
                try:
                    with rasterio.open(f"/vsicurl/{href}") as source:
                        destination = np.zeros((rows, cols), dtype="float32")
                        reproject(
                            source=rasterio.band(source, 1),
                            destination=destination,
                            src_transform=source.transform,
                            src_crs=source.crs,
                            dst_transform=transform,
                            dst_crs="EPSG:4326",
                            resampling=Resampling.average,
                        )
                except rasterio.errors.RasterioIOError as exc:
                    raise ConnectorError(f"could not read {band} from {href}: {exc}") from exc
                scene_stack[index] = destination
                if index == 0:
                    scene_valid = destination > 0

            # Later scenes only fill what earlier ones did not cover: the corridor
            # spans two MGRS tiles and the seam has to come from somewhere.
            fill = scene_valid & (coverage == 0)
            for index in range(len(BANDS)):
                stack[index][fill] = scene_stack[index][fill]
            coverage[fill] = 1.0

        if coverage.mean() < 0.5:
            raise ConnectorError(
                f"composite covers only {coverage.mean() * 100:.0f}% of the corridor"
            )

        profile = {
            "driver": "GTiff",
            "height": rows,
            "width": cols,
            "count": len(BANDS),
            "dtype": "uint16",
            "crs": "EPSG:4326",
            "transform": transform,
            "nodata": 0,
            "compress": "deflate",
            "predictor": 2,
            "tiled": True,
            "blockxsize": 256,
            "blockysize": 256,
        }
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(self.artifact_path, "w", **profile) as sink:
            sink.write(np.clip(stack, 0, 65535).astype("uint16"))
            sink.descriptions = BANDS

        scene_ids = ", ".join(scene["id"] for scene in scenes)
        return FetchResult(
            dataset_id=self.dataset_id,
            path=self.artifact_path,
            bytes_written=self.artifact_path.stat().st_size,
            from_cache=False,
            detail=(
                f"{len(scenes)} scene(s) [{scene_ids}] resampled to "
                f"{rows} x {cols} at ~30 m, {coverage.mean() * 100:.0f}% coverage"
            ),
        )

    def dataset_record(self, acquired: date) -> DatasetRecord:
        return DatasetRecord(
            id=self.dataset_id,
            name="Sentinel-2 L2A post-monsoon composite (study corridor, 30 m)",
            source="ESA Copernicus Sentinel-2, via the Earth Search STAC catalogue",
            source_url=STAC_ENDPOINT,
            provenance=ProvenanceClass.REAL_OPEN,
            acquired=acquired,
            processing=(
                "Least-cloudy scenes between "
                f"{SEARCH_START[:10]} and {SEARCH_END[:10]} under "
                f"{MAX_CLOUD_COVER:.0f}% cloud, bands "
                f"{', '.join(BANDS)} resampled by averaging to a ~30 m geographic grid "
                "over the study bounding box and mosaicked across MGRS tiles."
            ),
            resolution="~30 m, resampled from 10 m and 20 m native bands",
            temporal_coverage=f"{SEARCH_START[:10]} to {SEARCH_END[:10]}",
            confidence=ConfidenceBand.HIGH,
            licence="Copernicus Sentinel data, free and open",
            notes=(
                "Used only by the land-cover refinement model, which is a second "
                "opinion on usable area. The published ESA WorldCover product "
                "remains the primary path."
            ),
        )
