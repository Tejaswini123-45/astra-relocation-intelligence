"""Engine 1: the arithmetic, the invariants and the things it must refuse to do.

These tests build synthetic surfaces rather than reading the corridor data, so
each property is checked against an answer that can be worked out by hand.
"""

from __future__ import annotations

import numpy as np
import pytest

from astra.domain.enums import HazardType, ProvenanceClass, ZoneClass
from astra.domain.model_config import MODEL_CONFIG
from astra.domain.models import BBox
from astra.engines.grid import (
    AnalysisGrid,
    normalise_by_percentile,
    normalise_linear,
    normalise_log,
)
from astra.engines.hazard import (
    FactorSurface,
    HazardEngine,
    HazardSurfaces,
    kernel_density,
    zone_class_from_ordinal,
)
from astra.engines.zones import derive_zones, zone_summary

SHAPE = (12, 16)
BBOX = BBox(min_lon=79.30, min_lat=30.30, max_lon=79.75, max_lat=30.65)


def _grid(resolution_m: float = 100.0) -> AnalysisGrid:
    return AnalysisGrid.from_bbox(BBOX, resolution_m)


def _surfaces(grid: AnalysisGrid, **overrides) -> HazardSurfaces:
    """A neutral set of surfaces, so a test can vary exactly one thing."""
    shape = grid.shape
    base = {
        "slope_deg": np.full(shape, 10.0),
        "ruggedness_m": np.full(shape, 2.0),
        "drainage_density": np.zeros(shape),
        "hand_m": np.full(shape, 45.0),
        "drainage_distance_m": np.full(shape, 600.0),
        "catchment_slope_deg": np.full(shape, 10.0),
        "upstream_area_km2": np.full(shape, 0.5),
        "confluence_density": np.zeros(shape),
        "landcover_instability": np.zeros(shape),
        "landcover_infiltration": np.ones(shape),
        "incident_density": np.zeros(shape),
        "rainfall_intensity_mm": np.full(shape, 50.0),
        "extreme_rain_days": np.full(shape, 0.3),
    }
    base.update(overrides)
    return HazardSurfaces(grid=grid, **base)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def test_linear_normalisation_is_a_clipped_ramp() -> None:
    values = np.array([0.0, 10.0, 27.5, 45.0, 90.0])
    result = normalise_linear(values, 10.0, 45.0)
    assert np.allclose(result, [0.0, 0.0, 0.5, 1.0, 1.0])


def test_inverted_normalisation_ramps_the_other_way() -> None:
    values = np.array([3.0, 24.0, 45.0])
    result = normalise_linear(values, 3.0, 45.0, invert=True)
    assert np.allclose(result, [1.0, 0.5, 0.0])


def test_logarithmic_normalisation_is_linear_in_the_logarithm() -> None:
    result = normalise_log(np.array([0.5, 10.0, 200.0]), 0.5, 200.0)
    assert result[0] == pytest.approx(0.0)
    assert result[2] == pytest.approx(1.0)
    assert result[1] == pytest.approx(
        np.log(10.0 / 0.5) / np.log(200.0 / 0.5), abs=1e-9
    )


def test_normalisation_rejects_degenerate_bounds() -> None:
    with pytest.raises(ValueError, match="must differ"):
        normalise_linear(np.zeros(3), 5.0, 5.0)
    with pytest.raises(ValueError, match="0 < low < high"):
        normalise_log(np.zeros(3), 0.0, 10.0)


def test_percentile_normalisation_uses_the_surface_ceiling() -> None:
    values = np.array([0.0, 1.0, 2.0, 100.0])
    result = normalise_by_percentile(values, 75.0)
    assert result.max() == 1.0
    assert result[3] == 1.0  # the outlier is clipped, not allowed to flatten the rest
    assert 0.0 < result[2] <= 1.0


# ---------------------------------------------------------------------------
# The analysis grid
# ---------------------------------------------------------------------------


def test_grid_cells_are_close_to_the_requested_resolution() -> None:
    grid = _grid(100.0)
    assert 95.0 < grid.cell.x_m < 105.0
    assert 95.0 < grid.cell.y_m < 105.0


def test_grid_index_round_trips_and_rejects_points_outside() -> None:
    grid = _grid()
    index = grid.index_of(79.5, 30.5)
    assert index is not None
    bounds = grid.cell_bounds(*index)
    assert bounds.min_lon <= 79.5 <= bounds.max_lon
    assert bounds.min_lat <= 30.5 <= bounds.max_lat
    assert grid.index_of(77.0, 28.0) is None


