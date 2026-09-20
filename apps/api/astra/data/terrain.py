"""Terrain and hydrology derivatives computed from the real elevation model.

Everything in this module is a documented, standard geomorphometric computation
over the vendored Copernicus DEM. Nothing is approximated with a constant, and
nothing is randomised: given the same DEM these functions produce the same
surfaces every time, which is what makes the hazard scores downstream
reproducible.

References for the methods used:

* Slope - Horn (1981), the third-order finite difference used by ArcGIS/GDAL.
* Terrain ruggedness index - Riley, DeGloria and Elliot (1999).
* Depression filling - Priority-Flood (Barnes, Lehman and Mulla, 2014).
* Flow routing - D8 steepest descent (O'Callaghan and Mark, 1984).
* Height above nearest drainage - Renno et al. (2008), Nobre et al. (2011).
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

# D8 neighbour offsets, clockwise from east.
_D8 = (
    (0, 1),
    (1, 1),
    (1, 0),
    (1, -1),
    (0, -1),
    (-1, -1),
    (-1, 0),
    (-1, 1),
)

EARTH_METRES_PER_DEGREE_LAT = 110_574.0
EARTH_METRES_PER_DEGREE_LON = 111_320.0


@dataclass(frozen=True)
class CellSize:
    """Ground size of one DEM cell, in metres, at the raster's mean latitude."""

    x_m: float
    y_m: float

    @property
    def area_m2(self) -> float:
        return self.x_m * self.y_m

    @property
    def mean_m(self) -> float:
        return (self.x_m + self.y_m) / 2.0


def cell_size_metres(res_lon_deg: float, res_lat_deg: float, mean_lat_deg: float) -> CellSize:
    """Convert a geographic pixel size to metres at the given latitude."""
    return CellSize(
        x_m=abs(res_lon_deg) * EARTH_METRES_PER_DEGREE_LON * np.cos(np.radians(mean_lat_deg)),
        y_m=abs(res_lat_deg) * EARTH_METRES_PER_DEGREE_LAT,
    )


def slope_degrees(dem: np.ndarray, cell: CellSize) -> np.ndarray:
    """Slope angle in degrees (Horn 1981)."""
    padded = np.pad(dem.astype("float64"), 1, mode="edge")
    z = {
        (dy, dx): padded[1 + dy : padded.shape[0] - 1 + dy, 1 + dx : padded.shape[1] - 1 + dx]
        for dy in (-1, 0, 1)
        for dx in (-1, 0, 1)
    }
    dz_dx = (
        (z[(-1, 1)] + 2 * z[(0, 1)] + z[(1, 1)])
        - (z[(-1, -1)] + 2 * z[(0, -1)] + z[(1, -1)])
    ) / (8 * cell.x_m)
    dz_dy = (
        (z[(1, -1)] + 2 * z[(1, 0)] + z[(1, 1)])
        - (z[(-1, -1)] + 2 * z[(-1, 0)] + z[(-1, 1)])
    ) / (8 * cell.y_m)
    return np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))


def terrain_ruggedness_index(dem: np.ndarray) -> np.ndarray:
    """Riley TRI: root mean square elevation difference to the eight neighbours."""
    base = dem.astype("float64")
    padded = np.pad(base, 1, mode="edge")
    total = np.zeros_like(base)
    for dy, dx in _D8:
        shifted = padded[1 + dy : padded.shape[0] - 1 + dy, 1 + dx : padded.shape[1] - 1 + dx]
        total += (shifted - base) ** 2
    return np.sqrt(total / len(_D8))


def fill_depressions(dem: np.ndarray, epsilon: float = 0.001) -> np.ndarray:
    """Priority-Flood depression filling with a small gradient towards the outlet.

    Without this, D8 routing terminates in every pit in the DEM and the drainage
    network comes out fragmented. The epsilon gradient keeps filled flats
    routable rather than flat.
    """
    filled = dem.astype("float64").copy()
    rows, cols = filled.shape
    closed = np.zeros(filled.shape, dtype=bool)
    queue: list[tuple[float, int, int]] = []

    for row in range(rows):
        for col in (0, cols - 1):
            heapq.heappush(queue, (filled[row, col], row, col))
            closed[row, col] = True
    for col in range(cols):
        for row in (0, rows - 1):
            if not closed[row, col]:
                heapq.heappush(queue, (filled[row, col], row, col))
                closed[row, col] = True

    while queue:
        elevation, row, col = heapq.heappop(queue)
        for dy, dx in _D8:
            nr, nc = row + dy, col + dx
            if nr < 0 or nr >= rows or nc < 0 or nc >= cols or closed[nr, nc]:
                continue
            closed[nr, nc] = True
            if filled[nr, nc] <= elevation:
                filled[nr, nc] = elevation + epsilon
            heapq.heappush(queue, (filled[nr, nc], nr, nc))
    return filled


