"""Engine 1 - multi-hazard susceptibility and analytical red zones.

The method is a weighted linear overlay of normalised factor rasters, the
approach used in published landslide hazard zonation and aligned with the
GSI/BIS weighted-overlay methodology:

    HSI_h = 100 * sum_f( w_hf * n_f(x) ),  sum_f w_hf = 1

and a dominance-preserving composite across hazards:

    C = min(100, max_h(HSI_h) + lambda * second_highest_h(HSI_h))

Two things this module refuses to do:

* It never averages the per-hazard vector away. The full vector, the dominant
  hazard and the ranked factor contributions survive all the way to the API, so
  "why is this red?" is always answered with a named hazard and its top factors.
* It never invents a factor. Where an input the model would like does not exist
  as open data for this corridor, the factor is absent from the weight set and
  the configuration says so, rather than being filled with a neutral constant
  that would look like evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from astra.domain.enums import HazardType, ProvenanceClass, ZoneClass
from astra.domain.model_config import MODEL_CONFIG, AstraModelConfig
from astra.engines.grid import (
    AnalysisGrid,
    normalise_by_percentile,
    normalise_linear,
    normalise_log,
    percentile_ceiling,
)

FORMULA_HSI = "hazard.hsi"
FORMULA_COMPOSITE = "hazard.composite"
FORMULA_ZONE = "hazard.zone_class"


@dataclass(frozen=True)
class FactorSurface:
    """One normalised 0-1 factor, with what it was computed from."""

    name: str
    values: np.ndarray
    raw_values: np.ndarray | None
    unit: str | None
    provenance: ProvenanceClass
    description: str


@dataclass
class HazardSurfaces:
    """The inputs the engine scores, already on the analysis grid.

    Everything here is either a real measurement aggregated onto the grid or a
    quantity derived from one. The engine does no I/O: it is handed these arrays
    so it can be exercised in tests without any data on disk.
    """

    grid: AnalysisGrid
    slope_deg: np.ndarray
    ruggedness_m: np.ndarray
    drainage_density: np.ndarray
    hand_m: np.ndarray
    drainage_distance_m: np.ndarray
    catchment_slope_deg: np.ndarray
    upstream_area_km2: np.ndarray
    confluence_density: np.ndarray
    landcover_instability: np.ndarray
    landcover_infiltration: np.ndarray
    incident_density: np.ndarray
    rainfall_intensity_mm: np.ndarray
    extreme_rain_days: np.ndarray
    evidence_recency_years: np.ndarray | None = None
    # Coastal inputs are absent in a Himalayan corridor. The sub-model is
    # implemented and tested; it is only exercised where these are supplied.
    shoreline_retreat_m_yr: np.ndarray | None = None
    elevation_m: np.ndarray | None = None
    coastline_distance_m: np.ndarray | None = None
    surge_exposure: np.ndarray | None = None

    def has_coastal_inputs(self) -> bool:
        return all(
            surface is not None
            for surface in (
                self.shoreline_retreat_m_yr,
                self.elevation_m,
                self.coastline_distance_m,
                self.surge_exposure,
            )
        )


@dataclass(frozen=True)
class HazardSurfaceResult:
    """Per-hazard susceptibility with its factor decomposition retained."""

    hazard: HazardType
    score: np.ndarray
    factors: list[FactorSurface]
    weights: dict[str, float]


@dataclass
class HazardResult:
    """Everything Engine 1 produces for one scenario."""

    grid: AnalysisGrid
    per_hazard: dict[HazardType, HazardSurfaceResult]
    composite: np.ndarray
    dominant: np.ndarray
    second: np.ndarray
    zone_class: np.ndarray
    confidence: np.ndarray
    config_version: str
    engine_version: str
    hazards_modelled: list[HazardType] = field(default_factory=list)

    def scores_at(self, row: int, col: int) -> dict[HazardType, float]:
        return {
            hazard: float(result.score[row, col])
            for hazard, result in self.per_hazard.items()
        }


def _ordered_hazards(result_keys: list[HazardType]) -> list[HazardType]:
    order = [
        HazardType.LANDSLIDE,
        HazardType.FLOOD,
        HazardType.CLOUDBURST,
        HazardType.COASTAL_EROSION,
    ]
    return [hazard for hazard in order if hazard in result_keys]


class HazardEngine:
    """Computes susceptibility surfaces, the composite and the zone classes."""

    #: Surfaces whose normalisation ceiling comes from the data rather than the
    #: configuration, and therefore has to be pinnable.
    DENSITY_SURFACES = ("incident_density", "confluence_density")

    def __init__(
        self,
        config: AstraModelConfig | None = None,
        *,
        ceilings: dict[str, float] | None = None,
    ) -> None:
        self.config = config or MODEL_CONFIG
        #: Pinned percentile ceilings, by surface name. When supplied, every
        #: density factor is scaled against these rather than against its own
        #: percentile, which makes scoring a pure per-cell function - see
        #: `normalise_by_percentile`.
        self.ceilings = dict(ceilings) if ceilings else None

    def _ceiling(self, name: str) -> float | None:
        return self.ceilings.get(name) if self.ceilings else None

    def measure_ceilings(self, surfaces: HazardSurfaces) -> dict[str, float]:
        """The ceilings this engine would use for these surfaces, to pin later."""
        percentile = self.config.normalisation.density_percentile.value
        return {
            name: percentile_ceiling(getattr(surfaces, name), percentile)
            for name in self.DENSITY_SURFACES
        }

    # -- factor construction -------------------------------------------------

    def _landslide_factors(self, surfaces: HazardSurfaces) -> list[FactorSurface]:
        norm = self.config.normalisation
        return [
            FactorSurface(
                name="slope",
                values=normalise_linear(
                    surfaces.slope_deg, norm.slope_low_deg.value, norm.slope_high_deg.value
                ),
                raw_values=surfaces.slope_deg,
                unit="degrees",
                provenance=ProvenanceClass.DERIVED,
                description="Slope angle from the Copernicus DEM (Horn 1981).",
            ),
            FactorSurface(
                name="ruggedness",
                values=normalise_linear(
                    surfaces.ruggedness_m,
                    norm.ruggedness_low_m.value,
                    norm.ruggedness_high_m.value,
                ),
                raw_values=surfaces.ruggedness_m,
                unit="m",
                provenance=ProvenanceClass.DERIVED,
                description="Terrain ruggedness index (Riley 1999).",
            ),
            FactorSurface(
                name="incident_density",
                values=normalise_by_percentile(
                    surfaces.incident_density,
                    norm.density_percentile.value,
                    ceiling=self._ceiling("incident_density"),
                ),
                raw_values=surfaces.incident_density,
                unit="weighted incidents/km2",
                provenance=ProvenanceClass.DERIVED,
                description=(
                    "Severity-weighted kernel density of recorded landslide incidents. "
                    "Evidence of past failure, not proof of future failure."
                ),
            ),
            FactorSurface(
                name="rainfall_intensity",
                values=normalise_linear(
                    surfaces.rainfall_intensity_mm,
                    norm.rainfall_intensity_low_mm.value,
                    norm.rainfall_intensity_high_mm.value,
                ),
                raw_values=surfaces.rainfall_intensity_mm,
                unit="mm/day",
                provenance=ProvenanceClass.DERIVED,
                description="Mean annual maximum daily rainfall from the ERA5 archive.",
            ),
            FactorSurface(
                name="landcover",
                values=np.clip(surfaces.landcover_instability, 0.0, 1.0),
                raw_values=surfaces.landcover_instability,
                unit="index 0-1",
                provenance=ProvenanceClass.DERIVED,
                description="Slope-stability proxy from ESA WorldCover land cover class.",
            ),
            FactorSurface(
                name="drainage_density",
                values=normalise_linear(
                    surfaces.drainage_density,
                    norm.drainage_density_low.value,
                    norm.drainage_density_high.value,
                ),
                raw_values=surfaces.drainage_density,
                unit="km/km2",
                provenance=ProvenanceClass.DERIVED,
                description="Channel density, a saturation and undercutting proxy.",
            ),
        ]

    def _flood_factors(self, surfaces: HazardSurfaces) -> list[FactorSurface]:
        norm = self.config.normalisation
        return [
            FactorSurface(
                name="hand",
                values=normalise_linear(
                    surfaces.hand_m,
                    norm.hand_low_m.value,
                    norm.hand_high_m.value,
                    invert=True,
                ),
                raw_values=surfaces.hand_m,
                unit="m",
                provenance=ProvenanceClass.DERIVED,
                description=(
                    "Height above nearest drainage. The lower a cell sits above its "
                    "channel, the more exposed it is."
                ),
            ),
            FactorSurface(
                name="drainage_distance",
                values=normalise_linear(
                    surfaces.drainage_distance_m,
                    norm.drainage_distance_low_m.value,
                    norm.drainage_distance_high_m.value,
                    invert=True,
                ),
                raw_values=surfaces.drainage_distance_m,
                unit="m",
                provenance=ProvenanceClass.DERIVED,
                description="Horizontal distance to the nearest channel.",
            ),
            FactorSurface(
                name="rainfall_intensity",
                values=normalise_linear(
                    surfaces.rainfall_intensity_mm,
                    norm.rainfall_intensity_low_mm.value,
                    norm.rainfall_intensity_high_mm.value,
                ),
                raw_values=surfaces.rainfall_intensity_mm,
                unit="mm/day",
                provenance=ProvenanceClass.DERIVED,
                description="Mean annual maximum daily rainfall from the ERA5 archive.",
            ),
            FactorSurface(
                name="infiltration",
                values=1.0 - np.clip(surfaces.landcover_infiltration, 0.0, 1.0),
                raw_values=surfaces.landcover_infiltration,
                unit="index 0-1",
                provenance=ProvenanceClass.DERIVED,
                description=(
                    "Inverted infiltration capacity from land cover: what does not "
                    "soak in runs off."
                ),
            ),
        ]

    def _cloudburst_factors(self, surfaces: HazardSurfaces) -> list[FactorSurface]:
        norm = self.config.normalisation
        return [
            FactorSurface(
                name="extreme_rainfall_frequency",
                values=normalise_linear(
                    surfaces.extreme_rain_days,
                    norm.extreme_rain_days_low.value,
                    norm.extreme_rain_days_high.value,
                ),
                raw_values=surfaces.extreme_rain_days,
                unit="days/year",
                provenance=ProvenanceClass.DERIVED,
                description=(
                    "Heavy-rainfall days per year at the IMD 64.5 mm threshold, from "
                    "the ERA5 archive."
                ),
            ),
            FactorSurface(
                name="catchment_steepness",
                values=normalise_linear(
                    surfaces.catchment_slope_deg,
                    norm.catchment_slope_low_deg.value,
                    norm.catchment_slope_high_deg.value,
                ),
                raw_values=surfaces.catchment_slope_deg,
                unit="degrees",
                provenance=ProvenanceClass.DERIVED,
                description="Mean slope of the catchment draining through the cell.",
            ),
            FactorSurface(
                name="confluence_density",
                values=normalise_by_percentile(
                    surfaces.confluence_density,
                    norm.density_percentile.value,
                    ceiling=self._ceiling("confluence_density"),
                ),
                raw_values=surfaces.confluence_density,
                unit="confluences/km2",
                provenance=ProvenanceClass.DERIVED,
                description="Channel junction density, where flash flow converges.",
            ),
            FactorSurface(
                name="upstream_area",
                values=normalise_log(
                    surfaces.upstream_area_km2,
                    norm.upstream_area_low_km2.value,
                    norm.upstream_area_high_km2.value,
                ),
                raw_values=surfaces.upstream_area_km2,
                unit="km2",
                provenance=ProvenanceClass.DERIVED,
                description=(
                    "Upstream contributing area, normalised logarithmically because "
                    "discharge scales with area over orders of magnitude."
                ),
            ),
        ]

    def _coastal_factors(self, surfaces: HazardSurfaces) -> list[FactorSurface]:
        norm = self.config.normalisation
        assert surfaces.shoreline_retreat_m_yr is not None
        assert surfaces.elevation_m is not None
        assert surfaces.coastline_distance_m is not None
        assert surfaces.surge_exposure is not None
        return [
            FactorSurface(
                name="shoreline_retreat_rate",
                values=normalise_linear(
                    surfaces.shoreline_retreat_m_yr,
                    norm.coastal_retreat_low_m_yr.value,
                    norm.coastal_retreat_high_m_yr.value,
                ),
                raw_values=surfaces.shoreline_retreat_m_yr,
                unit="m/year",
                provenance=ProvenanceClass.DERIVED,
                description="Observed shoreline retreat rate.",
            ),
            FactorSurface(
                name="elevation",
                values=normalise_linear(
                    surfaces.elevation_m,
                    norm.coastal_elevation_low_m.value,
                    norm.coastal_elevation_high_m.value,
                    invert=True,
                ),
                raw_values=surfaces.elevation_m,
                unit="m",
                provenance=ProvenanceClass.REAL_OPEN,
                description="Elevation above mean sea level, inverted.",
            ),
            FactorSurface(
                name="coastline_distance",
                values=normalise_linear(
                    surfaces.coastline_distance_m,
                    norm.coastline_distance_low_m.value,
                    norm.coastline_distance_high_m.value,
                    invert=True,
                ),
                raw_values=surfaces.coastline_distance_m,
                unit="m",
                provenance=ProvenanceClass.DERIVED,
                description="Distance to the coastline.",
            ),
            FactorSurface(
                name="surge_exposure",
                values=np.clip(surfaces.surge_exposure, 0.0, 1.0),
                raw_values=surfaces.surge_exposure,
                unit="index 0-1",
                provenance=ProvenanceClass.DERIVED,
                description="Storm-surge exposure proxy.",
            ),
        ]

    # -- scoring -------------------------------------------------------------

    def score_hazard(
        self, hazard: HazardType, factors: list[FactorSurface]
    ) -> HazardSurfaceResult:
        """Weighted linear overlay: HSI_h = 100 * sum_f(w_hf * n_f)."""
        weight_set = self.config.hazard.weights_for(hazard)
        declared = set(weight_set.weights)
        supplied = {factor.name for factor in factors}
        if declared != supplied:
            raise ValueError(
                f"{hazard.value}: factor surfaces {sorted(supplied)} do not match the "
                f"declared weights {sorted(declared)}. Every weighted factor must be "
                "supplied, and no unweighted factor may be scored."
            )

        total = np.zeros(factors[0].values.shape, dtype="float64")
        for factor in factors:
            total += weight_set.w(factor.name) * np.nan_to_num(factor.values, nan=0.0)
        return HazardSurfaceResult(
            hazard=hazard,
            score=np.clip(total * 100.0, 0.0, 100.0),
            factors=factors,
            weights={name: weight_set.w(name) for name in declared},
        )

    def composite(
        self, per_hazard: dict[HazardType, HazardSurfaceResult]
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Dominance-preserving composite, plus the dominant and second hazard.

        Averaging would hide precisely the cells that matter most: those exposed
        to two hazards at once. The strongest hazard sets the level; a second
        coincident hazard adds a configured fraction of its own score.
        """
        hazards = _ordered_hazards(list(per_hazard))
        stack = np.stack([per_hazard[hazard].score for hazard in hazards], axis=0)
        order = np.argsort(stack, axis=0)[::-1]
        highest = np.take_along_axis(stack, order[:1], axis=0)[0]
        if stack.shape[0] > 1:
            second_highest = np.take_along_axis(stack, order[1:2], axis=0)[0]
            second_index = order[1]
        else:
            second_highest = np.zeros_like(highest)
            second_index = order[0]

        lam = self.config.hazard.composite_lambda.value
        composite = np.clip(highest + lam * second_highest, 0.0, 100.0)
        return composite, order[0], second_index

    def classify(self, composite: np.ndarray) -> np.ndarray:
        """Zone class per cell, as an ordinal array matching ZONE_ORDER."""
        hazard = self.config.hazard
        classes = np.zeros(composite.shape, dtype="int8")
        classes[composite >= hazard.zone_threshold_watch.value] = 1
        classes[composite >= hazard.zone_threshold_elevated.value] = 2
        classes[composite >= hazard.zone_threshold_critical.value] = 3
        return classes

    def confidence_surface(
        self, surfaces: HazardSurfaces, per_hazard: dict[HazardType, HazardSurfaceResult]
    ) -> np.ndarray:
        """Evidence confidence per cell, computed separately from the score.

        Confidence is never multiplied into susceptibility. A cell can be highly
        susceptible on thin evidence, and the interface has to be able to say so.
        """
        config = self.config.confidence
        shape = surfaces.slope_deg.shape

        supplied = [
            factor.values
            for result in per_hazard.values()
            for factor in result.factors
        ]
        completeness = np.mean(
            [np.isfinite(values).astype("float64") for values in supplied], axis=0
        )

        # Terrain factors are derived from a real DEM everywhere; the evidence
        # that actually varies in space is the incident record.
        evidence = normalise_by_percentile(
            surfaces.incident_density,
            self.config.normalisation.density_percentile.value,
            ceiling=self._ceiling("incident_density"),
        )
        provenance_mix = np.full(shape, 0.70) + 0.30 * evidence

        if surfaces.evidence_recency_years is not None:
            recency = np.clip(1.0 - surfaces.evidence_recency_years / 20.0, 0.0, 1.0)
            # No incident record within the kernel means no recency support at
            # all, which lowers confidence rather than leaving it undefined.
            recency = np.nan_to_num(recency, nan=0.0)
        else:
            recency = np.full(shape, 0.5)

        # One value for the whole grid: every contributing surface is 100 m or
        # finer, which is good for a district-scale screening product and no
        # better than that.
        resolution = np.full(shape, 0.75)

        return np.clip(
            config.w_data_completeness.value * completeness
            + config.w_provenance_mix.value * provenance_mix
            + config.w_evidence_recency.value * recency
            + config.w_spatial_resolution.value * resolution,
            0.0,
            1.0,
        )

    def compute(self, surfaces: HazardSurfaces) -> HazardResult:
        """Score every applicable hazard and build the composite surface."""
        per_hazard: dict[HazardType, HazardSurfaceResult] = {
            HazardType.LANDSLIDE: self.score_hazard(
                HazardType.LANDSLIDE, self._landslide_factors(surfaces)
            ),
            HazardType.FLOOD: self.score_hazard(
                HazardType.FLOOD, self._flood_factors(surfaces)
            ),
            HazardType.CLOUDBURST: self.score_hazard(
                HazardType.CLOUDBURST, self._cloudburst_factors(surfaces)
            ),
        }
        if surfaces.has_coastal_inputs():
            per_hazard[HazardType.COASTAL_EROSION] = self.score_hazard(
                HazardType.COASTAL_EROSION, self._coastal_factors(surfaces)
            )

        composite, dominant, second = self.composite(per_hazard)
        return HazardResult(
            grid=surfaces.grid,
            per_hazard=per_hazard,
            composite=composite,
            dominant=dominant,
            second=second,
            zone_class=self.classify(composite),
            confidence=self.confidence_surface(surfaces, per_hazard),
            config_version=self.config.version,
            engine_version=self.config.engine_version,
            hazards_modelled=_ordered_hazards(list(per_hazard)),
        )


