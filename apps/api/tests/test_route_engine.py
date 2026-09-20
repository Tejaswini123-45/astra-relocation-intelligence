"""Engine 5 - route reliability, survivability and closures.

The arithmetic tests build tiny synthetic networks so the expected answer can be
worked out by hand. The corridor tests run against the real Alaknanda extract and
assert the properties that have to hold whatever the road turns out to look like.
"""

from __future__ import annotations

import math

import pytest

from astra.domain.enums import RoadClass, RouteProfile
from astra.domain.model_config import MODEL_CONFIG
from astra.engines.network import RoadNetwork, RoadNode, Segment, polyline_length_m
from astra.engines.routes import RouteEngine

ROUTE = MODEL_CONFIG.route


def node(name: str, lon: float, lat: float) -> RoadNode:
    return RoadNode(id=name, lon=lon, lat=lat)


def segment(
    seg_id: str,
    a: str,
    b: str,
    coordinates: tuple[tuple[float, float], ...],
    *,
    road_class: RoadClass = RoadClass.DISTRICT_ROAD,
    hazard: float = 0.0,
    bridge: bool = False,
) -> Segment:
    return Segment(
        id=seg_id,
        osm_id=int(seg_id[1:].split("-")[0]) if seg_id[1:].split("-")[0].isdigit() else 1,
        from_node=a,
        to_node=b,
        coordinates=coordinates,
        length_m=polyline_length_m(coordinates),
        road_class=road_class,
        is_bridge=bridge,
        hazard_mean=hazard,
        hazard_max=hazard,
    )


def straight(lon0: float, lat0: float, lon1: float, lat1: float):
    return ((lon0, lat0), (lon1, lat1))


# ---------------------------------------------------------------------------
# Segment arithmetic
# ---------------------------------------------------------------------------


def test_travel_time_is_length_over_the_class_speed() -> None:
    highway = segment(
        "W1-0", "A", "B", straight(79.30, 30.40, 79.40, 30.40),
        road_class=RoadClass.NATIONAL_HIGHWAY,
    )
    expected = highway.length_m / 1000.0 / ROUTE.speed_national_highway_kmh.value * 60.0
    assert highway.travel_time_min() == pytest.approx(expected)


def test_a_track_takes_longer_than_a_highway_over_the_same_ground() -> None:
    line = straight(79.30, 30.40, 79.35, 30.40)
    fast = segment("W1-0", "A", "B", line, road_class=RoadClass.NATIONAL_HIGHWAY)
    slow = segment("W2-0", "A", "B", line, road_class=RoadClass.TRACK)
    assert slow.travel_time_min() > fast.travel_time_min()
    assert slow.travel_time_min() / fast.travel_time_min() == pytest.approx(
        ROUTE.speed_national_highway_kmh.value / ROUTE.speed_track_kmh.value
    )


def test_a_segment_over_safe_ground_cannot_fail() -> None:
    safe = segment("W1-0", "A", "B", straight(79.30, 30.40, 79.31, 30.40), hazard=0.0)
    assert safe.p_fail() == 0.0


def test_failure_probability_follows_the_documented_formula() -> None:
    exposed = segment(
        "W1-0", "A", "B", straight(79.30, 30.40, 79.35, 30.40), hazard=0.8
    )
    reference = ROUTE.p_fail_reference_length_m.value
    coefficient = ROUTE.p_fail_hazard_coefficient.value
    expected = 1.0 - (1.0 - coefficient) ** (exposed.exposed_length_m / reference)
    assert exposed.p_fail() == pytest.approx(expected, abs=1e-9)


def test_a_bridge_adds_its_own_independent_failure_chance() -> None:
    line = straight(79.30, 30.40, 79.305, 30.40)
    plain = segment("W1-0", "A", "B", line, hazard=0.5)
    bridged = segment("W2-0", "A", "B", line, hazard=0.5, bridge=True)
    expected = 1.0 - (1.0 - plain.p_fail()) * (
        1.0 - ROUTE.p_fail_bridge_dependency.value
    )
    assert bridged.p_fail() == pytest.approx(expected, abs=1e-9)