def d8_receivers(filled: np.ndarray, cell: CellSize) -> np.ndarray:
    """Index of the steepest-descent neighbour for every cell, flattened.

    A cell with no lower neighbour points at itself, which marks an outlet.
    """
    rows, cols = filled.shape
    flat = filled.ravel()
    index = np.arange(flat.size, dtype=np.int64).reshape(rows, cols)
    receiver = index.copy()
    best_drop = np.zeros(filled.shape, dtype="float64")

    for dy, dx in _D8:
        distance = np.hypot(dx * cell.x_m, dy * cell.y_m)
        shifted = np.full(filled.shape, np.inf)
        shifted_index = np.full(filled.shape, -1, dtype=np.int64)

        src_rows = slice(max(0, dy), rows + min(0, dy))
        src_cols = slice(max(0, dx), cols + min(0, dx))
        dst_rows = slice(max(0, -dy), rows + min(0, -dy))
        dst_cols = slice(max(0, -dx), cols + min(0, -dx))

        shifted[dst_rows, dst_cols] = filled[src_rows, src_cols]
        shifted_index[dst_rows, dst_cols] = index[src_rows, src_cols]

        drop = (filled - shifted) / distance
        better = (drop > best_drop) & (shifted_index >= 0)
        best_drop = np.where(better, drop, best_drop)
        receiver = np.where(better, shifted_index, receiver)

    return receiver.ravel()


def flow_accumulation(filled: np.ndarray, receivers: np.ndarray) -> np.ndarray:
    """Number of cells draining through each cell, including itself.

    Cells are processed from the highest downwards, so every donor has already
    contributed by the time a cell is visited - a single linear pass.
    """
    flat_size = filled.size
    accumulation = np.ones(flat_size, dtype="float64")
    order = np.argsort(filled.ravel())[::-1]
    for node in order:
        target = receivers[node]
        if target != node:
            accumulation[target] += accumulation[node]
    return accumulation.reshape(filled.shape)


def drainage_mask(accumulation: np.ndarray, threshold_cells: float) -> np.ndarray:
    """Cells carrying enough upstream area to be treated as a channel."""
    return accumulation >= threshold_cells


def height_above_nearest_drainage(
    dem: np.ndarray, receivers: np.ndarray, channels: np.ndarray
) -> np.ndarray:
    """HAND: elevation above the channel cell each cell drains into.

    Cells are processed from the lowest upwards, so a cell's receiver already
    knows its own drainage elevation - again one linear pass, no tracing loops.
    """
    flat_dem = dem.astype("float64").ravel()
    flat_channels = channels.ravel()
    drainage_elevation = np.where(flat_channels, flat_dem, np.nan)

    order = np.argsort(flat_dem)
    for node in order:
        if flat_channels[node]:
            continue
        target = receivers[node]
        if target != node:
            drainage_elevation[node] = drainage_elevation[target]

    hand = flat_dem - drainage_elevation
    # Cells that never reach a channel (small edge basins) keep their own relief.
    hand = np.where(np.isnan(hand), 0.0, hand)
    return np.maximum(hand, 0.0).reshape(dem.shape)


def distance_to_drainage(channels: np.ndarray, cell: CellSize) -> np.ndarray:
    """Euclidean distance in metres from every cell to the nearest channel."""
    distance = ndimage.distance_transform_edt(
        ~channels, sampling=(cell.y_m, cell.x_m)
    )
    return np.asarray(distance, dtype="float64")


def drainage_density(channels: np.ndarray, cell: CellSize, window_m: float = 1000.0) -> np.ndarray:
    """Channel length per unit area, in km per square km, over a moving window."""
    window_cells = max(3, int(round(window_m / cell.mean_m)))
    if window_cells % 2 == 0:
        window_cells += 1
    counts = ndimage.uniform_filter(
        channels.astype("float64"), size=window_cells, mode="nearest"
    )
    # Each channel cell contributes roughly one cell-length of channel.
    channel_length_km = counts * cell.mean_m / 1000.0
    window_area_km2 = (window_cells * cell.mean_m / 1000.0) ** 2
    return channel_length_km * (window_cells**2) / window_area_km2


def catchment_mean_slope(
    filled: np.ndarray, receivers: np.ndarray, slope: np.ndarray
) -> np.ndarray:
    """Mean slope of the area draining through each cell.

    Accumulating slope alongside cell counts costs one extra pass and gives the
    cloudburst sub-model a real measure of how steep the contributing catchment
    is, rather than the steepness of the cell itself.
    """
    flat_slope = slope.astype("float64").ravel()
    accumulated_slope = flat_slope.copy()
    counts = np.ones(filled.size, dtype="float64")
    order = np.argsort(filled.ravel())[::-1]
    for node in order:
        target = receivers[node]
        if target != node:
            accumulated_slope[target] += accumulated_slope[node]
            counts[target] += counts[node]
    return (accumulated_slope / counts).reshape(filled.shape)


