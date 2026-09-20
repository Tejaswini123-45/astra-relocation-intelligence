"""Engine 7 - scenario construction and what-if recalculation.

A scenario is a first-class object, not a state the interface happens to be in.
It carries a list of typed perturbations, it is stored, and running it produces a
complete second assessment beside the baseline rather than replacing it. That is
what makes a before-and-after a comparison of two real results.

**Each perturbation enters the pipeline at exactly one stage**, and the stage
decides how far it propagates:

    RAINFALL_MULTIPLIER, LANDSLIDE_SHIFT   -> Engine 1, so everything downstream
    POPULATION_MULTIPLIER                  -> Engine 2, priority and demand
    SITE_CAPACITY_LOSS, SERVICE_UPGRADE,
    SITE_DISABLED                          -> Engine 4, capacity and suitability
    ROAD_CLOSURE                           -> Engine 5, routes and access

Nothing here mutates the baseline. Perturbed inputs are built as new objects, the
chain runs over them, and the caches that hold the baseline are untouched - so a
simulation can be run repeatedly and the baseline it is compared against is the
same one the rest of the product is showing.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import numpy as np

from astra.data import osm
from astra.data.scenarios import BASELINE as BASELINE_SCENARIO
from astra.domain.enums import PerturbationKind, ServiceType
from astra.domain.models import (
    CandidateSite,
    Habitation,
    Perturbation,
    Scenario,
    ServiceSupply,
)
from astra.domain.notices import SCENARIO_DISCLAIMER
from astra.engines.capacity import SiteCapacity
from astra.engines.capacity_service import compute_site_capacity
from astra.engines.context import EngineContext
from astra.engines.network import build_network
from astra.engines.optimizer import Plan
from astra.engines.optimizer_service import PlanInputs, build_inputs, solve_plan
from astra.engines.priority import PriorityEngine, PriorityResult
from astra.engines.routes_service import CorridorRoutes, evaluate_corridor
from astra.engines.service import RiskRun, compute_risk
from astra.settings import get_settings

#: Perturbations that change the hazard surface, and therefore force the network
#: to be rebuilt: a road's exposure is sampled from the surface it runs over.
HAZARD_KINDS = frozenset(
    {PerturbationKind.RAINFALL_MULTIPLIER, PerturbationKind.LANDSLIDE_SHIFT}
)


class ScenarioError(ValueError):
    """A scenario asked for something the study area does not contain."""


@dataclass
class ScenarioRun:
    """A complete assessment under one scenario."""

    scenario: Scenario
    risk: RiskRun
    priority: PriorityResult
    capacity: list[SiteCapacity]
    routes: CorridorRoutes
    plan: Plan
    plan_inputs: PlanInputs
    elapsed_ms: float
    stage_ms: dict[str, float] = field(default_factory=dict)

    @property
    def disabled_sites(self) -> set[str]:
        return {
            change.target
            for change in self.scenario.changes
            if change.kind is PerturbationKind.SITE_DISABLED and change.target
        }


# ---------------------------------------------------------------------------
# Applying perturbations
# ---------------------------------------------------------------------------


def perturb_context(context: EngineContext, changes: list[Perturbation]) -> EngineContext:
    """Return a new context with the Engine 1 and Engine 2 changes applied.

    Rainfall scales both the intensity surface and the extreme-rain-day surface,
    because a wetter monsoon is not only heavier on its worst day - it has more
    of them. The landslide shift is applied as an additive offset to the terrain
    instability inputs rather than to the finished score, so the score stays a
    function of its factors and the factor decomposition on screen stays honest.
    """
    surfaces = context.surfaces
    fixtures = context.fixtures
    rainfall = 1.0
    shift = 0.0
    population: dict[str | None, float] = {}

    for change in changes:
        if change.kind is PerturbationKind.RAINFALL_MULTIPLIER:
            rainfall *= change.value
        elif change.kind is PerturbationKind.LANDSLIDE_SHIFT:
            shift += change.value
        elif change.kind is PerturbationKind.POPULATION_MULTIPLIER:
            population[change.target] = population.get(change.target, 1.0) * change.value

    if rainfall != 1.0 or shift != 0.0:
        surfaces = replace(
            surfaces,
            rainfall_intensity_mm=surfaces.rainfall_intensity_mm * rainfall,
            extreme_rain_days=surfaces.extreme_rain_days * rainfall,
            # The shift is expressed against the normalised 0-1 scale the engine
            # works in, so it is applied to the raw inputs through the same
            # normalisation ranges the config declares.
            slope_deg=_shift_towards_max(surfaces.slope_deg, shift, ceiling=90.0),
            landcover_instability=_shift_towards_max(
                surfaces.landcover_instability, shift, ceiling=1.0
            ),
        )

    if population:
        fixtures = replace(
            fixtures,
            habitations=[
                _scale_population(habitation, population)
                for habitation in fixtures.habitations
            ],
        )

    if surfaces is context.surfaces and fixtures is context.fixtures:
        return context
    return replace(context, surfaces=surfaces, fixtures=fixtures)


def _shift_towards_max(values: np.ndarray, shift: float, *, ceiling: float) -> np.ndarray:
    """Move a factor surface a fraction of the way to its ceiling, or back from it."""
    if shift == 0.0:
        return values
    if shift > 0:
        return np.clip(values + shift * (ceiling - values), 0.0, ceiling)
    return np.clip(values * (1.0 + shift), 0.0, ceiling)


def _scale_population(
    habitation: Habitation, multipliers: dict[str | None, float]
) -> Habitation:
    factor = multipliers.get(habitation.id, 1.0) * multipliers.get(None, 1.0)
    if factor == 1.0:
        return habitation
    population = max(int(round(habitation.population * factor)), 1)
    households = max(int(round(habitation.households * factor)), 1)
    demographics = habitation.demographics
    scaled = demographics.model_copy(
        update={
            name: max(int(round(getattr(demographics, name) * factor)), 0)
            for name in (
                "elderly_60_plus",
                "children_under_5",
                "persons_with_disability",
                "medically_dependent",
                "low_income_households",
            )
            if hasattr(demographics, name)
        }
    )
    return habitation.model_copy(
        update={
            "population": population,
            "households": households,
            "demographics": scaled,
        }
    )


def perturb_sites(
    sites: list[CandidateSite], changes: list[Perturbation]
) -> list[CandidateSite]:
    """Apply the Engine 4 changes: capacity loss, service upgrade, withdrawal.

    A withdrawn site is removed from the list rather than given zero capacity. A
    site with zero capacity would still appear on the capacity screen with a
    bottleneck and an intervention that would 'unlock' it, which is not what
    withdrawing a candidate means.
    """
    disabled = {
        change.target
        for change in changes
        if change.kind is PerturbationKind.SITE_DISABLED and change.target
    }
    remaining = [site for site in sites if site.id not in disabled]

    result: list[CandidateSite] = []
    for site in remaining:
        services = list(site.services)
        shelter = site.existing_shelter_units
        constructable = site.constructable_units
        touched = False
        for change in changes:
            if change.target != site.id:
                continue
            if change.kind is PerturbationKind.SITE_CAPACITY_LOSS:
                keep = max(1.0 - change.value, 0.0)
                services = [
                    supply.model_copy(update={"supply": supply.supply * keep})
                    for supply in services
                ]
                shelter = int(shelter * keep)
                constructable = int(constructable * keep)
                touched = True
            elif change.kind is PerturbationKind.SERVICE_UPGRADE:
                service = _service_from_note(change)
                services = _add_supply(services, service, change.value)
                touched = True
        result.append(
            site.model_copy(
                update={
                    "services": services,
                    "existing_shelter_units": shelter,
                    "constructable_units": constructable,
                }
            )
            if touched
            else site
        )
    return result


def _service_from_note(change: Perturbation) -> ServiceType:
    """Which service a SERVICE_UPGRADE adds to, read from the note."""
    raw = (change.note or "").strip().upper()
    try:
        return ServiceType(raw)
    except ValueError as error:
        raise ScenarioError(
            "a SERVICE_UPGRADE must name the service in its note, one of: "
            + ", ".join(s.value for s in ServiceType)
        ) from error


def _add_supply(
    services: list[ServiceSupply], service: ServiceType, amount: float
) -> list[ServiceSupply]:
    updated = [
        supply.model_copy(update={"supply": supply.supply + amount})
        if supply.service is service
        else supply
        for supply in services
    ]
    if all(supply.service is not service for supply in services):
        if service in (ServiceType.LAND, ServiceType.ACCESS):
            raise ScenarioError(
                f"{service.value.lower()} is not a supply that can be delivered to a "
                "site: land capacity is measured off the buildable ground and access "
                "capacity off the road network. Neither is created by an upgrade, so "
                "ASTRA will not model one"
            )
        raise ScenarioError(
            f"this site records no {service.value} supply to upgrade; ASTRA will not "
            "invent a service where the fixture declares none"
        )
    return updated


def closed_segments(changes: list[Perturbation]) -> frozenset[str]:
    return frozenset(
        change.target
        for change in changes
        if change.kind is PerturbationKind.ROAD_CLOSURE and change.target
    )


# ---------------------------------------------------------------------------
# Running a scenario
# ---------------------------------------------------------------------------


def make_scenario(
    changes: list[Perturbation],
    *,
    name: str | None = None,
    description: str | None = None,
    baseline: Scenario,
    scenario_id: str | None = None,
) -> Scenario:
    """Build a versioned scenario object from a list of changes."""
    described = [change.describe() for change in changes]
    return Scenario(
        # Two simulations can land in the same millisecond. A colliding id would
        # silently overwrite the scenario an earlier result was traced to.
        id=scenario_id or f"sim-{int(time.time() * 1000):x}-{uuid.uuid4().hex[:6]}",
        name=name or (described[0] if described else "Unchanged baseline"),
        description=description
        or (
            "Simulated scenario: " + "; ".join(described)
            if described
            else "No perturbation applied."
        ),
        study_area_id=baseline.study_area_id,
        is_baseline=False,
        created_at=datetime.now(tz=UTC),
        disclaimer=SCENARIO_DISCLAIMER,
        changes=changes,
        derived_from=baseline.id,
    )


def run_scenario(scenario: Scenario, context: EngineContext) -> ScenarioRun:
    """Run the whole chain under one scenario, from hazard to plan."""
    started = time.perf_counter()
    stage: dict[str, float] = {}
    changes = scenario.changes

    mark = time.perf_counter()
    scenario_context = perturb_context(context, changes)
    sites = perturb_sites(list(scenario_context.sites), changes)
    if not sites:
        raise ScenarioError(
            "this scenario withdraws every candidate site; there is nothing to plan "
            "against"
        )
    scenario_context = replace(
        scenario_context,
        fixtures=replace(scenario_context.fixtures, sites=sites),
    )
    stage["perturb"] = (time.perf_counter() - mark) * 1000.0

    mark = time.perf_counter()
    risk = compute_risk(scenario_context)
    stage["hazard"] = (time.perf_counter() - mark) * 1000.0

    mark = time.perf_counter()
    priority = PriorityEngine().compute(risk)
    stage["priority"] = (time.perf_counter() - mark) * 1000.0

    mark = time.perf_counter()
    network = _network_for(risk, changes)
    routes = evaluate_corridor(
        closed_segments=closed_segments(changes),
        habitations=list(risk.context.habitations),
        sites=sites,
        network=network,
    )
    stage["routes"] = (time.perf_counter() - mark) * 1000.0

    mark = time.perf_counter()
    access = {
        site_id: entry.access_capacity_persons for site_id, entry in routes.access.items()
    }
    capacity = compute_site_capacity(risk, access=access)
    stage["capacity"] = (time.perf_counter() - mark) * 1000.0

    mark = time.perf_counter()
    inputs = build_inputs(routes, priority=priority, capacity=capacity)
    plan, inputs = solve_plan(inputs)
    stage["optimise"] = (time.perf_counter() - mark) * 1000.0

    return ScenarioRun(
        scenario=scenario,
        risk=risk,
        priority=priority,
        capacity=capacity,
        routes=routes,
        plan=plan,
        plan_inputs=inputs,
        elapsed_ms=round((time.perf_counter() - started) * 1000.0, 1),
        stage_ms={key: round(value, 1) for key, value in stage.items()},
    )


def _network_for(risk: RiskRun, changes: list[Perturbation]):
    """Rebuild the routed graph when the hazard surface under it has moved.

    Segment failure probability is sampled from the composite surface, so a
    rainfall scenario changes how survivable every road is. Reusing the baseline
    graph would leave the roads as safe as they were before the storm, which is
    exactly the assumption a what-if exists to test.
    """
    from astra.engines.routes_service import corridor_network

    if not any(change.kind in HAZARD_KINDS for change in changes):
        return corridor_network()
    path = get_settings().raw_dir / "osm" / "osm_alaknanda_network.json"
    return build_network(osm.load_ways(path), risk.grid, risk.result.composite)


def baseline_run() -> ScenarioRun:
    """The baseline assembled as a ScenarioRun, so a diff compares like with like.

    Reads the warmed caches rather than recomputing: the baseline a simulation is
    measured against must be the same one every other screen is showing, or the
    deltas describe a comparison nobody can see.
    """
    from astra.api.priority_router import baseline_priority
    from astra.engines.capacity_service import baseline_capacity
    from astra.engines.optimizer_service import baseline_plan
    from astra.engines.routes_service import baseline_routes
    from astra.engines.service import baseline_risk

    plan, inputs = baseline_plan()
    return ScenarioRun(
        scenario=BASELINE_SCENARIO,
        risk=baseline_risk(),
        priority=baseline_priority(),
        capacity=baseline_capacity(),
        routes=baseline_routes(),
        plan=plan,
        plan_inputs=inputs,
        elapsed_ms=0.0,
        stage_ms={},
    )


# ---------------------------------------------------------------------------
# The structured diff
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ZoneDelta:
    zone_class: str
    area_km2_before: float
    area_km2_after: float
    area_km2_delta: float
    population_before: int
    population_after: int


@dataclass(frozen=True)
class HabitationDelta:
    habitation_id: str
    name: str
    population: int
    priority_before: float
    priority_after: float
    priority_delta: float
    phase_before: str
    phase_after: str
    phase_changed: bool
    rank_before: int
    rank_after: int
    hazard_before: float
    hazard_after: float


@dataclass(frozen=True)
class SiteDelta:
    site_id: str
    name: str
    suitable_before: bool
    suitable_after: bool
    effective_before: float
    effective_after: float
    effective_delta: float
    bottleneck_before: str | None
    bottleneck_after: str | None
    withdrawn: bool
    #: Hard gates the site fails on each side. A site can keep every one of its
    #: service capacities and still stop being a candidate, because a gate is a
    #: yes or no and never a low score - so the diff has to name the gate rather
    #: than let an unchanged capacity figure imply nothing happened.
    failed_gates_before: tuple[str, ...] = ()
    failed_gates_after: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouteDelta:
    habitation_id: str
    site_id: str
    reliability_before: float
    reliability_after: float
    travel_before_min: float
    travel_after_min: float
    feasible_before: bool
    feasible_after: bool


@dataclass(frozen=True)
class AssignmentDelta:
    habitation_id: str
    site_id: str
    phase: str
    people_before: int
    people_after: int
    people_delta: int


@dataclass(frozen=True)
class ScenarioDiff:
    """Before and after, as two real assessments compared field by field."""

    scenario: Scenario
    zones: list[ZoneDelta]
    habitations: list[HabitationDelta]
    sites: list[SiteDelta]
    routes: list[RouteDelta]
    assignments: list[AssignmentDelta]
    tier_changes: list[HabitationDelta]
    newly_immediate_population: int
    placed_before: int
    placed_after: int
    unmet_before: int
    unmet_after: int
    effective_capacity_before: float
    effective_capacity_after: float
    feasible_routes_before: int
    feasible_routes_after: int
    critical_area_km2_before: float
    critical_area_km2_after: float
    headline: str
    elapsed_ms: float
    stage_ms: dict[str, float]


def _zone_areas(run: ScenarioRun) -> dict[str, tuple[float, int]]:
    totals: dict[str, tuple[float, int]] = {}
    for zone in run.risk.zones:
        area, population = totals.get(zone.zone_class.value, (0.0, 0))
        totals[zone.zone_class.value] = (
            area + zone.area_km2,
            population + zone.population_intersected,
        )
    return totals


def diff_runs(before: ScenarioRun, after: ScenarioRun) -> ScenarioDiff:
    """Compare two complete assessments. Nothing here is recomputed."""
    zone_before, zone_after = _zone_areas(before), _zone_areas(after)
    zones = [
        ZoneDelta(
            zone_class=zone_class,
            area_km2_before=round(zone_before.get(zone_class, (0.0, 0))[0], 3),
            area_km2_after=round(zone_after.get(zone_class, (0.0, 0))[0], 3),
            area_km2_delta=round(
                zone_after.get(zone_class, (0.0, 0))[0]
                - zone_before.get(zone_class, (0.0, 0))[0],
                3,
            ),
            population_before=zone_before.get(zone_class, (0.0, 0))[1],
            population_after=zone_after.get(zone_class, (0.0, 0))[1],
        )
        for zone_class in sorted(set(zone_before) | set(zone_after))
    ]

    rows_before = {row.id: row for row in before.priority.rows}
    rows_after = {row.id: row for row in after.priority.rows}
    habitations: list[HabitationDelta] = []
    newly_immediate = 0
    for habitation_id, row in rows_after.items():
        base = rows_before.get(habitation_id)
        if base is None:
            continue
        changed = base.phase is not row.phase
        if changed and row.phase.value == "IMMEDIATE":
            newly_immediate += row.habitation.population
        habitations.append(
            HabitationDelta(
                habitation_id=habitation_id,
                name=row.habitation.name,
                population=row.habitation.population,
                priority_before=round(base.priority_score, 1),
                priority_after=round(row.priority_score, 1),
                priority_delta=round(row.priority_score - base.priority_score, 1),
                phase_before=base.phase.value,
                phase_after=row.phase.value,
                phase_changed=changed,
                rank_before=base.rank,
                rank_after=row.rank,
                hazard_before=round(base.hazard.composite, 1),
                hazard_after=round(row.hazard.composite, 1),
            )
        )
    habitations.sort(key=lambda entry: entry.priority_delta, reverse=True)

    capacity_before = {entry.site.id: entry for entry in before.capacity}
    capacity_after = {entry.site.id: entry for entry in after.capacity}
    sites: list[SiteDelta] = []
    for site_id in sorted(set(capacity_before) | set(capacity_after)):
        base = capacity_before.get(site_id)
        now = capacity_after.get(site_id)
        reference = now or base
        assert reference is not None
        sites.append(
            SiteDelta(
                site_id=site_id,
                name=reference.site.name,
                suitable_before=bool(base and base.suitable),
                suitable_after=bool(now and now.suitable),
                effective_before=round(base.effective_capacity, 1) if base else 0.0,
                effective_after=round(now.effective_capacity, 1) if now else 0.0,
                effective_delta=round(
                    (now.effective_capacity if now else 0.0)
                    - (base.effective_capacity if base else 0.0),
                    1,
                ),
                bottleneck_before=(
                    base.bottleneck.value if base and base.bottleneck else None
                ),
                bottleneck_after=(
                    now.bottleneck.value if now and now.bottleneck else None
                ),
                withdrawn=now is None,
                failed_gates_before=(
                    tuple(gate.gate.value for gate in base.failed_gates) if base else ()
                ),
                failed_gates_after=(
                    tuple(gate.gate.value for gate in now.failed_gates) if now else ()
                ),
            )
        )

    routes: list[RouteDelta] = []
    for key, pair_after in after.routes.pairs.items():
        pair_before = before.routes.pairs.get(key)
        if pair_before is None:
            continue
        best_before, best_after = pair_before.best, pair_after.best
        if (
            abs(best_after.reliability - best_before.reliability) < 1e-6
            and abs(best_after.travel_time_min - best_before.travel_time_min) < 1e-6
        ):
            continue
        routes.append(
            RouteDelta(
                habitation_id=key[0],
                site_id=key[1],
                reliability_before=best_before.reliability,
                reliability_after=best_after.reliability,
                travel_before_min=best_before.travel_time_min,
                travel_after_min=best_after.travel_time_min,
                feasible_before=pair_before.feasible,
                feasible_after=pair_after.feasible,
            )
        )
    routes.sort(key=lambda entry: entry.reliability_after - entry.reliability_before)

    def by_pair(run: ScenarioRun) -> dict[tuple[str, str, str], int]:
        totals: dict[tuple[str, str, str], int] = {}
        for assignment in run.plan.assignments:
            key = (assignment.habitation_id, assignment.site_id, assignment.phase.value)
            totals[key] = totals.get(key, 0) + assignment.people
        return totals

    moves_before, moves_after = by_pair(before), by_pair(after)
    assignments = [
        AssignmentDelta(
            habitation_id=key[0],
            site_id=key[1],
            phase=key[2],
            people_before=moves_before.get(key, 0),
            people_after=moves_after.get(key, 0),
            people_delta=moves_after.get(key, 0) - moves_before.get(key, 0),
        )
        for key in sorted(set(moves_before) | set(moves_after))
        if moves_before.get(key, 0) != moves_after.get(key, 0)
    ]
    assignments.sort(key=lambda entry: entry.people_delta)

    tier_changes = [entry for entry in habitations if entry.phase_changed]
    critical_before = zone_before.get("CRITICAL", (0.0, 0))[0]
    critical_after = zone_after.get("CRITICAL", (0.0, 0))[0]

    return ScenarioDiff(
        scenario=after.scenario,
        zones=zones,
        habitations=habitations,
        sites=sites,
        routes=routes,
        assignments=assignments,
        tier_changes=tier_changes,
        newly_immediate_population=newly_immediate,
        placed_before=before.plan.totals.population_assigned,
        placed_after=after.plan.totals.population_assigned,
        unmet_before=before.plan.totals.population_unmet,
        unmet_after=after.plan.totals.population_unmet,
        effective_capacity_before=round(
            sum(e.effective_capacity for e in before.capacity if e.suitable), 1
        ),
        effective_capacity_after=round(
            sum(e.effective_capacity for e in after.capacity if e.suitable), 1
        ),
        feasible_routes_before=sum(
            1 for pair in before.routes.pairs.values() if pair.feasible
        ),
        feasible_routes_after=sum(
            1 for pair in after.routes.pairs.values() if pair.feasible
        ),
        critical_area_km2_before=round(critical_before, 3),
        critical_area_km2_after=round(critical_after, 3),
        headline=_diff_headline(
            critical_before,
            critical_after,
            before.plan.totals.population_assigned,
            after.plan.totals.population_assigned,
            tier_changes,
            newly_immediate,
        ),
        elapsed_ms=after.elapsed_ms,
        stage_ms=after.stage_ms,
    )


def _diff_headline(
    critical_before: float,
    critical_after: float,
    placed_before: int,
    placed_after: int,
    tier_changes: list[HabitationDelta],
    newly_immediate: int,
) -> str:
    """The one line an official reads first, assembled from the computed deltas."""
    parts: list[str] = []
    area_delta = critical_after - critical_before
    if abs(area_delta) >= 0.01:
        parts.append(
            f"Critical zone area moves {critical_before:.1f} to "
            f"{critical_after:.1f} km2 ({area_delta:+.1f})"
        )
    if tier_changes:
        parts.append(
            f"{len(tier_changes)} habitation(s) change phase"
            + (
                f", {newly_immediate:,} residents newly requiring immediate action"
                if newly_immediate
                else ""
            )
        )
    placed_delta = placed_after - placed_before
    if placed_delta:
        parts.append(
            f"the plan places {placed_after:,} instead of {placed_before:,} "
            f"({placed_delta:+,})"
        )
    if not parts:
        return (
            "This scenario changes no zone area, no phase and no assignment. The "
            "perturbation is real; the outputs are unchanged."
        )
    return "Under this scenario, " + "; ".join(parts) + "."