def test_twice_the_exposed_length_compounds_rather_than_doubles() -> None:
    short = segment("W1-0", "A", "B", straight(79.30, 30.40, 79.31, 30.40), hazard=0.9)
    long = segment("W2-0", "A", "B", straight(79.30, 30.40, 79.32, 30.40), hazard=0.9)
    assert long.length_m == pytest.approx(2 * short.length_m, rel=1e-3)
    survives_twice = (1.0 - short.p_fail()) ** 2
    assert 1.0 - long.p_fail() == pytest.approx(survives_twice, abs=1e-6)
    assert long.p_fail() < 2 * short.p_fail()


# ---------------------------------------------------------------------------
# Route reliability
# ---------------------------------------------------------------------------


def chain_network(hazards: list[float], bridges: list[bool] | None = None) -> RoadNetwork:
    """A straight chain of equal segments: N0 - N1 - ... - Nk."""
    bridges = bridges or [False] * len(hazards)
    nodes = {
        f"N{i}": node(f"N{i}", 79.30 + 0.01 * i, 30.40) for i in range(len(hazards) + 1)
    }
    segments = [
        segment(
            f"W{i}-0",
            f"N{i}",
            f"N{i + 1}",
            straight(79.30 + 0.01 * i, 30.40, 79.30 + 0.01 * (i + 1), 30.40),
            hazard=hazard,
            bridge=bridges[i],
        )
        for i, hazard in enumerate(hazards)
    ]
    return RoadNetwork(nodes=nodes, segments=segments)


def route_between(network: RoadNetwork, closed=frozenset()):
    engine = RouteEngine(network=network, closed_segments=closed)
    first = min(network.nodes.values(), key=lambda n: n.lon)
    last = max(network.nodes.values(), key=lambda n: n.lon)
    return engine.pair(
        origin_id="H-TEST",
        destination_id="S-TEST",
        origin=(first.lon, first.lat),
        destination=(last.lon, last.lat),
    )


def test_reliability_is_the_product_of_the_segments_surviving() -> None:
    network = chain_network([0.5, 0.5, 0.5])
    route = route_between(network).fastest
    expected = math.prod(1.0 - leg.p_fail for leg in route.legs)
    assert route.reliability == pytest.approx(expected, abs=1e-4)
    assert len(route.legs) == 3


def test_a_route_over_safe_ground_is_fully_reliable() -> None:
    route = route_between(chain_network([0.0, 0.0, 0.0])).fastest
    assert route.reliability == pytest.approx(1.0)
    assert route.risk == pytest.approx(0.0)
    assert route.feasible


def test_reliability_falls_as_a_route_gets_longer() -> None:
    short = route_between(chain_network([0.7, 0.7])).fastest
    long = route_between(chain_network([0.7] * 8)).fastest
    assert long.reliability < short.reliability


def test_reliability_does_not_depend_on_where_the_graph_was_split() -> None:
    """The same road, drawn as one segment or four, must survive equally well.

    This is the property that made the failure model length-scaled rather than
    per-segment, so it is asserted directly.
    """
    whole = RoadNetwork(
        nodes={"N0": node("N0", 79.30, 30.40), "N1": node("N1", 79.34, 30.40)},
        segments=[
            segment("W1-0", "N0", "N1", straight(79.30, 30.40, 79.34, 30.40), hazard=0.6)
        ],
    )
    split = chain_network([0.6, 0.6, 0.6, 0.6])
    assert route_between(whole).fastest.reliability == pytest.approx(
        route_between(split).fastest.reliability, abs=1e-6
    )


def test_a_route_below_the_threshold_is_infeasible_and_says_why() -> None:
    route = route_between(chain_network([1.0] * 40)).fastest
    assert route.reliability < ROUTE.min_reliability_threshold.value
    assert not route.feasible
    assert "below the" in (route.infeasible_reason or "")


def test_travel_time_includes_the_walk_to_the_road() -> None:
    network = chain_network([0.0])
    engine = RouteEngine(network=network)
    on_road = engine.pair(
        origin_id="H", destination_id="S", origin=(79.30, 30.40), destination=(79.31, 30.40)
    ).fastest
    off_road = engine.pair(
        origin_id="H", destination_id="S", origin=(79.30, 30.42), destination=(79.31, 30.40)
    ).fastest
    assert off_road.off_network_m > on_road.off_network_m
    assert off_road.travel_time_min > on_road.travel_time_min


# ---------------------------------------------------------------------------
# Fastest against safest
# ---------------------------------------------------------------------------