ZONE_ORDER: tuple[ZoneClass, ...] = (
    ZoneClass.LOW,
    ZoneClass.WATCH,
    ZoneClass.ELEVATED,
    ZoneClass.CRITICAL,
)


def zone_class_from_ordinal(value: int) -> ZoneClass:
    return ZONE_ORDER[int(value)]


# ---------------------------------------------------------------------------
# Kernel density over point evidence
# ---------------------------------------------------------------------------


def kernel_density(
    grid: AnalysisGrid,
    lons: np.ndarray,
    lats: np.ndarray,
    weights: np.ndarray,
    bandwidth_m: float,
) -> np.ndarray:
    """Gaussian kernel density of weighted points, in weighted events per km2.

    Implemented by binning the points onto the grid and convolving with a
    Gaussian of the configured bandwidth, which is exact for a regular grid and
    orders of magnitude faster than summing kernels point by point - the
    difference between a re-score that feels live and one that does not.
    """
    counts = np.zeros(grid.shape, dtype="float64")
    if lons.size:
        cols, rows = ~grid.transform * (lons, lats)
        rows = np.floor(rows).astype(np.int64)
        cols = np.floor(cols).astype(np.int64)
        inside = (rows >= 0) & (rows < grid.rows) & (cols >= 0) & (cols < grid.cols)
        np.add.at(counts, (rows[inside], cols[inside]), weights[inside])

    sigma_rows = bandwidth_m / grid.cell.y_m
    sigma_cols = bandwidth_m / grid.cell.x_m
    density = ndimage.gaussian_filter(
        counts, sigma=(sigma_rows, sigma_cols), mode="constant"
    )
    return density / grid.cell_area_km2