def test_aggregation_averages_the_source_cells_in_each_analysis_cell() -> None:

    from astra.data.rasters import RasterLayer

    grid = _grid(200.0)
    fine = AnalysisGrid.from_bbox(BBOX, 50.0)
    data = np.zeros(fine.shape)
    data[:, : fine.cols // 2] = 4.0
    layer = RasterLayer(
        name="test",
        data=data,
        transform=fine.transform,
        crs="EPSG:4326",
    )
    aggregated = grid.aggregate(layer, how="mean")
    assert np.nanmax(aggregated) == pytest.approx(4.0)
    assert np.nanmin(aggregated) == pytest.approx(0.0)
    assert 0.0 < float(np.nanmean(aggregated)) < 4.0

    peak = grid.aggregate(
        RasterLayer(name="t", data=data, transform=fine.transform, crs="EPSG:4326"),
        how="max",
    )
    assert np.nanmax(peak) == pytest.approx(4.0)


def test_uncovered_cells_are_nan_not_zero() -> None:
    """A gap in the data must never read as an absence of hazard."""
    from rasterio.transform import Affine

    from astra.data.rasters import RasterLayer

    grid = _grid(200.0)
    tiny = np.ones((2, 2))
    transform = Affine.translation(79.31, 30.64) * Affine.scale(0.001, -0.001)
    aggregated = grid.aggregate(
        RasterLayer(name="t", data=tiny, transform=transform, crs="EPSG:4326")
    )
    assert np.isnan(aggregated).any()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def test_a_fully_benign_surface_scores_zero() -> None:
    grid = _grid(1000.0)
    result = HazardEngine().compute(_surfaces(grid))
    for hazard in (HazardType.LANDSLIDE, HazardType.FLOOD, HazardType.CLOUDBURST):
        assert float(np.nanmax(result.per_hazard[hazard].score)) == pytest.approx(0.0)
    assert float(np.nanmax(result.composite)) == pytest.approx(0.0)
    assert (result.zone_class == 0).all()


def test_a_saturated_surface_scores_one_hundred() -> None:
    grid = _grid(1000.0)
    shape = grid.shape
    surfaces = _surfaces(
        grid,
        slope_deg=np.full(shape, 60.0),
        ruggedness_m=np.full(shape, 80.0),
        drainage_density=np.full(shape, 20.0),
        incident_density=np.full(shape, 10.0),
        rainfall_intensity_mm=np.full(shape, 200.0),
        landcover_instability=np.ones(shape),
    )
    result = HazardEngine().compute(surfaces)
    assert float(np.nanmax(result.per_hazard[HazardType.LANDSLIDE].score)) == pytest.approx(
        100.0
    )


def test_weighted_overlay_matches_the_arithmetic_by_hand() -> None:
    """HSI = 100 * sum(w_f * n_f), and nothing else."""
    grid = _grid(1000.0)
    shape = grid.shape
    surfaces = _surfaces(grid, slope_deg=np.full(shape, 45.0))  # slope factor = 1.0
    result = HazardEngine().compute(surfaces)
    expected = 100.0 * MODEL_CONFIG.hazard.landslide_weights.w("slope")
    assert float(result.per_hazard[HazardType.LANDSLIDE].score[0, 0]) == pytest.approx(
        expected, abs=1e-6
    )


def test_engine_refuses_a_factor_set_that_does_not_match_the_weights() -> None:
    """No unweighted factor may be scored, and no weighted factor may be skipped."""
    engine = HazardEngine()
    bogus = [
        FactorSurface(
            name="vibes",
            values=np.ones((2, 2)),
            raw_values=None,
            unit=None,
            provenance=ProvenanceClass.DEMO_CONFIG,
            description="not a real factor",
        )
    ]
    with pytest.raises(ValueError, match="do not match the declared weights"):
        engine.score_hazard(HazardType.LANDSLIDE, bogus)


def test_composite_preserves_dominance_instead_of_averaging() -> None:
    grid = _grid(1000.0)
    shape = grid.shape
    # Flood saturated, landslide and cloudburst benign.
    surfaces = _surfaces(
        grid,
        hand_m=np.full(shape, 0.0),
        drainage_distance_m=np.zeros(shape),
        rainfall_intensity_mm=np.full(shape, 200.0),
        landcover_infiltration=np.zeros(shape),
    )
    result = HazardEngine().compute(surfaces)
    flood = float(result.per_hazard[HazardType.FLOOD].score[0, 0])
    composite = float(result.composite[0, 0])
    scores = sorted(
        (float(surface.score[0, 0]) for surface in result.per_hazard.values()),
        reverse=True,
    )
    lam = MODEL_CONFIG.hazard.composite_lambda.value
    assert composite == pytest.approx(min(100.0, scores[0] + lam * scores[1]), abs=1e-6)
    assert composite >= flood
    assert composite > float(np.mean(scores)), "averaging would hide the dominant hazard"


def test_dominant_hazard_is_the_highest_scoring_one() -> None:
    grid = _grid(1000.0)
    shape = grid.shape
    surfaces = _surfaces(grid, slope_deg=np.full(shape, 60.0))
    result = HazardEngine().compute(surfaces)
    dominant = result.hazards_modelled[int(result.dominant[0, 0])]
    assert dominant is HazardType.LANDSLIDE


def test_composite_never_exceeds_one_hundred() -> None:
    grid = _grid(1000.0)
    shape = grid.shape
    surfaces = _surfaces(
        grid,
        slope_deg=np.full(shape, 90.0),
        ruggedness_m=np.full(shape, 200.0),
        drainage_density=np.full(shape, 50.0),
        incident_density=np.full(shape, 100.0),
        rainfall_intensity_mm=np.full(shape, 500.0),
        landcover_instability=np.ones(shape),
        hand_m=np.zeros(shape),
        drainage_distance_m=np.zeros(shape),
        landcover_infiltration=np.zeros(shape),
        catchment_slope_deg=np.full(shape, 80.0),
        upstream_area_km2=np.full(shape, 5000.0),
        confluence_density=np.full(shape, 50.0),
        extreme_rain_days=np.full(shape, 40.0),
    )
    result = HazardEngine().compute(surfaces)
    assert float(np.nanmax(result.composite)) <= 100.0


def test_coastal_submodel_is_only_scored_when_its_inputs_exist() -> None:
    grid = _grid(1000.0)
    shape = grid.shape
    assert HazardType.COASTAL_EROSION not in HazardEngine().compute(_surfaces(grid)).per_hazard

    coastal = _surfaces(
        grid,
        shoreline_retreat_m_yr=np.full(shape, 5.0),
        elevation_m=np.zeros(shape),
        coastline_distance_m=np.zeros(shape),
        surge_exposure=np.ones(shape),
    )
    result = HazardEngine().compute(coastal)
    assert float(result.per_hazard[HazardType.COASTAL_EROSION].score[0, 0]) == pytest.approx(
        100.0
    )


# ---------------------------------------------------------------------------
# Classification and confidence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (100.0, ZoneClass.CRITICAL),
        (78.0, ZoneClass.CRITICAL),
        (77.9, ZoneClass.ELEVATED),
        (62.0, ZoneClass.ELEVATED),
        (61.9, ZoneClass.WATCH),
        (52.0, ZoneClass.WATCH),
        (51.9, ZoneClass.LOW),
    ],
)
def test_classification_is_exact_at_the_thresholds(score: float, expected: ZoneClass) -> None:
    engine = HazardEngine()
    classes = engine.classify(np.array([[score]]))
    assert zone_class_from_ordinal(int(classes[0, 0])) is expected