def hillshade(
    dem: np.ndarray, cell: CellSize, azimuth_deg: float = 315.0, altitude_deg: float = 45.0
) -> np.ndarray:
    """Standard hillshade, 0-255, for the terrain basemap."""
    base = dem.astype("float64")
    dy, dx = np.gradient(base, cell.y_m, cell.x_m)
    slope = np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(-dx, dy)
    azimuth = np.radians(360.0 - azimuth_deg + 90.0)
    altitude = np.radians(altitude_deg)
    shaded = np.sin(altitude) * np.cos(slope) + np.cos(altitude) * np.sin(slope) * np.cos(
        azimuth - aspect
    )
    return np.clip(shaded, 0.0, 1.0) * 255.0


# ---------------------------------------------------------------------------
# ESA WorldCover reclassification
# ---------------------------------------------------------------------------

WORLDCOVER_CLASSES: dict[int, str] = {
    10: "Tree cover",
    20: "Shrubland",
    30: "Grassland",
    40: "Cropland",
    50: "Built-up",
    60: "Bare / sparse vegetation",
    70: "Snow and ice",
    80: "Permanent water bodies",
    90: "Herbaceous wetland",
    95: "Mangroves",
    100: "Moss and lichen",
}

#: Classes a settlement may be built on. Tree cover, water, wetland, snow and
#: ice are excluded: they are either protected, unusable, or would require
#: clearance ASTRA has no authority to assume.
BUILDABLE_CLASSES: frozenset[int] = frozenset({30, 40, 50, 60, 100})

#: Classes that fail the land-cover suitability gate outright.
PROTECTED_CLASSES: frozenset[int] = frozenset({10, 70, 80, 90, 95})

#: Vegetation stability weighting used by the landslide sub-model: dense
#: vegetation binds slope material, bare ground does not.
LANDCOVER_INSTABILITY: dict[int, float] = {
    10: 0.10,
    20: 0.35,
    30: 0.50,
    40: 0.55,
    50: 0.45,
    60: 0.95,
    70: 0.60,
    80: 0.20,
    90: 0.40,
    95: 0.20,
    100: 0.70,
}


#: Infiltration capacity by land cover class, 0 (runs straight off) to 1 (soaks
#: away). Used inverted by the flood sub-model: low infiltration means more
#: surface water reaching the channel.
LANDCOVER_INFILTRATION: dict[int, float] = {
    10: 0.85,
    20: 0.70,
    30: 0.60,
    40: 0.55,
    50: 0.15,
    60: 0.35,
    70: 0.10,
    80: 0.05,
    90: 0.45,
    95: 0.50,
    100: 0.40,
}


def landcover_infiltration(landcover: np.ndarray) -> np.ndarray:
    """Per-cell infiltration capacity in 0-1, from land cover class."""
    result = np.full(landcover.shape, 0.5, dtype="float64")
    for code, value in LANDCOVER_INFILTRATION.items():
        result[landcover == code] = value
    return result


def confluence_density(
    receivers: np.ndarray,
    channels: np.ndarray,
    cell: CellSize,
    window_m: float = 1500.0,
) -> np.ndarray:
    """Density of channel confluences, where flash flow concentrates.

    A confluence is a channel cell receiving flow from two or more channel cells.
    Counting them over a moving window measures how convergent the local drainage
    is - the difference between a single stream passing through and several
    joining at once.
    """
    donors = np.zeros(channels.size, dtype="int32")
    flat_channels = channels.ravel()
    nodes = np.arange(channels.size, dtype=np.int64)
    contributing = (receivers != nodes) & flat_channels
    np.add.at(donors, receivers[contributing], 1)
    confluences = ((donors >= 2) & flat_channels).reshape(channels.shape)

    window_cells = max(3, int(round(window_m / cell.mean_m)))
    if window_cells % 2 == 0:
        window_cells += 1
    counts = ndimage.uniform_filter(
        confluences.astype("float64"), size=window_cells, mode="nearest"
    ) * (window_cells**2)
    window_area_km2 = (window_cells * cell.mean_m / 1000.0) ** 2
    return counts / window_area_km2


def buildable_mask(landcover: np.ndarray) -> np.ndarray:
    """True where the land cover class permits construction."""
    mask = np.zeros(landcover.shape, dtype=bool)
    for code in BUILDABLE_CLASSES:
        mask |= landcover == code
    return mask


def landcover_instability(landcover: np.ndarray) -> np.ndarray:
    """Per-cell slope instability proxy in 0-1, from land cover class."""
    result = np.full(landcover.shape, 0.5, dtype="float64")
    for code, value in LANDCOVER_INSTABILITY.items():
        result[landcover == code] = value
    return result
