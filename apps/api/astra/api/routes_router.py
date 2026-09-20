"""Route endpoints: can these people reach that site, and will the road hold.

Everything here is computed by Engine 5 over the vendored OSM network and the
hazard surface Engine 1 produced. Closures are evaluated as a second, complete
assessment beside the baseline, so a before-and-after is a comparison of two real
results rather than a differently-rendered one.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from astra.api.schemas import (
    ClosureImpactResponse,
    ClosureRequest,
    NetworkSummaryResponse,
    PlanDependencyListResponse,
    PlanDependencyResponse,
    PointOfFailureResponse,
    RouteAssessmentResponse,
    RouteDeltaRow,
    RouteMatrixRow,
    RoutePairResponse,
    RouteResponse,
    SegmentLegResponse,
    SiteAccessResponse,
)
from astra.domain.model_config import MODEL_CONFIG
from astra.domain.notices import DECISION_AUTHORITY
from astra.engines.capacity_service import baseline_capacity
from astra.engines.routes import Route, RoutePair
from astra.engines.routes_service import (
    CorridorRoutes,
    baseline_routes,
    corridor_network,
    evaluate_corridor,
    network_summary,
)
from astra.engines.service import baseline_risk

router = APIRouter(prefix="/routes", tags=["routes"])

MAX_CLOSED_SEGMENTS = 25
"""A scenario closes roads, it does not dismantle the network. Bounded so a
request cannot turn into an unbounded re-evaluation."""


def _leg(leg) -> SegmentLegResponse:
    return SegmentLegResponse(
        segment_id=leg.segment_id,
        name=leg.name,
        road_class=leg.road_class,
        length_m=leg.length_m,
        travel_time_min=leg.travel_time_min,
        hazard_max=leg.hazard_max,
        hazard_coverage=leg.hazard_coverage,
        is_bridge=leg.is_bridge,
        p_fail=leg.p_fail,
    )


def _route(route: Route, corridor: CorridorRoutes) -> RouteResponse:
    return RouteResponse(
        origin_id=route.origin_id,
        destination_id=route.destination_id,
        profile=route.profile,
        travel_time_min=route.travel_time_min,
        distance_km=route.distance_km,
        reliability=route.reliability,
        risk=route.risk,
        off_network_m=route.off_network_m,
        off_network_min=route.off_network_min,
        hazard_segment_count=route.hazard_segment_count,
        hazard_exposed_km=route.hazard_exposed_km,
        longest_hazard_run_km=route.longest_hazard_run_km,
        bridges_crossed=route.bridges_crossed,
        feasible=route.feasible,
        scored_share=route.scored_share,
        infeasible_reason=route.infeasible_reason,
        legs=[_leg(leg) for leg in route.legs],
        points_of_failure=[
            PointOfFailureResponse(
                segment_id=point.segment_id,
                name=point.name,
                reason=point.reason,
                p_fail=point.p_fail,
                length_m=point.length_m,
                no_alternative=point.no_alternative,
            )
            for point in route.points_of_failure
        ],
        geometry=corridor.geometry_of(route),
        provenance=route.provenance,
    )


def _pair(pair: RoutePair, corridor: CorridorRoutes) -> RoutePairResponse:
    return RoutePairResponse(
        origin_id=pair.origin_id,
        destination_id=pair.destination_id,
        fastest=_route(pair.fastest, corridor),
        safest=_route(pair.safest, corridor),
        profiles_differ=pair.profiles_differ,
        minutes_paid=pair.minutes_paid,
        reliability_gained=pair.reliability_gained,
        tradeoff=pair.tradeoff_sentence(),
        feasible=pair.feasible,
    )


def _redundancy_note(summary: dict) -> str:
    """The network's own verdict on itself, phrased from its computed numbers."""
    share = summary["share_without_alternative"]
    loops = summary["independent_loops"]
    return (
        f"{share * 100:.0f}% of mapped road segments in this corridor have no "
        f"alternative: removing one disconnects the network. Across "
        f"{summary['segments']} segments there are only {loops} independent loops. "
        "That is why the fastest and safest routes are so often the same road - "
        "there is rarely a second one to choose."
    )


