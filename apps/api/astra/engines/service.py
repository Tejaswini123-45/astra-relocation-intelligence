"""The engine runtime: one computed baseline, shared by every request.

The hazard surfaces are deterministic given the vendored data and the model
configuration, so the baseline is computed once and reused. Scenario runs
recompute from the same context with perturbed inputs and never mutate this
result.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from astra.domain.enums import ConfidenceBand, HazardType, ZoneClass
from astra.domain.model_config import MODEL_CONFIG
from astra.domain.models import (
    CompositeHazard,
    ConfidenceReport,
    FactorContribution,
    HazardScore,
)
from astra.domain.notices import CLASSIFICATION_LABEL
from astra.engines.context import EngineContext, get_context
from astra.engines.hazard import HazardEngine, HazardResult, zone_class_from_ordinal
from astra.engines.zones import ZonePolygon, derive_zones, zone_summary


@dataclass
class RiskRun:
    """A computed hazard state: the surfaces, the zones and how long it took."""

    context: EngineContext
    result: HazardResult
    zones: list[ZonePolygon]
    computed_ms: float

    @property
    def grid(self):
        return self.context.grid

    def summary(self) -> dict:
        return zone_summary(self.zones)


def compute_risk(context: EngineContext | None = None) -> RiskRun:
    """Run Engine 1 and derive the zones. Pure with respect to its inputs.

    The reported duration covers the scoring and zone derivation only. Loading
    and aggregating the surfaces happens once at startup and is not folded in:
    reporting it as compute time would flatter nothing and confuse everything.
    """
    ctx = context or get_context()
    started = time.perf_counter()
    result = HazardEngine().compute(ctx.surfaces)
    zones = derive_zones(result, ctx.habitations)
    return RiskRun(
        context=ctx,
        result=result,
        zones=zones,
        computed_ms=(time.perf_counter() - started) * 1000.0,
    )


@lru_cache(maxsize=1)
def baseline_risk() -> RiskRun:
    """The cached baseline run, warmed at API startup."""
    return compute_risk()


def confidence_band(value: float) -> ConfidenceBand:
    config = MODEL_CONFIG.confidence
    if value >= config.band_high_min.value:
        return ConfidenceBand.HIGH
    if value >= config.band_medium_min.value:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


def _round(value: float, digits: int = 6) -> float:
    return float(np.round(value, digits))


def hazard_at_cell(run: RiskRun, row: int, col: int) -> CompositeHazard:
    """The full decomposition at one grid cell, with nothing averaged away."""
    result = run.result
    per_hazard: list[HazardScore] = []
    for hazard in result.hazards_modelled:
        surface = result.per_hazard[hazard]
        factors = [
            FactorContribution(
                factor=factor.name,
                raw_value=(
                    None
                    if factor.raw_values is None
                    else _round(float(factor.raw_values[row, col]), 4)
                ),
                normalised_value=_round(float(np.nan_to_num(factor.values[row, col])), 6),
                weight=_round(surface.weights[factor.name], 6),
                contribution=_round(
                    surface.weights[factor.name]
                    * float(np.nan_to_num(factor.values[row, col])),
                    6,
                ),
                provenance=factor.provenance,
                unit=factor.unit,
            )
            for factor in surface.factors
        ]
        factors.sort(key=lambda contribution: contribution.contribution, reverse=True)
        per_hazard.append(
            HazardScore(
                hazard=hazard,
                score=_round(float(surface.score[row, col]), 2),
                factors=factors,
            )
        )

    dominant = result.hazards_modelled[int(result.dominant[row, col])]
    second = (
        result.hazards_modelled[int(result.second[row, col])]
        if len(result.hazards_modelled) > 1
        else None
    )
    return CompositeHazard(
        composite=_round(float(result.composite[row, col]), 2),
        dominant_hazard=dominant,
        second_hazard=second if second is not dominant else None,
        per_hazard=sorted(per_hazard, key=lambda entry: entry.score, reverse=True),
        zone_class=zone_class_from_ordinal(int(result.zone_class[row, col])),
        classification_label=CLASSIFICATION_LABEL,
    )


def confidence_at_cell(run: RiskRun, row: int, col: int) -> ConfidenceReport:
    """Evidence confidence at a cell, reported beside the score, never inside it."""
    value = float(run.result.confidence[row, col])
    recency = run.context.surfaces.evidence_recency_years
    age_days = None
    if recency is not None and np.isfinite(recency[row, col]):
        age_days = int(float(recency[row, col]) * 365.25)
    return ConfidenceReport(
        value=_round(value, 4),
        band=confidence_band(value),
        evidence_age_days=age_days,
        note=(
            "Evidence confidence is computed from input completeness, provenance mix, "
            "evidence recency and spatial resolution. It is reported alongside the "
            "susceptibility score and is never multiplied into it."
        ),
    )


def hazard_over_footprint(
    run: RiskRun, lon: float, lat: float, radius_m: float
) -> tuple[CompositeHazard, float, float]:
    """Composite hazard over a habitation footprint: decomposition plus statistics.

    A settlement is not a point, but it is also not the worst cell within walking
    distance of it. Reporting the maximum as *the* habitation score would push
    almost every settlement in this terrain to 100 and destroy any ability to
    rank them. So the decomposition is reported at the settlement's own cell -
    the location an official would point to - and the footprint mean and maximum
    are reported alongside it, separately, as the spread around that value.
    """
    grid = run.grid
    index = grid.index_of(lon, lat)
    if index is None:
        raise ValueError(f"({lon}, {lat}) falls outside the analysis grid")
    row, col = index

    half_rows = max(0, int(round(radius_m / grid.cell.y_m)))
    half_cols = max(0, int(round(radius_m / grid.cell.x_m)))
    r0, r1 = max(0, row - half_rows), min(grid.rows, row + half_rows + 1)
    c0, c1 = max(0, col - half_cols), min(grid.cols, col + half_cols + 1)
    window = run.result.composite[r0:r1, c0:c1]

    return (
        hazard_at_cell(run, row, col),
        float(np.nanmean(window)),
        float(np.nanmax(window)),
    )


def zone_of(run: RiskRun, lon: float, lat: float) -> ZonePolygon | None:
    """The published zone polygon containing a point, if any."""
    from shapely.geometry import Point

    point = Point(lon, lat)
    for zone in run.zones:
        if zone.geometry.covers(point):
            return zone
    return None


def hazard_class_share(run: RiskRun) -> dict[str, float]:
    """Share of the corridor in each class, as computed, not as estimated."""
    total = run.result.zone_class.size
    shares: dict[str, float] = {}
    for ordinal, zone_class in enumerate(
        (ZoneClass.LOW, ZoneClass.WATCH, ZoneClass.ELEVATED, ZoneClass.CRITICAL)
    ):
        count = int((run.result.zone_class == ordinal).sum())
        shares[zone_class.value] = round(count / total * 100.0, 2)
    return shares


def hazard_statistics(run: RiskRun) -> dict[str, dict[str, float]]:
    """Per-hazard distribution statistics for the transparency panel."""
    statistics: dict[str, dict[str, float]] = {}
    for hazard in [*run.result.hazards_modelled, None]:
        if hazard is None:
            values = run.result.composite
            key = "COMPOSITE"
        else:
            values = run.result.per_hazard[hazard].score
            key = hazard.value
        statistics[key] = {
            "mean": round(float(np.nanmean(values)), 2),
            "p50": round(float(np.nanpercentile(values, 50)), 2),
            "p95": round(float(np.nanpercentile(values, 95)), 2),
            "max": round(float(np.nanmax(values)), 2),
        }
    return statistics


def hazard_types_modelled(run: RiskRun) -> list[HazardType]:
    return list(run.result.hazards_modelled)
