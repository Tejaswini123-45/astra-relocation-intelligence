"""Priority endpoints: who is most at risk, and why they are ranked where they are.

The ranking is a decision-support output. Every row carries the four components
that produced it, each with its own factor decomposition, plus the evidence
confidence, which is reported beside the score and never folded into it.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, HTTPException

from astra.api.schemas import (
    ComponentScoreResponse,
    HabitationDetailResponse,
    HabitationPriorityResponse,
    HabitationPriorityRow,
    PhaseDecision,
    PhaseTotals,
)
from astra.domain.model_config import MODEL_CONFIG
from astra.domain.notices import (
    CLASSIFICATION_LABEL,
    DECISION_AUTHORITY,
    HISTORY_NOT_PREDICTION,
    PRIORITY_NOT_PROBABILITY,
    SCENARIO_DISCLAIMER,
)
from astra.domain.registry import get_formula
from astra.engines.priority import (
    ComponentScore,
    HabitationPriority,
    PriorityEngine,
    PriorityResult,
    priority_explanation,
)
from astra.engines.service import baseline_risk

router = APIRouter(prefix="/priority", tags=["priority"])


@lru_cache(maxsize=1)
def baseline_priority() -> PriorityResult:
    """The cached baseline ranking. Scenario runs recompute from a fresh context."""
    return PriorityEngine().compute(baseline_risk())


def _component(score: ComponentScore) -> ComponentScoreResponse:
    return ComponentScoreResponse(
        value=round(min(max(score.value, 0.0), 1.0), 6),
        formula_id=score.formula_id,
        factors=score.factors,
        note=score.note,
    )


def _row(entry: HabitationPriority) -> HabitationPriorityRow:
    return HabitationPriorityRow(
        rank=entry.rank,
        habitation_id=entry.habitation.id,
        name=entry.habitation.name,
        population=entry.habitation.population,
        households=entry.habitation.households,
        centroid=entry.habitation.centroid,
        elevation_m=entry.habitation.elevation_m,
        priority_score=entry.priority_score,
        priority_factors=entry.priority_factors,
        hazard=entry.hazard,
        hazard_component=_component(entry.hazard_component),
        footprint_mean_composite=entry.footprint_mean,
        footprint_max_composite=entry.footprint_max,
        footprint_radius_m=MODEL_CONFIG.priority.footprint_radius_m.value,
        exposure=_component(entry.exposure),
        vulnerability=_component(entry.vulnerability),
        history=_component(entry.history),
        confidence=entry.confidence,
        zone_class=entry.zone_class,
        zone_id=entry.zone_id,
        phase=PhaseDecision(
            phase=entry.phase,
            reason=entry.phase_reason,
            rules_applied=entry.rules_applied,
            pending_checks=entry.pending_checks,
        ),
    )


@router.get("/habitations", response_model=HabitationPriorityResponse)
def priority_habitations() -> HabitationPriorityResponse:
    """The ranked decision list, with every component of every score."""
    result = baseline_priority()
    priority = MODEL_CONFIG.priority
    return HabitationPriorityResponse(
        habitations=[_row(entry) for entry in result.rows],
        totals_by_phase={
            phase: PhaseTotals(**totals) for phase, totals in result.totals().items()
        },
        total_population_assessed=sum(
            entry.habitation.population for entry in result.rows
        ),
        weights={
            "hazard": priority.w_hazard.value,
            "exposure": priority.w_exposure.value,
            "vulnerability": priority.w_vulnerability.value,
            "history": priority.w_history.value,
        },
        tier_thresholds={
            "IMMEDIATE": priority.tier_immediate_min_priority.value,
            "SHORT_TERM": priority.tier_short_term_min_priority.value,
            "MEDIUM_TERM": priority.tier_medium_term_min_priority.value,
        },
        classification_label=CLASSIFICATION_LABEL,
        decision_authority=DECISION_AUTHORITY,
        scenario_disclaimer=SCENARIO_DISCLAIMER,
        priority_note=PRIORITY_NOT_PROBABILITY,
        history_note=HISTORY_NOT_PREDICTION,
        computed_at=result.computed_at,
        model_config_version=result.config_version,
        engine_version=result.engine_version,
    )


@router.get("/habitations/{habitation_id}", response_model=HabitationDetailResponse)
def priority_habitation_detail(habitation_id: str) -> HabitationDetailResponse:
    """Why this habitation is ranked where it is - the full reasoning payload."""
    result = baseline_priority()
    match = next(
        (entry for entry in result.rows if entry.habitation.id == habitation_id), None
    )
    if match is None:
        raise HTTPException(
            status_code=404, detail=f"unknown habitation '{habitation_id}'"
        )
    index = result.rows.index(match)
    return HabitationDetailResponse(
        row=_row(match),
        habitation=match.habitation,
        explanation=priority_explanation(match, MODEL_CONFIG),
        priority_formula=get_formula("priority.score"),
        exposure_formula=get_formula("exposure.index"),
        vulnerability_formula=get_formula("vulnerability.index"),
        history_formula=get_formula("history.weighted_density"),
        peers_above=[entry.habitation.id for entry in result.rows[max(0, index - 2) : index]],
        peers_below=[entry.habitation.id for entry in result.rows[index + 1 : index + 3]],
    )
