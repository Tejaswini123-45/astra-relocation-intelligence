"""Plan endpoints: who goes where, in what order, and why not somewhere else.

Everything here is the CP-SAT solver's own output. The interface renders the
assignments it is given; it does not sort, filter or re-total them. "Why not
this site" is answered by re-solving with that assignment forced, so the answer
is an outcome rather than an explanation.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from astra.api.schemas import (
    AssignmentResponse,
    CounterfactualResponse,
    LivelihoodResponse,
    OptimiseRequest,
    PhasePlanTotals,
    PlanResponse,
    PlanTotalsResponse,
    RejectedOptionResponse,
    SiteLoadResponse,
    StrandedCapacityResponse,
    UnmetReasonResponse,
)
from astra.domain.enums import PhaseTier, SolverStatus
from astra.domain.model_config import MODEL_CONFIG
from astra.domain.notices import DECISION_AUTHORITY
from astra.engines.optimizer import PHASE_ORDER, Plan, RelocationOptimiser
from astra.engines.optimizer_service import (
    PlanInputs,
    baseline_plan,
    build_inputs,
    capacity_blocked,
    counterfactual,
    explain_unmet,
    solve_plan,
    stranded_capacity,
)
from astra.engines.routes_service import baseline_routes, evaluate_corridor
from astra.engines.service import baseline_risk

router = APIRouter(prefix="/plan", tags=["plan"])

MAX_CLOSED_SEGMENTS = 25


def _travel_ceiling(phase: PhaseTier) -> float:
    settings = MODEL_CONFIG.optimiser
    return {
        PhaseTier.IMMEDIATE: settings.max_travel_minutes_immediate.value,
        PhaseTier.SHORT_TERM: settings.max_travel_minutes_short_term.value,
        PhaseTier.MEDIUM_TERM: settings.max_travel_minutes_medium_term.value,
    }[phase]


def _capacity_share(phase: PhaseTier) -> float:
    settings = MODEL_CONFIG.optimiser
    return {
        PhaseTier.IMMEDIATE: settings.phase_capacity_share_immediate.value,
        PhaseTier.SHORT_TERM: settings.phase_capacity_share_short_term.value,
        PhaseTier.MEDIUM_TERM: settings.phase_capacity_share_medium_term.value,
    }[phase]


def _headline(plan: Plan, inputs: PlanInputs, stranded) -> str:
    """The one line an official reads first, assembled from computed values."""
    totals = plan.totals
    if totals.population_assigned == 0:
        return (
            "No assignment satisfies every hard constraint: no candidate site is both "
            "suitable and reliably reachable from any habitation needing relocation."
        )
    lead = (
        f"{totals.population_assigned:,} of {totals.population_assessed:,} residents "
        f"are placed across {totals.sites_used} site(s), at a mean "
        f"{totals.mean_travel_time_min:.0f} min and "
        f"{totals.mean_route_reliability * 100:.0f}% route reliability."
    )
    if totals.population_unmet == 0:
        return lead
    tail = f" {totals.population_unmet:,} residents are not placed."
    if stranded:
        worst = stranded[0]
        tail += (
            f" The largest single constraint is access, not capacity: "
            f"{worst.stranded_places:,} assessed places at {worst.site_id} cannot be "
            "reached by anyone still waiting."
        )
    return lead + tail


def serialise_plan(plan: Plan, inputs: PlanInputs, corridor) -> PlanResponse:
    run = baseline_risk()
    habitations = {h.id: h for h in run.context.habitations}
    sites = {s.id: s for s in run.context.sites}
    settings = MODEL_CONFIG.optimiser

    assignments: list[AssignmentResponse] = []
    for assignment in plan.assignments:
        habitation = habitations[assignment.habitation_id]
        site = sites[assignment.site_id]
        pair = corridor.pair(assignment.habitation_id, assignment.site_id)
        route = pair.best if pair else None
        disruption = inputs.livelihood[(assignment.habitation_id, assignment.site_id)]
        # Households are reported as a proportional equivalent, not a count of
        # named households: ASTRA assigns people, and rounding a share of a
        # village into whole households would invent a precision it does not have.
        share = assignment.people / habitation.population
        assignments.append(
            AssignmentResponse(
                habitation_id=assignment.habitation_id,
                habitation_name=habitation.name,
                site_id=assignment.site_id,
                site_name=site.name,
                phase=assignment.phase,
                people=assignment.people,
                households_equivalent=round(habitation.households * share),
                travel_time_min=assignment.travel_time_min,
                distance_km=route.distance_km if route else 0.0,
                route_reliability=assignment.route_reliability,
                route_risk=assignment.route_risk,
                livelihood_disruption=assignment.livelihood_disruption,
                livelihood=LivelihoodResponse(
                    value=disruption.value,
                    percent=disruption.percent,
                    commute_min=disruption.commute_min,
                    commute_reliability=disruption.commute_reliability,
                    worst_road_class=disruption.worst_road_class,
                    market_access_min=disruption.market_access_min,
                    reachable=disruption.reachable,
                    factors=disruption.factors,
                ),
                objective_contribution=assignment.objective_contribution,
                origin=habitation.centroid,
                destination=site.centroid,
                route_geometry=corridor.geometry_of(route) if route else [],
            )
        )

    phases: list[PhasePlanTotals] = []
    for phase in PHASE_ORDER:
        rows = [a for a in plan.assignments if a.phase is phase]
        people = sum(a.people for a in rows)
        phases.append(
            PhasePlanTotals(
                phase=phase,
                people_moved=people,
                habitations=len({a.habitation_id for a in rows}),
                sites_used=len({a.site_id for a in rows}),
                mean_travel_time_min=round(
                    sum(a.people * a.travel_time_min for a in rows) / people, 1
                )
                if people
                else 0.0,
                mean_route_reliability=round(
                    sum(a.people * a.route_reliability for a in rows) / people, 4
                )
                if people
                else 0.0,
                travel_ceiling_min=_travel_ceiling(phase),
                capacity_share=_capacity_share(phase),
            )
        )

    site_load = [
        SiteLoadResponse(
            site_id=site.site_id,
            site_name=sites[site.site_id].name,
            effective_capacity=site.effective_capacity,
            soft_capacity=site.soft_capacity,
            assigned=plan.site_usage.get(site.site_id, 0),
            remaining=site.effective_capacity - plan.site_usage.get(site.site_id, 0),
            utilisation=round(
                plan.site_usage.get(site.site_id, 0) / site.effective_capacity, 4
            )
            if site.effective_capacity
            else 0.0,
            over_soft_capacity=max(
                plan.site_usage.get(site.site_id, 0) - site.soft_capacity, 0
            ),
            phase_ceilings={
                phase.value: ceiling for phase, ceiling in site.phase_ceiling.items()
            },
        )
        for site in inputs.sites
    ]

    stranded = stranded_capacity(plan, inputs)
    return PlanResponse(
        status=plan.status,
        solver=(
            "OR-Tools CP-SAT"
            if plan.status is not SolverStatus.FALLBACK
            else "deterministic greedy fallback"
        ),
        solve_ms=plan.solve_ms,
        objective_value=plan.objective_value,
        objective_terms=plan.objective_terms,
        assignments=assignments,
        phases=phases,
        site_load=site_load,
        totals=PlanTotalsResponse(**vars(plan.totals)),
        unmet=[
            UnmetReasonResponse(
                habitation_id=entry.habitation_id,
                habitation_name=habitations[entry.habitation_id].name,
                people=entry.people,
                reason=entry.reason,
                detail=entry.detail,
            )
            for entry in explain_unmet(plan, inputs)
        ],
        stranded_capacity=[
            StrandedCapacityResponse(
                site_id=entry.site_id,
                site_name=sites[entry.site_id].name,
                capacity=entry.capacity,
                unused=entry.unused,
                reachable_unmet_people=entry.reachable_unmet_people,
                stranded_places=entry.stranded_places,
                detail=entry.detail,
            )
            for entry in stranded
        ],
        capacity_blocked=capacity_blocked(plan, inputs),
        rejected=[
            RejectedOptionResponse(
                habitation_id=entry.habitation_id,
                site_id=entry.site_id,
                reason=entry.reason,
                detail=entry.detail,
            )
            for entry in plan.rejected
        ],
        options_offered=len(inputs.options),
        notes=plan.notes,
        weights=[
            settings.beta_unmet_demand,
            settings.beta_travel_time,
            settings.beta_route_risk,
            settings.beta_site_overload,
            settings.beta_livelihood_disruption,
            settings.beta_fragmentation,
            settings.livelihood_w_travel_time,
            settings.livelihood_w_connectivity,
            settings.livelihood_w_road_reliability,
            settings.livelihood_w_market_access,
        ],
        constraints=[
            settings.min_assignment_block,
            settings.site_soft_capacity_share,
            settings.phase_capacity_share_immediate,
            settings.phase_capacity_share_short_term,
            settings.phase_capacity_share_medium_term,
            settings.max_travel_minutes_immediate,
            settings.max_travel_minutes_short_term,
            settings.max_travel_minutes_medium_term,
            settings.solver_time_limit_s,
            settings.solver_seed,
            MODEL_CONFIG.route.min_reliability_threshold,
        ],
        headline=_headline(plan, inputs, stranded),
        decision_authority=DECISION_AUTHORITY,
        model_config_version=MODEL_CONFIG.version,
        engine_version=MODEL_CONFIG.engine_version,
    )


@router.get("", response_model=PlanResponse)
def plan() -> PlanResponse:
    """The baseline optimised relocation plan."""
    solved, inputs = baseline_plan()
    return serialise_plan(solved, inputs, baseline_routes())


@router.post("/optimize", response_model=PlanResponse)
def optimize(request: OptimiseRequest) -> PlanResponse:
    """Re-solve, optionally under road closures or with the fallback.

    Closures are an argument, not a state change: the baseline plan is untouched
    and can be compared against this one.
    """
    if len(request.closed_segments) > MAX_CLOSED_SEGMENTS:
        raise HTTPException(
            status_code=422,
            detail=f"at most {MAX_CLOSED_SEGMENTS} segments may be closed",
        )
    network = baseline_routes().network
    unknown = [s for s in request.closed_segments if s not in network.by_id]
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"unknown road segments: {', '.join(unknown)}"
        )

    if not request.closed_segments and not request.use_fallback:
        solved, inputs = baseline_plan()
        return serialise_plan(solved, inputs, baseline_routes())

    corridor = (
        evaluate_corridor(closed_segments=frozenset(request.closed_segments))
        if request.closed_segments
        else baseline_routes()
    )
    inputs = build_inputs(corridor)
    if request.use_fallback:
        solved = RelocationOptimiser().greedy(
            inputs.options, inputs.sites, inputs.demand, rejected=inputs.rejected
        )
    else:
        solved, inputs = solve_plan(inputs)
    return serialise_plan(solved, inputs, corridor)


@router.get(
    "/why-not/{habitation_id}/{site_id}", response_model=CounterfactualResponse
)
def why_not(habitation_id: str, site_id: str, people: int | None = None):
    """Force this assignment, re-solve, and report what actually happened."""
    solved, inputs = baseline_plan()
    if habitation_id not in inputs.demand:
        raise HTTPException(
            status_code=404,
            detail=f"'{habitation_id}' has no relocation demand in this plan",
        )
    result = counterfactual(
        habitation_id, site_id, people=people, inputs=inputs, baseline=solved
    )
    return CounterfactualResponse(
        habitation_id=result.habitation_id,
        site_id=result.site_id,
        people=result.people,
        feasible=result.feasible,
        reason=result.reason,
        objective_baseline=result.objective_baseline,
        objective_forced=result.objective_forced,
        objective_delta=result.objective_delta,
        assigned_elsewhere_before=result.assigned_elsewhere_before,
        assigned_elsewhere_after=result.assigned_elsewhere_after,
        displaced=[[other, str(count)] for other, count in result.displaced],
        headline=result.headline,
    )