def _assessment(corridor: CorridorRoutes) -> RouteAssessmentResponse:
    run = baseline_risk()
    capacity = {entry.site.id: entry for entry in baseline_capacity()}
    habitations = {h.id: h for h in run.context.habitations}
    sites = {s.id: s for s in run.context.sites}
    route_config = MODEL_CONFIG.route
    threshold = route_config.min_reliability_threshold.value

    rows: list[RouteMatrixRow] = []
    for (habitation_id, site_id), pair in corridor.pairs.items():
        habitation = habitations[habitation_id]
        best = pair.best
        rows.append(
            RouteMatrixRow(
                habitation_id=habitation_id,
                habitation_name=habitation.name,
                population=habitation.population,
                site_id=site_id,
                site_name=sites[site_id].name,
                travel_time_min=best.travel_time_min,
                distance_km=best.distance_km,
                reliability=best.reliability,
                feasible=pair.feasible,
                site_suitable=capacity[site_id].suitable if site_id in capacity else False,
                bridges_crossed=best.bridges_crossed,
                hazard_exposed_km=best.hazard_exposed_km,
                points_of_failure=len(best.points_of_failure),
            )
        )
    rows.sort(key=lambda row: (row.habitation_id, -row.reliability))

    usable = {
        row.habitation_id
        for row in rows
        if row.feasible and row.site_suitable
    }
    blocked = sorted(set(habitations) - usable)

    summary = network_summary(corridor.network)
    return RouteAssessmentResponse(
        network=NetworkSummaryResponse(**summary, redundancy_note=_redundancy_note(summary)),
        closed_segments=sorted(corridor.closed_segments),
        rows=rows,
        access=[
            SiteAccessResponse(
                site_id=access.site_id,
                name=sites[access.site_id].name,
                reachable_habitations=access.reachable_habitations,
                feasible_habitations=access.feasible_habitations,
                usable_routes=access.usable_routes,
                best_reliability=access.best_reliability,
                median_travel_time_min=access.median_travel_time_min,
                access_capacity_persons=access.access_capacity_persons,
                population_with_feasible_route=access.population_with_feasible_route,
            )
            for access in corridor.access.values()
        ],
        pairs_evaluated=len(corridor.pairs),
        feasible_pairs=sum(1 for pair in corridor.pairs.values() if pair.feasible),
        habitations_with_a_reachable_suitable_site=len(usable),
        route_blocked_habitations=blocked,
        reliability_threshold=threshold,
        profiles_differ_count=sum(
            1 for pair in corridor.pairs.values() if pair.profiles_differ
        ),
        constants=[
            route_config.p_fail_hazard_coefficient,
            route_config.p_fail_reference_length_m,
            route_config.p_fail_bridge_dependency,
            route_config.min_reliability_threshold,
            route_config.safest_risk_alpha,
            route_config.throughput_persons_per_hour,
            route_config.access_movement_window_hours,
            route_config.speed_national_highway_kmh,
            route_config.speed_state_highway_kmh,
            route_config.speed_district_road_kmh,
            route_config.speed_village_road_kmh,
            route_config.speed_track_kmh,
        ],
        decision_authority=DECISION_AUTHORITY,
        model_config_version=MODEL_CONFIG.version,
        engine_version=MODEL_CONFIG.engine_version,
    )


@router.get("", response_model=RouteAssessmentResponse)
def routes() -> RouteAssessmentResponse:
    """Every habitation-to-site pair on the open network."""
    return _assessment(baseline_routes())