def test_confidence_is_bounded_and_independent_of_the_score() -> None:
    """A cell can be highly susceptible on thin evidence. Confidence must say so."""
    grid = _grid(1000.0)
    shape = grid.shape
    severe_thin_evidence = _surfaces(
        grid, slope_deg=np.full(shape, 60.0), incident_density=np.zeros(shape)
    )
    severe_rich_evidence = _surfaces(
        grid, slope_deg=np.full(shape, 60.0), incident_density=np.full(shape, 8.0)
    )
    thin = HazardEngine().compute(severe_thin_evidence)
    rich = HazardEngine().compute(severe_rich_evidence)

    assert 0.0 <= float(np.nanmin(thin.confidence)) <= 1.0
    assert float(np.nanmax(thin.confidence)) <= 1.0
    assert float(rich.confidence[0, 0]) > float(thin.confidence[0, 0])
    # The susceptibility itself is untouched by the evidence weighting.
    assert float(rich.per_hazard[HazardType.LANDSLIDE].score[0, 0]) >= float(
        thin.per_hazard[HazardType.LANDSLIDE].score[0, 0]
    )


def test_confidence_never_contains_nan() -> None:
    grid = _grid(1000.0)
    surfaces = _surfaces(grid)
    surfaces.evidence_recency_years = np.full(grid.shape, np.nan)
    assert not np.isnan(HazardEngine().compute(surfaces).confidence).any()


