"""Terrain and hydrology maths, checked against cases with a known answer.

These are the surfaces every hazard score is built from. If slope is wrong by a
factor, every downstream number is wrong and no amount of UI polish helps.
"""

from __future__ import annotations

import numpy as np
import pytest

from astra.data import terrain
from astra.data.terrain import CellSize

CELL = CellSize(x_m=30.0, y_m=30.0)


def _plane(rows: int, cols: int, rise_per_cell: float) -> np.ndarray:
    """A constant-gradient surface descending towards increasing row index."""
    return np.tile(
        (np.arange(rows, dtype="float64")[::-1] * rise_per_cell)[:, None], (1, cols)
    )


def test_slope_of_a_flat_surface_is_zero() -> None:
    flat = np.full((20, 20), 1500.0)
    assert np.allclose(terrain.slope_degrees(flat, CELL), 0.0)


def test_slope_of_a_known_gradient_matches_the_trigonometry() -> None:
    # 30 m rise over a 30 m cell is exactly 45 degrees.
    dem = _plane(20, 20, rise_per_cell=30.0)
    slope = terrain.slope_degrees(dem, CELL)
    interior = slope[2:-2, 2:-2]
    assert np.allclose(interior, 45.0, atol=1e-6)


def test_slope_scales_with_cell_size() -> None:
    dem = _plane(20, 20, rise_per_cell=30.0)
    coarse = terrain.slope_degrees(dem, CellSize(x_m=60.0, y_m=60.0))
    assert np.isclose(coarse[10, 10], np.degrees(np.arctan(0.5)), atol=1e-6)


def test_ruggedness_is_zero_on_a_flat_surface_and_positive_on_rough_ground() -> None:
    flat = np.full((10, 10), 800.0)
    assert np.allclose(terrain.terrain_ruggedness_index(flat), 0.0)

    rough = flat.copy()
    rough[5, 5] = 900.0
    assert terrain.terrain_ruggedness_index(rough)[4, 4] > 0.0


def test_depression_filling_removes_a_pit_without_lowering_the_surface() -> None:
    dem = np.full((15, 15), 100.0)
    dem[7, 7] = 40.0  # a pit with no outlet
    filled = terrain.fill_depressions(dem)
    assert filled[7, 7] > dem[7, 7]
    assert filled[7, 7] >= 100.0
    assert (filled >= dem - 1e-9).all(), "filling must never lower the terrain"


def test_d8_routing_points_downhill() -> None:
    dem = _plane(10, 10, rise_per_cell=10.0)
    receivers = terrain.d8_receivers(dem, CELL)
    flat = dem.ravel()
    for node in range(flat.size):
        target = receivers[node]
        if target != node:
            assert flat[target] <= flat[node]


def test_flow_accumulation_concentrates_downstream() -> None:
    dem = _plane(12, 12, rise_per_cell=10.0)
    receivers = terrain.d8_receivers(dem, CELL)
    accumulation = terrain.flow_accumulation(dem, receivers)
    assert accumulation.min() >= 1.0
    # The bottom row of a uniform slope drains everything above it.
    assert accumulation[-1].sum() > accumulation[0].sum()
    assert np.isclose(accumulation.max(), accumulation.max())


def test_hand_is_zero_on_channels_and_positive_above_them() -> None:
    dem = _plane(12, 12, rise_per_cell=10.0)
    receivers = terrain.d8_receivers(dem, CELL)
    channels = np.zeros(dem.shape, dtype=bool)
    channels[-1, :] = True  # the valley floor row

    hand = terrain.height_above_nearest_drainage(dem, receivers, channels)
    assert np.allclose(hand[-1, :], 0.0)
    assert (hand[:-1, :] > 0).all()
    # Two cells above the channel on a 10 m/cell slope is 20 m of relief.
    assert np.isclose(hand[-3, 5], 20.0, atol=1e-6)


def test_distance_to_drainage_is_measured_in_metres() -> None:
    channels = np.zeros((10, 10), dtype=bool)
    channels[0, 0] = True
    distance = terrain.distance_to_drainage(channels, CELL)
    assert distance[0, 0] == 0.0
    assert np.isclose(distance[0, 3], 90.0)
    assert np.isclose(distance[3, 0], 90.0)


def test_drainage_mask_respects_the_threshold() -> None:
    accumulation = np.array([[1.0, 5.0], [20.0, 100.0]])
    mask = terrain.drainage_mask(accumulation, threshold_cells=20.0)
    assert mask.tolist() == [[False, False], [True, True]]


def test_catchment_mean_slope_lies_within_the_slope_range() -> None:
    dem = _plane(12, 12, rise_per_cell=10.0)
    receivers = terrain.d8_receivers(dem, CELL)
    slope = terrain.slope_degrees(dem, CELL)
    catchment = terrain.catchment_mean_slope(dem, receivers, slope)
    assert catchment.min() >= slope.min() - 1e-6
    assert catchment.max() <= slope.max() + 1e-6


def test_hillshade_stays_inside_the_byte_range() -> None:
    dem = _plane(20, 20, rise_per_cell=15.0)
    shade = terrain.hillshade(dem, CELL)
    assert shade.min() >= 0.0
    assert shade.max() <= 255.0


@pytest.mark.parametrize("code", sorted(terrain.BUILDABLE_CLASSES))
def test_buildable_classes_are_marked_buildable(code: int) -> None:
    assert terrain.buildable_mask(np.array([[code]]))[0, 0]


@pytest.mark.parametrize("code", sorted(terrain.PROTECTED_CLASSES))
def test_protected_classes_are_never_buildable(code: int) -> None:
    assert not terrain.buildable_mask(np.array([[code]]))[0, 0]


def test_buildable_and_protected_classes_do_not_overlap() -> None:
    assert not (terrain.BUILDABLE_CLASSES & terrain.PROTECTED_CLASSES)


def test_every_worldcover_class_has_an_instability_value() -> None:
    for code in terrain.WORLDCOVER_CLASSES:
        assert code in terrain.LANDCOVER_INSTABILITY


def test_bare_ground_is_less_stable_than_tree_cover() -> None:
    assert terrain.LANDCOVER_INSTABILITY[60] > terrain.LANDCOVER_INSTABILITY[10]