def plan_dependencies(plan, corridor: CorridorRoutes) -> list[PlanDependencyResponse]:
    """Every segment a solved plan's movements cross, ranked by residents carried.

    Shared by the critical-segments endpoint and the Decision Brief, so the road a
    brief says the plan leans on is the road the What-If screen offers to close.
    """
    network = corridor.network
    no_alternative = network.critical_segments

    people: dict[str, int] = {}
    movements: dict[str, int] = {}
    for assignment in plan.assignments:
        pair = corridor.pair(assignment.habitation_id, assignment.site_id)
        if pair is None:
            continue
        for leg in pair.best.legs:
            people[leg.segment_id] = people.get(leg.segment_id, 0) + assignment.people
            movements[leg.segment_id] = movements.get(leg.segment_id, 0) + 1

    rows: list[PlanDependencyResponse] = []
    for segment_id, carried in people.items():
        segment = network.by_id[segment_id]
        alone = segment_id in no_alternative
        rows.append(
            PlanDependencyResponse(
                segment_id=segment_id,
                name=segment.name,
                road_class=segment.road_class,
                length_m=round(segment.length_m, 1),
                is_bridge=segment.is_bridge,
                p_fail=round(segment.p_fail(), 5),
                no_alternative=alone,
                people_dependent=carried,
                movements=movements[segment_id],
                consequence=(
                    f"{carried:,} of the {plan.totals.population_assigned:,} residents "
                    f"the plan moves cross this "
                    + ("bridge" if segment.is_bridge else "stretch of road")
                    + (
                        "; the network offers no alternative around it."
                        if alone
                        else "; an alternative exists, so closing it re-routes rather "
                        "than disconnects."
                    )
                ),
            )
        )
    # Ties on residents carried are common - a corridor with one road through it
    # puts the same people on every segment of it. Break the tie towards the
    # segments whose loss is hardest to work around, so the top of the list is
    # the set of closures actually worth simulating.
    rows.sort(
        key=lambda row: (
            -row.people_dependent,
            0 if row.is_bridge else 1,
            0 if row.no_alternative else 1,
            -row.p_fail,
            row.segment_id,
        )
    )
    return rows


@router.get("/critical-segments", response_model=PlanDependencyListResponse)
def critical_segments() -> PlanDependencyListResponse:
    """The roads the baseline plan is standing on, ranked by residents carried.

    A what-if that closes a road at random mostly proves nothing. This ranks the
    corridor by how much of the *solved plan* actually crosses each segment, so
    the closure a user picks is the one that tests the plan rather than the
    network. Every figure here is read off the baseline plan and the baseline
    routes; nothing is re-solved.
    """
    from astra.engines.optimizer_service import baseline_plan

    corridor = baseline_routes()
    network = corridor.network
    plan, _ = baseline_plan()
    rows = plan_dependencies(plan, corridor)

    return PlanDependencyListResponse(
        segments=rows[:20],
        plan_people=plan.totals.population_assigned,
        segments_carrying_the_plan=len(rows),
        note=(
            f"{len(rows)} of {len(network.segments)} mapped segments carry at least "
            "one planned movement. Ranked by residents whose assigned journey crosses "
            "them, read off the baseline plan."
        ),
    )


@router.get("/network.geojson")
def network_geojson() -> dict:
    """The routed graph as GeoJSON, coloured by what each segment contributes."""
    network = corridor_network()
    critical = network.critical_segments
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": segment.id,
                "geometry": segment.as_linestring(),
                "properties": {
                    "segment_id": segment.id,
                    "name": segment.name,
                    "road_class": segment.road_class.value,
                    "length_m": round(segment.length_m, 1),
                    "is_bridge": segment.is_bridge,
                    "hazard_mean": segment.hazard_mean,
                    "hazard_max": segment.hazard_max,
                    "hazard_coverage": segment.hazard_coverage,
                    "p_fail": round(segment.p_fail(), 5),
                    "no_alternative": segment.id in critical,
                },
            }
            for segment in network.segments
        ],
        "properties": {
            "source": "OpenStreetMap contributors, ODbL 1.0",
            **network_summary(network),
        },
    }


@router.get("/pair/{habitation_id}/{site_id}", response_model=RoutePairResponse)
def route_pair(habitation_id: str, site_id: str) -> RoutePairResponse:
    """Fastest and safest between one habitation and one site, with the trade."""
    corridor = baseline_routes()
    pair = corridor.pair(habitation_id, site_id)
    if pair is None:
        raise HTTPException(
            status_code=404,
            detail=f"no evaluated route from '{habitation_id}' to '{site_id}'",
        )
    return _pair(pair, corridor)