# ---------------------------------------------------------------------------
# Kernel density
# ---------------------------------------------------------------------------


def test_kernel_density_peaks_at_the_evidence_and_conserves_weight() -> None:
    grid = _grid(500.0)
    lons = np.array([79.5])
    lats = np.array([30.5])
    weights = np.array([4.0])
    density = kernel_density(grid, lons, lats, weights, bandwidth_m=1000.0)

    index = grid.index_of(79.5, 30.5)
    assert index is not None
    assert density[index] == float(density.max())
    total = float(density.sum()) * grid.cell_area_km2
    assert total == pytest.approx(4.0, rel=0.05)


def test_kernel_density_of_no_evidence_is_zero_everywhere() -> None:
    grid = _grid(500.0)
    density = kernel_density(
        grid, np.array([]), np.array([]), np.array([]), bandwidth_m=1000.0
    )
    assert float(density.max()) == 0.0


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------


def test_zones_below_the_minimum_mapping_unit_are_not_published() -> None:
    """One cell above a threshold is noise, not a zone."""
    grid = _grid(200.0)
    shape = grid.shape
    slope = np.full(shape, 10.0)
    slope[5, 5] = 90.0  # a single severe cell
    result = HazardEngine().compute(_surfaces(grid, slope_deg=slope))
    zones = derive_zones(result)
    assert zones == []


def test_zones_are_published_when_the_severe_area_is_large_enough() -> None:
    grid = _grid(200.0)
    shape = grid.shape
    slope = np.full(shape, 10.0)
    slope[:, : shape[1] // 2] = 75.0
    hand = np.full(shape, 45.0)
    hand[:, : shape[1] // 2] = 0.0
    result = HazardEngine().compute(
        _surfaces(
            grid,
            slope_deg=slope,
            hand_m=hand,
            incident_density=np.where(slope > 50, 6.0, 0.0),
            rainfall_intensity_mm=np.full(shape, 120.0),
        )
    )
    zones = derive_zones(result)
    assert zones
    assert all(zone.area_km2 > 0 for zone in zones)
    assert all(zone.rule_version for zone in zones)
    assert all(zone.max_composite >= zone.mean_composite for zone in zones)


def test_zone_classes_do_not_overlap_so_areas_add_up() -> None:
    grid = _grid(200.0)
    shape = grid.shape
    ramp = np.linspace(5.0, 80.0, shape[1])
    slope = np.tile(ramp, (shape[0], 1))
    result = HazardEngine().compute(
        _surfaces(
            grid,
            slope_deg=slope,
            incident_density=np.tile(np.linspace(0, 8, shape[1]), (shape[0], 1)),
            rainfall_intensity_mm=np.full(shape, 110.0),
        )
    )
    zones = derive_zones(result)
    if len(zones) < 2:
        pytest.skip("this synthetic surface produced a single zone")
    for first in range(len(zones)):
        for second in range(first + 1, len(zones)):
            overlap = zones[first].geometry.intersection(zones[second].geometry).area
            assert overlap == pytest.approx(0.0, abs=1e-9)


def test_zone_summary_totals_match_the_polygons() -> None:
    grid = _grid(200.0)
    shape = grid.shape
    slope = np.full(shape, 10.0)
    slope[:, : shape[1] // 2] = 78.0
    result = HazardEngine().compute(
        _surfaces(
            grid,
            slope_deg=slope,
            incident_density=np.where(slope > 50, 7.0, 0.0),
            rainfall_intensity_mm=np.full(shape, 130.0),
        )
    )
    zones = derive_zones(result)
    summary = zone_summary(zones)
    assert sum(entry["count"] for entry in summary.values()) == len(zones)
    assert sum(entry["area_km2"] for entry in summary.values()) == pytest.approx(
        sum(zone.area_km2 for zone in zones), rel=1e-6
    )