#: A - D direct is 30 km of fully exposed road; A - M - D is 37 km of safe road.
#: The extra distance has to be large enough to be a real choice and small enough
#: that risk aversion at the configured alpha still prefers it.
ORIGIN = (79.00, 30.40)
DESTINATION = (79.31, 30.40)
DETOUR_VIA = (79.155, 30.50)


def detour_network() -> RoadNetwork:
    """Two ways from A to D: a shorter exposed one and a longer safe one."""
    nodes = {
        "A": node("A", *ORIGIN),
        "D": node("D", *DESTINATION),
        "M": node("M", *DETOUR_VIA),
    }
    return RoadNetwork(
        nodes=nodes,
        segments=[
            segment("W1-0", "A", "D", straight(*ORIGIN, *DESTINATION), hazard=1.0),
            segment("W2-0", "A", "M", straight(*ORIGIN, *DETOUR_VIA), hazard=0.0),
            segment("W3-0", "M", "D", straight(*DETOUR_VIA, *DESTINATION), hazard=0.0),
        ],
    )


def test_the_safest_route_takes_the_detour_the_fastest_one_skips() -> None:
    engine = RouteEngine(network=detour_network())
    pair = engine.pair(
        origin_id="H", destination_id="S", origin=ORIGIN, destination=DESTINATION
    )
    assert pair.profiles_differ
    assert [leg.segment_id for leg in pair.fastest.legs] == ["W1-0"]
    assert [leg.segment_id for leg in pair.safest.legs] == ["W2-0", "W3-0"]
    assert pair.safest.reliability > pair.fastest.reliability
    assert pair.minutes_paid > 0


def test_the_safest_route_is_never_less_reliable_than_the_fastest() -> None:
    """The guarantee the candidate search exists to provide."""
    for hazard in (0.0, 0.3, 0.6, 0.95):
        network = detour_network()
        engine = RouteEngine(network=network)
        pair = engine.pair(
            origin_id="H",
            destination_id="S",
            origin=ORIGIN,
            destination=DESTINATION,
        )
        assert pair.safest.reliability >= pair.fastest.reliability - 1e-9, hazard


def test_the_tradeoff_sentence_states_the_exchange_rate_in_numbers() -> None:
    engine = RouteEngine(network=detour_network())
    pair = engine.pair(
        origin_id="H", destination_id="S", origin=ORIGIN, destination=DESTINATION
    )
    sentence = pair.tradeoff_sentence()
    assert f"{pair.minutes_paid:.0f} more minutes" in sentence
    assert f"{pair.fastest.reliability * 100:.0f}%" in sentence
    assert f"{pair.safest.reliability * 100:.0f}%" in sentence


def test_identical_profiles_say_there_is_no_trade_to_make() -> None:
    pair = route_between(chain_network([0.3, 0.3]))
    assert not pair.profiles_differ
    assert "no trade to make" in pair.tradeoff_sentence()


def test_both_profiles_are_labelled_with_the_profile_they_answer_for() -> None:
    pair = route_between(chain_network([0.3, 0.3]))
    assert pair.fastest.profile is RouteProfile.FASTEST
    assert pair.safest.profile is RouteProfile.SAFEST
    assert pair.for_profile(RouteProfile.SAFEST) is pair.safest


# ---------------------------------------------------------------------------
# Closures
# ---------------------------------------------------------------------------


def test_closing_the_short_road_forces_the_detour() -> None:
    engine = RouteEngine(network=detour_network(), closed_segments=frozenset({"W1-0"}))
    route = engine.pair(
        origin_id="H", destination_id="S", origin=ORIGIN, destination=DESTINATION
    ).fastest
    assert [leg.segment_id for leg in route.legs] == ["W2-0", "W3-0"]


def test_closing_the_only_road_reports_no_path_not_a_slow_one() -> None:
    route = route_between(chain_network([0.1, 0.1]), closed=frozenset({"W0-0"})).fastest
    assert route.legs == []
    assert route.travel_time_min == 0.0
    assert not route.feasible
    assert "No connected road route" in (route.infeasible_reason or "")


def test_a_closed_segment_is_never_travelled_over() -> None:
    engine = RouteEngine(network=detour_network(), closed_segments=frozenset({"W2-0"}))
    for profile in RouteProfile:
        route = engine.route(
            origin_id="H",
            destination_id="S",
            origin=ORIGIN,
            destination=DESTINATION,
            profile=profile,
        )
        assert all(leg.segment_id != "W2-0" for leg in route.legs)