@router.post("/evaluate", response_model=ClosureImpactResponse)
def evaluate(request: ClosureRequest) -> ClosureImpactResponse:
    """Re-route the corridor with the given segments closed, against the baseline."""
    network = corridor_network()
    unknown = [
        segment_id
        for segment_id in request.closed_segments
        if segment_id not in network.by_id
    ]
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"unknown road segments: {', '.join(unknown)}"
        )
    if len(request.closed_segments) > MAX_CLOSED_SEGMENTS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"at most {MAX_CLOSED_SEGMENTS} segments may be closed in one "
                "evaluation; a scenario closes roads, it does not remove the network"
            ),
        )

    closed = frozenset(request.closed_segments)
    before = baseline_routes()
    after = evaluate_corridor(closed_segments=closed)

    run = baseline_risk()
    population = {h.id: h.population for h in run.context.habitations}

    changed: list[RouteDeltaRow] = []
    losing_population = 0
    for key, pair_after in after.pairs.items():
        pair_before = before.pairs[key]
        best_before, best_after = pair_before.best, pair_after.best
        unreachable = bool(best_before.legs) and not best_after.legs
        if (
            abs(best_after.reliability - best_before.reliability) < 1e-6
            and abs(best_after.travel_time_min - best_before.travel_time_min) < 1e-6
            and pair_after.feasible == pair_before.feasible
        ):
            continue
        changed.append(
            RouteDeltaRow(
                habitation_id=key[0],
                site_id=key[1],
                reliability_before=best_before.reliability,
                reliability_after=best_after.reliability,
                travel_time_before_min=best_before.travel_time_min,
                travel_time_after_min=best_after.travel_time_min,
                feasible_before=pair_before.feasible,
                feasible_after=pair_after.feasible,
                became_unreachable=unreachable,
            )
        )
    changed.sort(key=lambda row: row.reliability_after - row.reliability_before)

    # "Losing a reachable site" has to mean the same thing here as it does in the
    # assessment's route-blocked list, or the two numbers on the same screen will
    # contradict each other. A site only counts as an option if it is both
    # reachable above the threshold and suitable: a site that fails a hard gate
    # is not somewhere anyone can be moved, however good the road to it is.
    suitable_sites = {entry.site.id for entry in baseline_capacity() if entry.suitable}

    def with_an_option(corridor: CorridorRoutes) -> set[str]:
        return {
            habitation_id
            for (habitation_id, site_id), pair in corridor.pairs.items()
            if pair.feasible and site_id in suitable_sites
        }

    had_option, has_option = with_an_option(before), with_an_option(after)
    losing_population = sum(
        population.get(habitation_id, 0)
        for habitation_id in sorted(had_option - has_option)
    )

    newly_infeasible = sum(
        1
        for key, pair in after.pairs.items()
        if before.pairs[key].feasible and not pair.feasible
    )
    newly_unreachable = sum(1 for row in changed if row.became_unreachable)
    return ClosureImpactResponse(
        closed_segments=sorted(closed),
        closed_segment_detail=[
            SegmentLegResponse(
                segment_id=segment.id,
                name=segment.name,
                road_class=segment.road_class,
                length_m=round(segment.length_m, 1),
                travel_time_min=round(segment.travel_time_min(), 3),
                hazard_max=segment.hazard_max,
                hazard_coverage=segment.hazard_coverage,
                is_bridge=segment.is_bridge,
                p_fail=round(segment.p_fail(), 5),
            )
            for segment in (network.by_id[sid] for sid in sorted(closed))
        ],
        assessment=_assessment(after),
        changed=changed,
        newly_infeasible=newly_infeasible,
        newly_unreachable=newly_unreachable,
        population_losing_a_reachable_site=losing_population,
        headline=(
            f"Closing {len(closed)} segment(s) changes {len(changed)} of "
            f"{len(after.pairs)} habitation-site routes: {newly_infeasible} drop below "
            f"the {MODEL_CONFIG.route.min_reliability_threshold.value * 100:.0f}% "
            f"reliability threshold and {newly_unreachable} lose any road connection. "
            f"{len(had_option - has_option)} habitation(s) totalling "
            f"{losing_population} residents are left with no suitable site they can "
            "reach reliably."
            if changed
            else "These closures change no habitation-site route in the corridor."
        ),
    )
