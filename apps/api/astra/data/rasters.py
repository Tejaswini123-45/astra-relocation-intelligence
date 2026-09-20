"""Raster I/O for the vendored and derived layers.

One place that knows how ASTRA stores a raster, so the build script, the engines
and the tests all read and write the same way. Derived layers are stored in the
narrowest dtype that preserves their meaningful precision - the scale factors are
declared here rather than hidden in the writer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.transform import Affine

from astra.data.terrain import CellSize, cell_size_metres
from astra.domain.models import BBox


@dataclass(frozen=True)
class RasterLayer:
    """An array plus everything needed to locate it on the ground."""

    name: str
    data: np.ndarray
    transform: Affine
    crs: Any
    nodata: float | None = None

    @property
    def shape(self) -> tuple[int, int]:
        return self.data.shape  # type: ignore[return-value]

    @property
    def bbox(self) -> BBox:
        rows, cols = self.data.shape
        left, top = self.transform * (0, 0)
        right, bottom = self.transform * (cols, rows)
        return BBox(
            min_lon=min(left, right),
            min_lat=min(top, bottom),
            max_lon=max(left, right),
            max_lat=max(top, bottom),
        )

    @property
    def cell_size(self) -> CellSize:
        bbox = self.bbox
        return cell_size_metres(
            self.transform.a, self.transform.e, (bbox.min_lat + bbox.max_lat) / 2.0
        )

    def sample(self, lon: float, lat: float) -> float:
        """Nearest-cell value at a coordinate. Returns NaN outside the raster."""
        col, row = ~self.transform * (lon, lat)
        row_i, col_i = int(row), int(col)
        if not (0 <= row_i < self.data.shape[0] and 0 <= col_i < self.data.shape[1]):
            return float("nan")
        return float(self.data[row_i, col_i])

    def window_mean(self, lon: float, lat: float, radius_m: float) -> float:
        """Mean value within a square window around a coordinate."""
        cell = self.cell_size
        half_rows = max(1, int(round(radius_m / cell.y_m)))
        half_cols = max(1, int(round(radius_m / cell.x_m)))
        col, row = ~self.transform * (lon, lat)
        row_i, col_i = int(row), int(col)
        r0, r1 = max(0, row_i - half_rows), min(self.data.shape[0], row_i + half_rows + 1)
        c0, c1 = max(0, col_i - half_cols), min(self.data.shape[1], col_i + half_cols + 1)
        if r0 >= r1 or c0 >= c1:
            return float("nan")
        return float(np.nanmean(self.data[r0:r1, c0:c1]))

    def window_array(self, lon: float, lat: float, radius_m: float) -> np.ndarray:
        """The block of cells within a square window around a coordinate."""
        cell = self.cell_size
        half_rows = max(1, int(round(radius_m / cell.y_m)))
        half_cols = max(1, int(round(radius_m / cell.x_m)))
        col, row = ~self.transform * (lon, lat)
        row_i, col_i = int(row), int(col)
        r0, r1 = max(0, row_i - half_rows), min(self.data.shape[0], row_i + half_rows + 1)
        c0, c1 = max(0, col_i - half_cols), min(self.data.shape[1], col_i + half_cols + 1)
        if r0 >= r1 or c0 >= c1:
            return np.empty((0, 0))
        return self.data[r0:r1, c0:c1]

    def window_max(self, lon: float, lat: float, radius_m: float) -> float:
        cell = self.cell_size
        half_rows = max(1, int(round(radius_m / cell.y_m)))
        half_cols = max(1, int(round(radius_m / cell.x_m)))
        col, row = ~self.transform * (lon, lat)
        row_i, col_i = int(row), int(col)
        r0, r1 = max(0, row_i - half_rows), min(self.data.shape[0], row_i + half_rows + 1)
        c0, c1 = max(0, col_i - half_cols), min(self.data.shape[1], col_i + half_cols + 1)
        if r0 >= r1 or c0 >= c1:
            return float("nan")
        return float(np.nanmax(self.data[r0:r1, c0:c1]))


def read_raster(path: Path, *, name: str | None = None, scale: float = 1.0) -> RasterLayer:
    """Read a single-band raster, applying the stored scale factor."""
    with rasterio.open(path) as source:
        data = source.read(1).astype("float64")
        nodata = source.nodata
        if nodata is not None:
            data = np.where(data == nodata, np.nan, data)
        return RasterLayer(
            name=name or path.stem,
            data=data * scale if scale != 1.0 else data,
            transform=source.transform,
            crs=source.crs,
            nodata=None,
        )


def write_raster(
    path: Path,
    data: np.ndarray,
    transform: Affine,
    crs: Any,
    *,
    dtype: str,
    nodata: float | None = None,
    scale: float = 1.0,
) -> int:
    """Write a single-band compressed GeoTIFF and return its size in bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    values = np.asarray(data, dtype="float64")
    if scale != 1.0:
        values = values * scale
    if np.issubdtype(np.dtype(dtype), np.integer):
        info = np.iinfo(np.dtype(dtype))
        values = np.clip(np.round(values), info.min, info.max)
    profile = {
        "driver": "GTiff",
        "height": values.shape[0],
        "width": values.shape[1],
        "count": 1,
        "dtype": dtype,
        "crs": crs,
        "transform": transform,
        "compress": "deflate",
        "predictor": 2,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as sink:
        sink.write(values.astype(dtype), 1)
    return path.stat().st_size


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
