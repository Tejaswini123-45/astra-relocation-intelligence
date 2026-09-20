"""The analysis grid.

Hazard is scored on a regular grid over the study bounding box, at the
resolution declared in the model configuration (100 m). The source surfaces are
finer than that - the DEM is roughly 30 m - so they are block-aggregated onto
the analysis grid rather than sampled, which keeps a single steep pixel from
deciding a whole cell.

Aggregation is exact: every source cell is assigned to the analysis cell that
contains its centre, and the statistic is computed over that set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from rasterio.transform import Affine

from astra.data.rasters import RasterLayer
from astra.data.terrain import CellSize, cell_size_metres
from astra.domain.models import BBox

Statistic = Literal["mean", "max", "min", "sum"]


@dataclass(frozen=True)
class AnalysisGrid:
    """A regular lon/lat grid with a known ground cell size."""

    bbox: BBox
    rows: int
    cols: int
    transform: Affine
    cell: CellSize

    @classmethod
    def from_bbox(cls, bbox: BBox, resolution_m: float) -> AnalysisGrid:
        mean_lat = (bbox.min_lat + bbox.max_lat) / 2.0
        unit = cell_size_metres(1.0, 1.0, mean_lat)
        width_m = (bbox.max_lon - bbox.min_lon) * unit.x_m
        height_m = (bbox.max_lat - bbox.min_lat) * unit.y_m
        cols = max(1, int(round(width_m / resolution_m)))
        rows = max(1, int(round(height_m / resolution_m)))
        res_lon = (bbox.max_lon - bbox.min_lon) / cols
        res_lat = (bbox.max_lat - bbox.min_lat) / rows
        transform = Affine.translation(bbox.min_lon, bbox.max_lat) * Affine.scale(
            res_lon, -res_lat
        )
        return cls(
            bbox=bbox,
            rows=rows,
            cols=cols,
            transform=transform,
            cell=cell_size_metres(res_lon, res_lat, mean_lat),
        )

    @property
    def shape(self) -> tuple[int, int]:
        return (self.rows, self.cols)

    @property
    def size(self) -> int:
        return self.rows * self.cols

    @property
    def cell_area_km2(self) -> float:
        return self.cell.area_m2 / 1e6

    def centres(self) -> tuple[np.ndarray, np.ndarray]:
        """Longitude and latitude of every cell centre, as 2-D arrays."""
        rows = np.arange(self.rows) + 0.5
        cols = np.arange(self.cols) + 0.5
        col_grid, row_grid = np.meshgrid(cols, rows)
        lon, lat = self.transform * (col_grid, row_grid)
        return np.asarray(lon), np.asarray(lat)

    def index_of(self, lon: float, lat: float) -> tuple[int, int] | None:
        """Grid indices containing a coordinate, or None if it falls outside."""
        col, row = ~self.transform * (lon, lat)
        row_i, col_i = int(np.floor(row)), int(np.floor(col))
        if 0 <= row_i < self.rows and 0 <= col_i < self.cols:
            return row_i, col_i
        return None

    def cell_bounds(self, row: int, col: int) -> BBox:
        left, top = self.transform * (col, row)
        right, bottom = self.transform * (col + 1, row + 1)
        return BBox(min_lon=left, min_lat=bottom, max_lon=right, max_lat=top)

    def aggregate(self, layer: RasterLayer, how: Statistic = "mean") -> np.ndarray:
        """Block-aggregate a finer raster onto this grid.

        Source cells whose centres fall outside the grid are dropped; analysis
        cells with no source coverage come back as NaN rather than zero, so a gap
        in the data can never read as "no hazard here".
        """
        source_rows, source_cols = layer.data.shape
        row_idx = np.arange(source_rows) + 0.5
        col_idx = np.arange(source_cols) + 0.5
        col_grid, row_grid = np.meshgrid(col_idx, row_idx)
        lon, lat = layer.transform * (col_grid, row_grid)

        target_col, target_row = ~self.transform * (lon, lat)
        target_row = np.floor(target_row).astype(np.int64)
        target_col = np.floor(target_col).astype(np.int64)

        values = np.asarray(layer.data, dtype="float64")
        inside = (
            (target_row >= 0)
            & (target_row < self.rows)
            & (target_col >= 0)
            & (target_col < self.cols)
            & np.isfinite(values)
        )
        flat_index = (target_row[inside] * self.cols + target_col[inside]).astype(np.int64)
        selected = values[inside]

        result = np.full(self.size, np.nan, dtype="float64")
        if selected.size == 0:
            return result.reshape(self.shape)

        if how in ("mean", "sum"):
            totals = np.bincount(flat_index, weights=selected, minlength=self.size)
            counts = np.bincount(flat_index, minlength=self.size)
            covered = counts > 0
            if how == "sum":
                result[covered] = totals[covered]
            else:
                result[covered] = totals[covered] / counts[covered]
            return result.reshape(self.shape)

        extreme = np.full(self.size, -np.inf if how == "max" else np.inf)
        if how == "max":
            np.maximum.at(extreme, flat_index, selected)
        else:
            np.minimum.at(extreme, flat_index, selected)
        covered = np.isfinite(extreme)
        result[covered] = extreme[covered]
        return result.reshape(self.shape)


def normalise_linear(
    values: np.ndarray, low: float, high: float, *, invert: bool = False
) -> np.ndarray:
    """Linear ramp from ``low`` to ``high``, clipped into 0-1.

    ``invert=True`` ramps the other way, for factors where a smaller measurement
    means a larger hazard contribution - height above nearest drainage, say.
    """
    if high == low:
        raise ValueError("normalisation bounds must differ")
    scaled = (np.asarray(values, dtype="float64") - low) / (high - low)
    scaled = np.clip(scaled, 0.0, 1.0)
    return 1.0 - scaled if invert else scaled


def normalise_log(
    values: np.ndarray, low: float, high: float, *, invert: bool = False
) -> np.ndarray:
    """Logarithmic ramp, for quantities that span orders of magnitude."""
    if low <= 0 or high <= low:
        raise ValueError("logarithmic normalisation needs 0 < low < high")
    safe = np.maximum(np.asarray(values, dtype="float64"), low)
    scaled = (np.log(safe) - np.log(low)) / (np.log(high) - np.log(low))
    scaled = np.clip(scaled, 0.0, 1.0)
    return 1.0 - scaled if invert else scaled


def percentile_ceiling(values: np.ndarray, percentile: float) -> float:
    """The ceiling ``normalise_by_percentile`` would use for this surface."""
    finite = np.asarray(values, dtype="float64")
    valid = finite[np.isfinite(finite)]
    if valid.size == 0:
        return 0.0
    return float(np.percentile(valid, percentile))


def normalise_by_percentile(
    values: np.ndarray, percentile: float, *, ceiling: float | None = None
) -> np.ndarray:
    """Scale a density surface by one of its own percentiles, clipped into 0-1.

    Densities have no natural upper bound, so the ceiling has to come from the
    surface itself. Using a percentile rather than the maximum stops a single
    exceptional cluster from flattening everything else to near zero.

    ``ceiling`` pins that scale to one computed elsewhere. Two things need this.
    A live re-score must not silently move the yardstick every time an
    observation arrives - a score computed this minute has to be comparable with
    the one on screen from last minute. And pinning the ceiling is what makes
    the whole factor stack a **pure per-cell function** of its inputs, which is
    what allows a re-score of a handful of cells to be spliced into the standing
    surface and still equal a full recomputation exactly (``engines/live.py``).
    """
    finite = np.asarray(values, dtype="float64")
    if ceiling is None:
        ceiling = percentile_ceiling(finite, percentile)
    if ceiling <= 0:
        return np.zeros_like(finite)
    return np.clip(finite / ceiling, 0.0, 1.0)
