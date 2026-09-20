"""Wiring Engine 6 to everything the earlier engines computed.

The optimiser takes options, site states and demand. This module is where those
come from, and it is the only place in ASTRA where all five earlier engines meet:

    Engine 1  hazard      -> which zones a site must stand clear of
    Engine 2  priority    -> the weight on leaving someone behind
    Engine 3  phasing     -> which phase a habitation moves in, and its ceilings
    Engine 4  capacity    -> how many people a site can actually hold
    Engine 5  routes      -> whether the road to it holds, and how long it takes

**Every pairing that is not offered to the solver is recorded with the constraint
that removed it.** That list is what makes "why not this site" answerable without
guessing, and it is served alongside the plan rather than being reconstructed
afterwards from prose.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from astra.domain.enums import PhaseTier, RoadClass, SolverStatus
from astra.domain.model_config import MODEL_CONFIG
from astra.engines.capacity_service import baseline_capacity
from astra.engines.livelihood import LivelihoodDisruption, livelihood_disruption
from astra.engines.network import RoadNetwork
from astra.engines.optimizer import (
    PHASE_ORDER,
    Option,
    Plan,
    RejectedOption,
    RelocationOptimiser,
    SiteState,
)
from astra.engines.routes import RouteEngine
from astra.engines.routes_service import CorridorRoutes, baseline_routes
from astra.engines.service import baseline_risk

#: Road classes that carry a district's markets, banks and offices. Travel time
#: to the nearest of these is ASTRA's measurable proxy for service access; it is
#: not a survey of where those services actually are, and the site panel says so.
TRUNK_CLASSES = frozenset({RoadClass.NATIONAL_HIGHWAY, RoadClass.STATE_HIGHWAY})

REASON_GATE = "SITE_FAILS_SUITABILITY_GATE"
REASON_ROUTE = "ROUTE_BELOW_RELIABILITY_THRESHOLD"
REASON_UNREACHABLE = "NO_ROAD_ROUTE"
REASON_TRAVEL_CEILING = "TRAVEL_TIME_EXCEEDS_PHASE_CEILING"
REASON_NOT_PRIORITISED = "HABITATION_NOT_PRIORITISED_FOR_RELOCATION"


@dataclass(frozen=True)
class PlanInputs:
    """Everything the solver was given, kept so a plan can be re-derived exactly."""

    options: list[Option]
    sites: list[SiteState]
    demand: dict[str, int]
    rejected: list[RejectedOption]
    livelihood: dict[tuple[str, str], LivelihoodDisruption]
    phase_of: dict[str, PhaseTier]
    priority_of: dict[str, float]


def _phase_ceilings(effective_capacity: int) -> dict[PhaseTier, int]:
    """Cumulative capacity available by the end of each phase."""
    settings = MODEL_CONFIG.optimiser
    shares = {
        PhaseTier.IMMEDIATE: settings.phase_capacity_share_immediate.value,
        PhaseTier.SHORT_TERM: settings.phase_capacity_share_short_term.value,
        PhaseTier.MEDIUM_TERM: settings.phase_capacity_share_medium_term.value,
    }
    return {phase: int(effective_capacity * share) for phase, share in shares.items()}


def _travel_ceiling(phase: PhaseTier) -> float:
    settings = MODEL_CONFIG.optimiser
    return {
        PhaseTier.IMMEDIATE: settings.max_travel_minutes_immediate.value,
        PhaseTier.SHORT_TERM: settings.max_travel_minutes_short_term.value,
        PhaseTier.MEDIUM_TERM: settings.max_travel_minutes_medium_term.value,
    }.get(phase, settings.max_travel_minutes_medium_term.value)


@lru_cache(maxsize=1)
def _market_access_minutes() -> dict[str, float]:
    """Routed travel time from each site to the nearest trunk road.

    One multi-source Dijkstra from every trunk-road node, on travel time, rather
    than a search per site per node: the answer is the same and it takes
    milliseconds instead of minutes.

    Computed on the open network, because this is a property of where a site is
    rather than of a scenario's closures. A site with no path to a trunk road at
    all is reported at the market ceiling, which scores it as maximally cut off -
    not as zero, which would score it as perfectly connected.
    """
    import networkx as nx

    corridor = baseline_routes()
    network: RoadNetwork = corridor.network
    graph = RouteEngine(network=network).graph
    trunk_nodes = [
        node_id
        for segment in network.segments
        if segment.road_class in TRUNK_CLASSES
        for node_id in (segment.from_node, segment.to_node)
    ]
    ceiling = MODEL_CONFIG.optimiser.livelihood_market_ceiling_min.value
    if not trunk_nodes:
        return {site.id: ceiling for site in baseline_risk().context.sites}

    distances = nx.multi_source_dijkstra_path_length(
        graph, set(trunk_nodes), weight="time_min"
    )
    minutes: dict[str, float] = {}
    for site in baseline_risk().context.sites:
        node, _ = network.nearest_node(site.centroid.lon, site.centroid.lat)
        minutes[site.id] = round(distances.get(node.id, ceiling), 1)
    return minutes


def build_inputs(
    corridor: CorridorRoutes | None = None,
    *,
    priority=None,
    capacity=None,
) -> PlanInputs:
    """Assemble the solver's inputs from the five engines that precede it.

    ``priority`` and ``capacity`` are supplied by a scenario run, which computes
    its own. Omitting them uses the cached baseline, which is what the ordinary
    plan endpoint wants.
    """
    from astra.api.priority_router import baseline_priority

    corridor = corridor or baseline_routes()
    priority = priority if priority is not None else baseline_priority()
    entries = capacity if capacity is not None else baseline_capacity()
    capacity = {entry.site.id: entry for entry in entries}
    market_access = _market_access_minutes()
    scenario_sites = {entry.site.id: entry.site for entry in entries}
    settings = MODEL_CONFIG.optimiser
    soft_share = settings.site_soft_capacity_share.value

    sites: list[SiteState] = []
    for site_id, entry in capacity.items():
        if not entry.suitable:
            continue
        effective = int(entry.effective_capacity)
        sites.append(
            SiteState(
                site_id=site_id,
                effective_capacity=effective,
                soft_capacity=int(effective * soft_share),
                phase_ceiling=_phase_ceilings(effective),
            )
        )

    options: list[Option] = []
    rejected: list[RejectedOption] = []
    demand: dict[str, int] = {}
    livelihood: dict[tuple[str, str], LivelihoodDisruption] = {}
    phase_of: dict[str, PhaseTier] = {}
    priority_of: dict[str, float] = {}

    for row in priority.rows:
        habitation = row.habitation
        phase_of[habitation.id] = row.phase
        priority_of[habitation.id] = row.priority_score
        if row.phase not in PHASE_ORDER:
            # A habitation the phasing engine did not select for relocation is not
            # demand. Recorded so the plan totals still account for everyone.
            for site_id in capacity:
                rejected.append(
                    RejectedOption(
                        habitation_id=habitation.id,
                        site_id=site_id,
                        reason=REASON_NOT_PRIORITISED,
                        detail=(
                            f"{habitation.name} is in phase {row.phase.value}, which "
                            "the phasing engine does not schedule for relocation."
                        ),
                    )
                )
            continue

        demand[habitation.id] = habitation.population
        # Engine 3 gives a habitation its *earliest* phase, not its only one. A
        # village of 487 people is not moved in a single afternoon; the plan is
        # allowed to move part of it now and the rest as capacity and access
        # allow, which is what a phased relocation means. Later phases have
        # looser travel ceilings, so a site out of reach for an immediate move
        # can still be a short-term destination.
        eligible = PHASE_ORDER[PHASE_ORDER.index(row.phase) :]
        for site_id, entry in capacity.items():
            if not entry.suitable:
                gates = ", ".join(
                    g.gate.value.lower().replace("_", " ") for g in entry.failed_gates
                )
                rejected.append(
                    RejectedOption(
                        habitation_id=habitation.id,
                        site_id=site_id,
                        reason=REASON_GATE,
                        detail=(
                            f"{entry.site.name} fails {gates}. A gate is a hard "
                            "constraint, so it is not a destination at any distance."
                        ),
                    )
                )
                continue

            pair = corridor.pair(habitation.id, site_id)
            if pair is None or not pair.best.legs:
                rejected.append(
                    RejectedOption(
                        habitation_id=habitation.id,
                        site_id=site_id,
                        reason=REASON_UNREACHABLE,
                        detail=(
                            f"No road route runs from {habitation.name} to "
                            f"{entry.site.name} on the mapped network."
                        ),
                    )
                )
                continue

            route = pair.best
            if not pair.feasible:
                rejected.append(
                    RejectedOption(
                        habitation_id=habitation.id,
                        site_id=site_id,
                        reason=REASON_ROUTE,
                        detail=(
                            f"The best route to {entry.site.name} is "
                            f"{route.reliability * 100:.0f}% reliable, below the "
                            f"{MODEL_CONFIG.route.min_reliability_threshold.value * 100:.0f}% "
                            "threshold. A road ASTRA would not plan a convoy down is "
                            "not a slightly worse option."
                        ),
                    )
                )
                continue

            phases = [
                phase
                for phase in eligible
                if route.travel_time_min <= _travel_ceiling(phase)
            ]
            if not phases:
                loosest = _travel_ceiling(eligible[-1])
                rejected.append(
                    RejectedOption(
                        habitation_id=habitation.id,
                        site_id=site_id,
                        reason=REASON_TRAVEL_CEILING,
                        detail=(
                            f"{route.travel_time_min:.0f} min exceeds the "
                            f"{loosest:.0f} min ceiling of even the "
                            f"{eligible[-1].value.replace('_', '-').lower()} phase, "
                            f"which is the loosest available to {habitation.name}."
                        ),
                    )
                )
                continue

            disruption = _disruption_for(
                habitation_id=habitation.id,
                site_id=site_id,
                route_reliability=route.reliability,
                worst_road_class=_worst_class(route),
                market_access_min=market_access.get(site_id, 0.0),
                corridor=corridor,
                habitation=habitation,
                sites_by_id=scenario_sites,
            )
            livelihood[(habitation.id, site_id)] = disruption
            for phase in phases:
                options.append(
                    Option(
                        delay_steps=PHASE_ORDER.index(phase)
                        - PHASE_ORDER.index(row.phase),
                        habitation_id=habitation.id,
                        site_id=site_id,
                        phase=phase,
                        population=habitation.population,
                        priority=row.priority_score,
                        travel_time_min=route.travel_time_min,
                        route_risk=route.risk,
                        route_reliability=route.reliability,
                        livelihood_disruption=disruption.value,
                    )
                )

    return PlanInputs(
        options=options,
        sites=sorted(sites, key=lambda s: s.site_id),
        demand=demand,
        rejected=rejected,
        livelihood=livelihood,
        phase_of=phase_of,
        priority_of=priority_of,
    )


@lru_cache(maxsize=1)
def _sites_by_id():
    return {site.id: site for site in baseline_risk().context.sites}


def _worst_class(route) -> RoadClass:
    """The weakest road class anywhere on the route. A chain is its weakest link."""
    order = [
        RoadClass.NATIONAL_HIGHWAY,
        RoadClass.STATE_HIGHWAY,
        RoadClass.DISTRICT_ROAD,
        RoadClass.VILLAGE_ROAD,
        RoadClass.TRACK,
    ]
    if not route.legs:
        return RoadClass.TRACK
    return max(route.legs, key=lambda leg: order.index(leg.road_class)).road_class


def _disruption_for(
    *,
    habitation_id: str,
    site_id: str,
    route_reliability: float,
    worst_road_class: RoadClass,
    market_access_min: float,
    corridor: CorridorRoutes,
    habitation,
    sites_by_id: dict,
) -> LivelihoodDisruption:
    """Route the site back to where these residents work, then score it."""
    centre = habitation.livelihood_centre or habitation.centroid
    site = sites_by_id.get(site_id) or _sites_by_id().get(site_id)
    if site is None:
        return livelihood_disruption(
            habitation_id=habitation_id,
            site_id=site_id,
            commute_min=0.0,
            commute_reliability=0.0,
            worst_road_class=worst_road_class,
            market_access_min=market_access_min,
            reachable=False,
        )
    engine = RouteEngine(
        network=corridor.network, closed_segments=corridor.closed_segments
    )
    commute = engine.pair(
        origin_id=site_id,
        destination_id=f"{habitation_id}-livelihood",
        origin=(site.centroid.lon, site.centroid.lat),
        destination=(centre.lon, centre.lat),
    ).best
    return livelihood_disruption(
        habitation_id=habitation_id,
        site_id=site_id,
        commute_min=commute.travel_time_min,
        commute_reliability=commute.reliability,
        worst_road_class=(
            _worst_class(commute) if commute.legs else worst_road_class
        ),
        market_access_min=market_access_min,
        reachable=bool(commute.legs),
    )


def solve_plan(inputs: PlanInputs | None = None) -> tuple[Plan, PlanInputs]:
    """Solve the baseline relocation plan."""
    inputs = inputs or build_inputs()
    plan = RelocationOptimiser().solve(
        inputs.options, inputs.sites, inputs.demand, rejected=inputs.rejected
    )
    return plan, inputs


@lru_cache(maxsize=1)
def baseline_plan() -> tuple[Plan, PlanInputs]:
    """The cached baseline plan, warmed at API startup."""
    return solve_plan()

# ---------------------------------------------------------------------------
# Why the plan leaves anyone behind
# ---------------------------------------------------------------------------

UNMET_NO_DESTINATION = "NO_FEASIBLE_DESTINATION"
UNMET_CAPACITY_EXHAUSTED = "CAPACITY_EXHAUSTED"
UNMET_OUTWEIGHED = "MOVE_COST_OUTWEIGHED_THE_BENEFIT"


@dataclass(frozen=True)
class UnmetReason:
    """Why these people were not moved. Three different problems, three answers."""

    habitation_id: str
    people: int
    reason: str
    detail: str


@dataclass(frozen=True)
class StrandedCapacity:
    """Assessed capacity that the people who still need it cannot reach."""

    site_id: str
    capacity: int
    unused: int
    reachable_unmet_people: int
    stranded_places: int
    detail: str


def explain_unmet(plan: Plan, inputs: PlanInputs) -> list[UnmetReason]:
    """Name the constraint behind every person the plan does not move.

    "1,375 people unmet" is a fact and not yet an insight. Whether they are
    unmet because nowhere safe can be reached from where they live, because the
    reachable sites are full, or because moving them costs more than the model
    thinks it is worth are three completely different problems with three
    completely different interventions, and an SDMA needs to know which it has.
    """
    options_by_habitation: dict[str, list[Option]] = {}
    for option in inputs.options:
        options_by_habitation.setdefault(option.habitation_id, []).append(option)

    headroom = {
        site.site_id: site.effective_capacity - plan.site_usage.get(site.site_id, 0)
        for site in inputs.sites
    }
    reasons: list[UnmetReason] = []
    for habitation_id, people in sorted(plan.unmet.items()):
        if people <= 0:
            continue
        options = options_by_habitation.get(habitation_id, [])
        if not options:
            blocking = sorted(
                {
                    entry.reason
                    for entry in inputs.rejected
                    if entry.habitation_id == habitation_id
                    and entry.reason != REASON_GATE
                }
            )
            named = ", ".join(reason.lower().replace("_", " ") for reason in blocking)
            reasons.append(
                UnmetReason(
                    habitation_id=habitation_id,
                    people=people,
                    reason=UNMET_NO_DESTINATION,
                    detail=(
                        "No candidate site is both suitable and reliably reachable "
                        f"from here. Every option was removed by: {named or 'suitability gates'}. "
                        "The intervention this points at is the road, not the site."
                    ),
                )
            )
            continue

        reachable = sorted({option.site_id for option in options})
        spare = {site_id: headroom.get(site_id, 0) for site_id in reachable}
        if all(value <= 0 for value in spare.values()):
            reasons.append(
                UnmetReason(
                    habitation_id=habitation_id,
                    people=people,
                    reason=UNMET_CAPACITY_EXHAUSTED,
                    detail=(
                        "Every site reachable from here is full: "
                        + ", ".join(reachable)
                        + ". Raising effective capacity at one of them, or opening "
                        "another route, is what moves these people."
                    ),
                )
            )
            continue

        available = ", ".join(
            f"{site_id} ({spare[site_id]} places)"
            for site_id in reachable
            if spare[site_id] > 0
        )
        reasons.append(
            UnmetReason(
                habitation_id=habitation_id,
                people=people,
                reason=UNMET_OUTWEIGHED,
                detail=(
                    f"Places remain at {available}, but under the configured weights "
                    "the travel, route risk and livelihood disruption of moving these "
                    "residents outweighed their priority-weighted benefit. This is a "
                    "policy trade-off, not a physical constraint: it changes when the "
                    "weights change."
                ),
            )
        )
    return reasons


def stranded_capacity(plan: Plan, inputs: PlanInputs) -> list[StrandedCapacity]:
    """Unused capacity measured against the people who could actually use it.

    This is the most actionable single figure the optimiser produces, and it is
    not "unused places". A site with 1,200 unused places that only twelve of the
    1,375 people still waiting can reach is not spare capacity - it is a road
    project with a number attached to it. The gap between the two is what gets
    reported.
    """
    needing = {
        habitation_id: people
        for habitation_id, people in plan.unmet.items()
        if people > 0
    }
    reachable_by_needy: dict[str, set[str]] = {}
    for option in inputs.options:
        if option.habitation_id in needing:
            reachable_by_needy.setdefault(option.site_id, set()).add(
                option.habitation_id
            )

    waiting = sum(needing.values())
    stranded: list[StrandedCapacity] = []
    for site in inputs.sites:
        unused = site.effective_capacity - plan.site_usage.get(site.site_id, 0)
        if unused <= 0:
            continue
        claimants = reachable_by_needy.get(site.site_id, set())
        reachable_people = sum(needing[h] for h in claimants)
        gap = unused - reachable_people
        if gap <= 0:
            continue
        stranded.append(
            StrandedCapacity(
                site_id=site.site_id,
                capacity=site.effective_capacity,
                unused=unused,
                reachable_unmet_people=reachable_people,
                stranded_places=gap,
                detail=(
                    f"{unused} assessed places at {site.site_id} are unused, and only "
                    f"{reachable_people} of the {waiting} people still waiting can "
                    "reach them on a route above the reliability threshold. "
                    f"{gap} places are stranded behind the road, not behind the site."
                ),
            )
        )
    stranded.sort(key=lambda entry: entry.stranded_places, reverse=True)
    return stranded


def capacity_blocked(plan: Plan, inputs: PlanInputs) -> list[str]:
    """Habitations with real demand and no destination at all.

    CLAUDE.md section 5.3 asks for these to be flagged distinctly rather than
    silently ranked, because a habitation that cannot be moved anywhere is not a
    low priority - it is a different problem.
    """
    served = {option.habitation_id for option in inputs.options}
    return sorted(
        habitation_id
        for habitation_id, people in plan.unmet.items()
        if people > 0 and habitation_id not in served
    )


# ---------------------------------------------------------------------------
# Counterfactuals: why not this site
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Counterfactual:
    """The answer to "why not that site", obtained by actually trying it."""

    habitation_id: str
    site_id: str
    people: int
    feasible: bool
    reason: str
    objective_baseline: float
    objective_forced: float | None
    objective_delta: float | None
    assigned_elsewhere_before: int
    assigned_elsewhere_after: int
    displaced: list[tuple[str, int]]
    headline: str


def counterfactual(
    habitation_id: str,
    site_id: str,
    *,
    people: int | None = None,
    inputs: PlanInputs | None = None,
    baseline: Plan | None = None,
) -> Counterfactual:
    """Force the assignment, re-solve, and report what actually happened.

    This is the difference between an explanation a judge accepts and one they
    poke a hole in. ASTRA does not narrate why a site was not chosen; it imposes
    the choice on the solver and reports the outcome - either the named hard
    constraint that makes it impossible, or the objective delta and the names of
    the people who would lose their place to make room.
    """
    if inputs is None or baseline is None:
        baseline, inputs = baseline_plan()

    demand = inputs.demand.get(habitation_id, 0)
    requested = people if people is not None else demand
    requested = max(min(requested, demand), 0)

    blocked = next(
        (
            entry
            for entry in inputs.rejected
            if entry.habitation_id == habitation_id and entry.site_id == site_id
        ),
        None,
    )
    before = sum(a.people for a in baseline.for_habitation(habitation_id))

    if blocked is not None or requested == 0:
        detail = (
            blocked.detail
            if blocked
            else f"{habitation_id} has no relocation demand in this plan."
        )
        return Counterfactual(
            habitation_id=habitation_id,
            site_id=site_id,
            people=requested,
            feasible=False,
            reason=blocked.reason if blocked else "NO_DEMAND",
            objective_baseline=baseline.objective_value,
            objective_forced=None,
            objective_delta=None,
            assigned_elsewhere_before=before,
            assigned_elsewhere_after=before,
            displaced=[],
            headline=detail,
        )

    forced_plan = RelocationOptimiser().solve(
        inputs.options,
        inputs.sites,
        inputs.demand,
        rejected=inputs.rejected,
        forced=(habitation_id, site_id, requested),
    )
    if forced_plan.status is SolverStatus.INFEASIBLE:
        return Counterfactual(
            habitation_id=habitation_id,
            site_id=site_id,
            people=requested,
            feasible=False,
            reason="INFEASIBLE_UNDER_HARD_CONSTRAINTS",
            objective_baseline=baseline.objective_value,
            objective_forced=None,
            objective_delta=None,
            assigned_elsewhere_before=before,
            assigned_elsewhere_after=before,
            displaced=[],
            headline=(
                f"Forcing {requested} people from {habitation_id} onto {site_id} "
                "leaves no assignment that satisfies every hard constraint. The site "
                "cannot take them without breaching capacity or a phase ceiling."
            ),
        )

    delta = round(forced_plan.objective_value - baseline.objective_value, 2)
    displaced = [
        (other, forced_plan.unmet.get(other, 0) - baseline.unmet.get(other, 0))
        for other in sorted(baseline.unmet)
        if forced_plan.unmet.get(other, 0) > baseline.unmet.get(other, 0)
    ]
    after = sum(a.people for a in forced_plan.for_habitation(habitation_id))
    displaced_total = sum(count for _, count in displaced)

    if delta <= 0:
        headline = (
            f"Forcing {requested} people onto {site_id} is not worse: the objective "
            f"moves by {delta:+.0f}. The chosen plan is one of several equally good "
            "ones on this measure."
        )
    else:
        if displaced:
            who = ", ".join(f"{other} (+{count})" for other, count in displaced[:4])
            headline = (
                f"Forcing {requested} people from {habitation_id} onto {site_id} is "
                f"feasible but worse by {delta:,.0f} on the objective, and it leaves "
                f"{displaced_total} more people unmet elsewhere: {who}."
            )
        else:
            headline = (
                f"Forcing {requested} people from {habitation_id} onto {site_id} is "
                f"feasible but worse by {delta:,.0f} on the objective - the extra "
                "travel, route risk and livelihood disruption of the longer journey - "
                "without freeing a place that helps anyone else."
            )

    return Counterfactual(
        habitation_id=habitation_id,
        site_id=site_id,
        people=requested,
        feasible=True,
        reason="FEASIBLE_BUT_SCORED",
        objective_baseline=baseline.objective_value,
        objective_forced=forced_plan.objective_value,
        objective_delta=delta,
        assigned_elsewhere_before=before,
        assigned_elsewhere_after=after,
        displaced=displaced,
        headline=headline,
    )
