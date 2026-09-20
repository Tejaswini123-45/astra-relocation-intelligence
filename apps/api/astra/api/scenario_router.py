"""Scenario endpoints: what the corridor looks like if something changes.

`POST /simulate` runs the whole chain under a set of typed perturbations and
returns a structured diff against the baseline. The baseline is not replaced and
is not recomputed - the comparison is between the assessment every other screen
is already showing and a second, complete one.

The simulated plan and the simulated zones come back through the same serialisers
the baseline endpoints use, so the What-If screen renders the same shapes the
Plan and Risk screens do rather than a parallel set that could drift.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from astra.api.plan_router import serialise_plan
from astra.api.risk_router import serialise_zones
from astra.api.schemas import (
    AssignmentDeltaResponse,
    HabitationDeltaResponse,
    PerturbationResponse,
    RouteDeltaResponse,
    ScenarioDiffResponse,
    SimulateRequest,
    SiteDeltaResponse,
    ZoneDeltaResponse,
)
from astra.data.scenarios import BASELINE, remember_scenario
from astra.domain.enums import PerturbationKind
from astra.domain.model_config import MODEL_CONFIG
from astra.domain.models import Perturbation
from astra.domain.notices import DECISION_AUTHORITY
from astra.engines.context import get_context
from astra.engines.routes_service import corridor_network
from astra.engines.scenario import (
    ScenarioDiff,
    ScenarioError,
    baseline_run,
    diff_runs,
    make_scenario,
    run_scenario,
)
from astra.engines.service import baseline_risk

router = APIRouter(tags=["scenarios"])

MAX_CHANGES = 12
"""A what-if is a question, not a rebuild of the study area."""


def _validate(changes: list[Perturbation]) -> None:
    """Reject a scenario that names something the study area does not contain.

    A perturbation aimed at a site or a road that does not exist would otherwise
    apply silently to nothing and produce a diff of zeroes, which reads as "this
    change makes no difference" rather than "this change was never applied".
    """
    if len(changes) > MAX_CHANGES:
        raise HTTPException(
            status_code=422,
            detail=f"at most {MAX_CHANGES} perturbations may be applied at once",
        )
    run = baseline_risk()
    sites = {site.id for site in run.context.sites}
    habitations = {habitation.id for habitation in run.context.habitations}
    segments = set(corridor_network().by_id)

    for change in changes:
        target = change.target
        if change.kind is PerturbationKind.ROAD_CLOSURE:
            if target not in segments:
                raise HTTPException(
                    status_code=422, detail=f"unknown road segment '{target}'"
                )
        elif change.kind in (
            PerturbationKind.SITE_CAPACITY_LOSS,
            PerturbationKind.SERVICE_UPGRADE,
            PerturbationKind.SITE_DISABLED,
        ):
            if target not in sites:
                raise HTTPException(
                    status_code=422, detail=f"unknown candidate site '{target}'"
                )
            # A capacity loss is a share of what is there. Accepting 5.0 and
            # clamping it would report a scenario nobody asked for as if it had
            # been applied as written.
            if change.kind is PerturbationKind.SITE_CAPACITY_LOSS and not (
                0.0 < change.value <= 1.0
            ):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "a site capacity loss is a share of the site's supply and "
                        "must be greater than 0 and at most 1"
                    ),
                )
            if change.kind is PerturbationKind.SERVICE_UPGRADE and change.value <= 0:
                raise HTTPException(
                    status_code=422,
                    detail="a service upgrade must add a positive quantity of supply",
                )
        elif change.kind is PerturbationKind.POPULATION_MULTIPLIER:
            if target is not None and target not in habitations:
                raise HTTPException(
                    status_code=422, detail=f"unknown habitation '{target}'"
                )
            if change.value <= 0:
                raise HTTPException(
                    status_code=422,
                    detail="a population multiplier must be greater than zero",
                )
        elif change.kind is PerturbationKind.RAINFALL_MULTIPLIER and change.value <= 0:
            raise HTTPException(
                status_code=422,
                detail="a rainfall multiplier must be greater than zero",
            )
        elif change.kind is PerturbationKind.LANDSLIDE_SHIFT and not (
            -1.0 <= change.value <= 1.0
        ):
            # The shift is a fraction of the distance to a factor's ceiling, so
            # outside -1..1 it stops meaning anything the engine can honour.
            raise HTTPException(
                status_code=422,
                detail=(
                    "a landslide susceptibility shift is a fraction of the distance "
                    "to the factor ceiling and must lie between -1 and 1"
                ),
            )


def _serialise(diff: ScenarioDiff, run) -> ScenarioDiffResponse:
    return ScenarioDiffResponse(
        scenario=diff.scenario,
        changes=[
            PerturbationResponse(
                kind=change.kind,
                target=change.target,
                value=change.value,
                note=change.note,
                description=change.describe(),
            )
            for change in diff.scenario.changes
        ],
        headline=diff.headline,
        zones=[ZoneDeltaResponse(**vars(entry)) for entry in diff.zones],
        habitations=[
            HabitationDeltaResponse(**vars(entry)) for entry in diff.habitations
        ],
        sites=[SiteDeltaResponse(**vars(entry)) for entry in diff.sites],
        routes=[RouteDeltaResponse(**vars(entry)) for entry in diff.routes],
        assignments=[
            AssignmentDeltaResponse(**vars(entry)) for entry in diff.assignments
        ],
        tier_changes=[
            HabitationDeltaResponse(**vars(entry)) for entry in diff.tier_changes
        ],
        newly_immediate_population=diff.newly_immediate_population,
        placed_before=diff.placed_before,
        placed_after=diff.placed_after,
        unmet_before=diff.unmet_before,
        unmet_after=diff.unmet_after,
        effective_capacity_before=diff.effective_capacity_before,
        effective_capacity_after=diff.effective_capacity_after,
        feasible_routes_before=diff.feasible_routes_before,
        feasible_routes_after=diff.feasible_routes_after,
        critical_area_km2_before=diff.critical_area_km2_before,
        critical_area_km2_after=diff.critical_area_km2_after,
        plan_after=serialise_plan(run.plan, run.plan_inputs, run.routes),
        zones_after=serialise_zones(run.risk),
        elapsed_ms=diff.elapsed_ms,
        stage_ms=diff.stage_ms,
        decision_authority=DECISION_AUTHORITY,
        model_config_version=MODEL_CONFIG.version,
        engine_version=MODEL_CONFIG.engine_version,
    )


@router.post("/simulate", response_model=ScenarioDiffResponse)
def simulate(request: SimulateRequest) -> ScenarioDiffResponse:
    """Run the full chain under these changes and diff it against the baseline."""
    _validate(request.changes)
    scenario = make_scenario(
        request.changes,
        name=request.name,
        description=request.description,
        baseline=BASELINE,
    )
    try:
        run = run_scenario(scenario, get_context())
    except ScenarioError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    # Stored so the scenario that produced a result can be looked up by id later,
    # which is what makes an audit record point at something. The store is bounded:
    # a slider dragged for a minute must not become a memory leak.
    remember_scenario(scenario)

    # A what-if is a decision point too: an officer may act on it, and a brief
    # citing it has to be traceable to the exact perturbed state that produced
    # it. Failing to write the row does not lose the simulation.
    decision_id: str | None = None
    try:
        from astra.engines.audit import record_decision

        decision_id = record_decision(
            scenario_id=scenario.id,
            trigger="simulate",
            plan=run.plan,
            plan_inputs=run.plan_inputs,
            priority=run.priority,
            capacity=run.capacity,
            routes=run.routes,
            notes=scenario.description,
        ).id
    except Exception:  # noqa: BLE001 - the diff stands without its ledger row
        decision_id = None

    response = _serialise(diff_runs(baseline_run(), run), run)
    return response.model_copy(update={"decision_id": decision_id})