# ---------------------------------------------------------------------------
# Exposure and points of failure
# ---------------------------------------------------------------------------


def test_the_longest_hazard_run_is_continuous_not_cumulative() -> None:
    threshold = ROUTE.hazard_segment_exposure_threshold.value
    high = min(threshold + 0.2, 1.0)
    broken = route_between(chain_network([high, 0.0, high, 0.0, high])).fastest
    continuous = route_between(chain_network([high, high, high, 0.0, 0.0])).fastest
    assert broken.hazard_exposed_km == pytest.approx(continuous.hazard_exposed_km, abs=0.05)
    assert continuous.longest_hazard_run_km > broken.longest_hazard_run_km


def test_a_bridge_on_the_route_is_listed_as_a_point_of_failure() -> None:
    route = route_between(chain_network([0.0, 0.0], bridges=[False, True])).fastest
    listed = {point.segment_id for point in route.points_of_failure}
    assert listed == {"W1-0"}
    assert route.bridges_crossed == 1


def test_a_safe_road_with_no_structures_has_no_points_of_failure() -> None:
    route = route_between(chain_network([0.0, 0.0, 0.0])).fastest
    assert route.points_of_failure == []


def test_points_of_failure_are_ranked_worst_first() -> None:
    route = route_between(chain_network([0.99] * 6, bridges=[True] * 6)).fastest
    probabilities = [point.p_fail for point in route.points_of_failure]
    assert probabilities == sorted(probabilities, reverse=True)


def test_a_segment_with_no_way_around_it_is_marked_as_such() -> None:
    route = route_between(chain_network([0.0, 0.0], bridges=[False, True])).fastest
    point = route.points_of_failure[0]
    assert point.no_alternative
    assert "no way around it" in point.reason


def test_a_segment_with_a_parallel_alternative_is_not_marked_critical() -> None:
    network = detour_network()
    assert "W1-0" not in network.critical_segments
    assert network.redundancy["independent_loops"] == 1


# ---------------------------------------------------------------------------
# Against the real corridor
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corridor():
    from astra.engines.routes_service import evaluate_corridor

    return evaluate_corridor()


def test_every_habitation_site_pair_is_evaluated(corridor) -> None:
    assert len(corridor.pairs) == 12 * 6


def test_the_graph_was_built_from_shared_osm_nodes(corridor) -> None:
    network = corridor.network
    assert network.unrouted_ways == 0
    assert len(network.segments) > 400
    assert network.bridges, "the corridor extract contains tagged bridges"
    for segment_ in network.segments:
        assert segment_.from_node in network.nodes
        assert segment_.to_node in network.nodes
        assert segment_.length_m > 0


def test_every_site_is_snapped_to_the_main_network_not_an_isolated_stub(corridor) -> None:
    reachable = [pair for pair in corridor.pairs.values() if pair.best.legs]
    assert len(reachable) == len(corridor.pairs), (
        "a site snapped onto a disconnected road stub would look reachable and "
        "route to nothing"
    )


def test_the_corridor_has_both_feasible_and_infeasible_pairs(corridor) -> None:
    """A threshold that never bites would prove nothing."""
    feasible = [pair for pair in corridor.pairs.values() if pair.feasible]
    assert feasible
    assert len(feasible) < len(corridor.pairs)


def test_reliability_is_between_zero_and_one_on_every_real_route(corridor) -> None:
    for pair in corridor.pairs.values():
        for route in (pair.fastest, pair.safest):
            assert 0.0 <= route.reliability <= 1.0
            assert route.risk == pytest.approx(1.0 - route.reliability, abs=1e-6)


def test_the_safest_route_is_never_worse_across_the_whole_corridor(corridor) -> None:
    for pair in corridor.pairs.values():
        assert pair.safest.reliability >= pair.fastest.reliability - 1e-9


def test_route_geometry_is_drawn_in_travel_order(corridor) -> None:
    """A route drawn from unreversed segments zig-zags; this catches that."""
    from astra.engines.network import haversine_m

    pair = corridor.pair("H-01", "S-03")
    geometry = corridor.geometry_of(pair.best)
    assert len(geometry) > len(pair.best.legs)
    drawn = sum(
        haversine_m(*geometry[i], *geometry[i + 1]) for i in range(len(geometry) - 1)
    )
    assert drawn == pytest.approx(pair.best.distance_km * 1000.0, rel=0.02)


def test_this_corridor_has_almost_no_redundancy(corridor) -> None:
    """A finding, asserted so it cannot quietly stop being true.

    The valley network is close to a tree, which is why fastest and safest are so
    often the same road. If a future extract changes that, this test should fail
    and the claim on screen should be revisited.
    """
    redundancy = corridor.network.redundancy
    assert redundancy["share_without_alternative"] > 0.5
    assert redundancy["independent_loops"] < 50


def test_closing_a_used_bridge_changes_real_outcomes(corridor) -> None:
    from collections import Counter

    from astra.engines.routes_service import evaluate_corridor

    used: Counter[str] = Counter()
    for pair in corridor.pairs.values():
        for leg in pair.best.legs:
            if leg.is_bridge:
                used[leg.segment_id] += 1
    assert used, "the corridor routes cross bridges"
    busiest = used.most_common(1)[0][0]

    after = evaluate_corridor(closed_segments=frozenset({busiest}))
    assert all(
        all(leg.segment_id != busiest for leg in pair.best.legs)
        for pair in after.pairs.values()
    )
    degraded = [
        key
        for key, pair in after.pairs.items()
        if pair.best.reliability < corridor.pairs[key].best.reliability - 1e-6
    ]
    assert degraded, "closing the busiest bridge should degrade some route"
    assert after.feasible_pair_count <= corridor.feasible_pair_count


def test_the_baseline_assessment_is_deterministic(corridor) -> None:
    from astra.engines.routes_service import evaluate_corridor

    again = evaluate_corridor()
    assert {
        key: round(pair.best.reliability, 6) for key, pair in again.pairs.items()
    } == {key: round(pair.best.reliability, 6) for key, pair in corridor.pairs.items()}


def test_access_capacity_is_route_count_times_the_per_route_norm(corridor) -> None:
    per_route = MODEL_CONFIG.capacity.access_persons_per_route_day.value
    for access in corridor.access.values():
        assert access.access_capacity_persons == pytest.approx(
            access.usable_routes * per_route, abs=0.1
        )


def test_road_outside_the_scored_surface_is_dropped_not_assumed_safe(corridor) -> None:
    """An unscored road is not a safe road, and the router must not prefer it."""
    network = corridor.network
    assert network.unscored_segments >= 1, (
        "the OSM extract runs past the study area, so some road is unscored"
    )
    for segment_ in network.segments:
        assert segment_.hazard_coverage > 0.0


def test_a_route_reports_how_much_of_it_was_actually_scored(corridor) -> None:
    for pair in corridor.pairs.values():
        for route in (pair.fastest, pair.safest):
            if not route.legs:
                continue
            assert 0.0 < route.scored_share <= 1.0


def test_closing_a_road_can_never_make_a_journey_more_survivable(corridor) -> None:
    """Monotonicity, and the reason the safest route is chosen reliability-first.

    Removing an edge cannot create a better path, so no pair may come back more
    reliable after a closure. It did, once: the objective preferred a quicker,
    less reliable candidate, so closing the quick road revealed the safe one that
    had been computed and discarded. A what-if in which shutting a bridge
    improves access is not a model anyone should trust.
    """
    from astra.engines.routes_service import evaluate_corridor

    bridges = [segment.id for segment in corridor.network.bridges][:10]
    assert bridges
    for segment_id in bridges:
        after = evaluate_corridor(closed_segments=frozenset({segment_id}))
        for key, pair in after.pairs.items():
            assert pair.best.reliability <= corridor.pairs[key].best.reliability + 1e-9
            assert pair.feasible <= corridor.pairs[key].feasible


def test_the_safest_route_is_the_most_reliable_candidate_found(corridor) -> None:
    for pair in corridor.pairs.values():
        assert pair.safest.reliability >= pair.fastest.reliability - 1e-9
        assert pair.best is pair.safest or pair.safest.reliability == pytest.approx(
            pair.fastest.reliability
        )


def test_the_corridor_does_show_a_real_fastest_versus_safest_trade(corridor) -> None:
    """Rare in this valley, but not absent - and where it happens it is priced."""
    differing = [pair for pair in corridor.pairs.values() if pair.profiles_differ]
    assert differing, "at least one pair should have a genuine alternative"
    for pair in differing:
        assert pair.safest.reliability > pair.fastest.reliability
        assert "buys" in pair.tradeoff_sentence() or "no slower" in pair.tradeoff_sentence()
