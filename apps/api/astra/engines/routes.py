"""Engine 5 - route reliability and survivability.

Travel time answers "how far is it". This engine answers the question a
relocation plan actually turns on: **will the road still be there.**

A route is a chain of segments, each of which can fail independently, so a
route's reliability is the product of its segments surviving::

    R = product over segments of (1 - p_fail)

That product is unforgiving, and deliberately so. Twelve segments at 95% each
give a route at 54%. An SDMA moving people along that road is not making a safe
journey with a small caveat; it is making a coin toss, and the number should say
so rather than round the discomfort away.

Two profiles are returned whenever they differ:

* ``FASTEST`` minimises travel time.
* ``SAFEST`` minimises ``time x (1 + alpha x risk)`` **on the finished route**,
  where risk is one minus the route's reliability. It buys reliability with
  minutes, and the response states the exchange rate numerically rather than
  asserting one route is better.

That objective is a property of the whole route, not a sum over its edges, so no
single shortest-path search optimises it. Three candidate paths are generated -
the quickest, the most reliable, and one under an edge-level risk-weighted cost -
and SAFEST is chosen from them by **reliability first**, with the stated
time-and-risk objective breaking ties between candidates that are equally
survivable.

Reliability first, rather than the objective alone, and the reason is not
aesthetic. Feasibility hangs on the safest route: a site is reachable if a route
to it clears the threshold. Letting the objective prefer a quicker, less reliable
candidate meant ASTRA computed a usable road, discarded it, and then declared the
site unreachable - and, worse, *closing* a road could then raise a pair above the
threshold by removing the quick option that was hiding the safe one. A what-if
in which shutting a bridge improves access is not a model anyone should trust.
Reliability-first restores the property that closing a road can never make a
journey more survivable, and a test asserts exactly that.

A route below the configured reliability threshold does not get a penalty. It
makes the destination **infeasible** for that origin, and the optimiser in the
next slice is required to treat it that way. A road you would not send a bus down
is not a slightly worse road.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from functools import cached_property

import networkx as nx

from astra.domain.enums import ProvenanceClass, RoadClass, RouteProfile
from astra.domain.model_config import MODEL_CONFIG, AstraModelConfig
from astra.engines.network import RoadNetwork, Segment

FORMULA_RELIABILITY = "route.reliability"
FORMULA_SAFEST = "route.safest_objective"

#: Walking speed for the off-network leg between a habitation or site centroid
#: and the nearest mapped road. Slow on purpose: this is people on foot on
#: mountain ground, often carrying what they own.
OFF_NETWORK_SPEED_KMH = 4.0


@dataclass(frozen=True)
class SegmentLeg:
    """One segment as travelled on a particular route."""

    segment_id: str
    name: str | None
    road_class: RoadClass
    #: The segment's endpoints in the direction actually travelled, which is not
    #: always the direction the segment is stored in. Drawing a route without
    #: this makes it zig-zag back on itself.
    from_node: str
    to_node: str
    length_m: float
    travel_time_min: float
    hazard_max: float
    hazard_coverage: float
    is_bridge: bool
    p_fail: float

    @property
    def survives(self) -> float:
        return 1.0 - self.p_fail


@dataclass(frozen=True)
class PointOfFailure:
    """A segment whose loss alone breaks the route, with why it is fragile."""

    segment_id: str
    name: str | None
    reason: str
    p_fail: float
    length_m: float
    #: True when the network offers no way around this segment at all, computed
    #: from the graph rather than inferred from the road tag.
    no_alternative: bool = False


@dataclass(frozen=True)
class Route:
    """One evaluated journey, with everything that makes it trustworthy or not."""

    origin_id: str
    destination_id: str
    profile: RouteProfile
    legs: list[SegmentLeg]
    travel_time_min: float
    distance_km: float
    reliability: float
    off_network_m: float
    off_network_min: float
    hazard_segment_count: int
    hazard_exposed_km: float
    longest_hazard_run_km: float
    points_of_failure: list[PointOfFailure]
    bridges_crossed: int
    feasible: bool
    #: Share of the route measured on the scored hazard surface, weighted by
    #: length. Below one, part of this road runs outside the study area and its
    #: risk was estimated from the part ASTRA could see.
    scored_share: float = 1.0
    infeasible_reason: str | None = None
    provenance: ProvenanceClass = ProvenanceClass.DERIVED

    @property
    def risk(self) -> float:
        """The chance the journey is blocked somewhere. One minus reliability."""
        return round(1.0 - self.reliability, 4)

    @property
    def worst_segment(self) -> SegmentLeg | None:
        return max(self.legs, key=lambda leg: leg.p_fail) if self.legs else None


@dataclass(frozen=True)
class RoutePair:
    """Both profiles for one origin-destination pair, and the trade between them."""

    origin_id: str
    destination_id: str
    fastest: Route
    safest: Route

    def for_profile(self, profile: RouteProfile) -> Route:
        return self.fastest if profile is RouteProfile.FASTEST else self.safest

    @property
    def feasible(self) -> bool:
        """A pair is usable if the most survivable route ASTRA found clears the bar.

        Judged on SAFEST, which is by construction the most reliable route the
        search found. Judging it on anything less would mean declaring a site
        unreachable while holding a road that reaches it.
        """
        return self.best.feasible

    @property
    def best(self) -> Route:
        """The route a plan should be built on: the more reliable of the two."""
        return (
            self.safest
            if self.safest.reliability >= self.fastest.reliability
            else self.fastest
        )

    @property
    def profiles_differ(self) -> bool:
        return [leg.segment_id for leg in self.fastest.legs] != [
            leg.segment_id for leg in self.safest.legs
        ]

    @property
    def minutes_paid(self) -> float:
        return round(self.safest.travel_time_min - self.fastest.travel_time_min, 1)

    @property
    def reliability_gained(self) -> float:
        return round(self.safest.reliability - self.fastest.reliability, 4)

    def tradeoff_sentence(self) -> str:
        """The exchange rate, stated in numbers rather than asserted in adjectives."""
        if not self.profiles_differ:
            return (
                "The fastest and safest routes are the same road, at "
                f"{self.fastest.travel_time_min:.0f} min and "
                f"{self.fastest.reliability * 100:.0f}% reliability. "
                "There is no trade to make here."
            )
        if self.minutes_paid <= 0:
            return (
                f"The safer route is also no slower: {self.safest.travel_time_min:.0f} "
                f"min at {self.safest.reliability * 100:.0f}% reliability against "
                f"{self.fastest.reliability * 100:.0f}% on the fastest road."
            )
        return (
            f"{self.minutes_paid:.0f} more minutes buys "
            f"{self.reliability_gained * 100:+.0f} points of reliability: "
            f"{self.fastest.travel_time_min:.0f} min at "
            f"{self.fastest.reliability * 100:.0f}% against "
            f"{self.safest.travel_time_min:.0f} min at "
            f"{self.safest.reliability * 100:.0f}%."
        )


@dataclass
class RouteEngine:
    """Routing over a road network under a given set of closures."""

    network: RoadNetwork
    closed_segments: frozenset[str] = field(default_factory=frozenset)
    config: AstraModelConfig = field(default_factory=lambda: MODEL_CONFIG)

    @cached_property
    def graph(self) -> nx.MultiGraph:
        """The graph with closed segments removed rather than penalised.

        A closed road is not a slow road. Leaving it in with a large weight would
        let the router use it when nothing else exists and report a travel time
        for a journey that cannot be made.
        """
        graph = nx.MultiGraph()
        for node in self.network.nodes.values():
            graph.add_node(node.id, lon=node.lon, lat=node.lat)
        for segment in self.network.segments:
            if segment.id in self.closed_segments:
                continue
            time_min = segment.travel_time_min(self.config)
            p_fail = segment.p_fail(self.config)
            alpha = self.config.route.safest_risk_alpha.value
            graph.add_edge(
                segment.from_node,
                segment.to_node,
                key=segment.id,
                segment=segment,
                time_min=time_min,
                p_fail=p_fail,
                # Reliability is a product over edges, so its logarithm is a sum -
                # which is what makes "the most reliable path" a shortest-path
                # problem at all. Minimising this weight maximises that product
                # exactly.
                reliability_cost=-math.log(max(1.0 - p_fail, 1e-9)),
                risk_weighted_time=time_min * (1.0 + alpha * p_fail),
            )
        return graph

    def _shortest(
        self, source: str, target: str, weight: str
    ) -> list[tuple[Segment, str, str]] | None:
        """The chosen segments, each paired with the nodes in travel order."""
        try:
            nodes = nx.shortest_path(self.graph, source, target, weight=weight)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None
        steps: list[tuple[Segment, str, str]] = []
        for a, b in zip(nodes, nodes[1:], strict=False):
            # Parallel edges are real - a bypass beside the old road - so take the
            # cheapest on the same measure the path was chosen by.
            candidates = self.graph[a][b]
            best_key = min(candidates, key=lambda key: candidates[key][weight])
            steps.append((candidates[best_key]["segment"], a, b))
        return steps

    def route(
        self,
        *,
        origin_id: str,
        destination_id: str,
        origin: tuple[float, float],
        destination: tuple[float, float],
        profile: RouteProfile,
    ) -> Route:
        """One journey under one profile. Both are computed together; see :meth:`pair`."""
        return self.pair(
            origin_id=origin_id,
            destination_id=destination_id,
            origin=origin,
            destination=destination,
        ).for_profile(profile)

    def _candidate(
        self,
        *,
        origin_id: str,
        destination_id: str,
        origin: tuple[float, float],
        destination: tuple[float, float],
        profile: RouteProfile,
        weight: str,
    ) -> Route:
        """One shortest-path search under one edge weight, scored as a whole route."""
        start, start_offset = self.network.nearest_node(*origin)
        end, end_offset = self.network.nearest_node(*destination)
        off_network_m = start_offset + end_offset
        off_network_min = off_network_m / 1000.0 / OFF_NETWORK_SPEED_KMH * 60.0

        steps = self._shortest(start.id, end.id, weight) if start.id != end.id else []
        if steps is None:
            return _unreachable(
                origin_id, destination_id, profile, off_network_m, off_network_min
            )

        legs = [
            SegmentLeg(
                segment_id=segment.id,
                name=segment.name,
                road_class=segment.road_class,
                from_node=from_node,
                to_node=to_node,
                length_m=round(segment.length_m, 1),
                travel_time_min=round(segment.travel_time_min(self.config), 3),
                hazard_max=segment.hazard_max,
                hazard_coverage=segment.hazard_coverage,
                is_bridge=segment.is_bridge,
                p_fail=round(segment.p_fail(self.config), 5),
            )
            for segment, from_node, to_node in steps
        ]
        return self._assemble(
            origin_id, destination_id, profile, legs, off_network_m, off_network_min
        )

    def _assemble(
        self,
        origin_id: str,
        destination_id: str,
        profile: RouteProfile,
        legs: list[SegmentLeg],
        off_network_m: float,
        off_network_min: float,
    ) -> Route:
        route_config = self.config.route
        hazard_threshold = route_config.hazard_segment_exposure_threshold.value
        reliability = 1.0
        for leg in legs:
            reliability *= leg.survives

        exposed = [leg for leg in legs if leg.hazard_max >= hazard_threshold]
        threshold = route_config.min_reliability_threshold.value
        feasible = reliability >= threshold
        return Route(
            origin_id=origin_id,
            destination_id=destination_id,
            profile=profile,
            legs=legs,
            travel_time_min=round(
                sum(leg.travel_time_min for leg in legs) + off_network_min, 1
            ),
            distance_km=round(sum(leg.length_m for leg in legs) / 1000.0, 2),
            reliability=round(reliability, 4),
            off_network_m=round(off_network_m, 1),
            off_network_min=round(off_network_min, 1),
            hazard_segment_count=len(exposed),
            hazard_exposed_km=round(sum(leg.length_m for leg in exposed) / 1000.0, 2),
            longest_hazard_run_km=round(
                _longest_run_m(legs, hazard_threshold) / 1000.0, 2
            ),
            points_of_failure=_points_of_failure(
                legs, self.config, self.network.critical_segments
            ),
            bridges_crossed=sum(1 for leg in legs if leg.is_bridge),
            feasible=feasible,
            scored_share=round(_scored_share(legs), 4),
            infeasible_reason=(
                None
                if feasible
                else (
                    f"Route reliability {reliability * 100:.0f}% is below the "
                    f"{threshold * 100:.0f}% threshold, so this site is treated as "
                    "unreachable from this habitation rather than merely penalised."
                )
            ),
        )

    def pair(
        self,
        *,
        origin_id: str,
        destination_id: str,
        origin: tuple[float, float],
        destination: tuple[float, float],
    ) -> RoutePair:
        """Both profiles for one pair, SAFEST chosen on the stated route objective."""
        common = {
            "origin_id": origin_id,
            "destination_id": destination_id,
            "origin": origin,
            "destination": destination,
        }
        fastest = self._candidate(
            **common, profile=RouteProfile.FASTEST, weight="time_min"
        )
        alpha = self.config.route.safest_risk_alpha.value

        def objective(route: Route) -> tuple[float, float, float]:
            """Reliability first; the stated time-and-risk objective breaks ties.

            Reliability is bucketed to nine decimals so two paths that are the
            same road within floating-point noise are treated as tied and the
            quicker one wins, rather than one of them being chosen on a rounding
            difference.
            """
            if not route.legs:
                return (math.inf, math.inf, math.inf)
            return (
                -round(route.reliability, 9),
                route.travel_time_min * (1.0 + alpha * route.risk),
                route.travel_time_min,
            )

        candidates = [
            fastest,
            self._candidate(
                **common, profile=RouteProfile.SAFEST, weight="reliability_cost"
            ),
            self._candidate(
                **common, profile=RouteProfile.SAFEST, weight="risk_weighted_time"
            ),
        ]
        safest = min(candidates, key=objective)
        return RoutePair(
            origin_id=origin_id,
            destination_id=destination_id,
            fastest=fastest,
            safest=(
                safest
                if safest.profile is RouteProfile.SAFEST
                else replace(safest, profile=RouteProfile.SAFEST)
            ),
        )


def _unreachable(
    origin_id: str,
    destination_id: str,
    profile: RouteProfile,
    off_network_m: float,
    off_network_min: float,
) -> Route:
    """No path exists. Reported as no path, never as a very long one."""
    return Route(
        origin_id=origin_id,
        destination_id=destination_id,
        profile=profile,
        legs=[],
        travel_time_min=0.0,
        distance_km=0.0,
        reliability=0.0,
        off_network_m=round(off_network_m, 1),
        off_network_min=round(off_network_min, 1),
        hazard_segment_count=0,
        hazard_exposed_km=0.0,
        longest_hazard_run_km=0.0,
        points_of_failure=[],
        bridges_crossed=0,
        feasible=False,
        infeasible_reason=(
            "No connected road route exists between these points on the mapped "
            "network under the current closures."
        ),
    )


def _scored_share(legs: list[SegmentLeg]) -> float:
    """How much of this route ASTRA actually scored, weighted by length."""
    total = sum(leg.length_m for leg in legs)
    if total <= 0:
        return 1.0
    return sum(leg.length_m * leg.hazard_coverage for leg in legs) / total


def _longest_run_m(legs: list[SegmentLeg], threshold: float) -> float:
    """Longest unbroken stretch of hazard-exposed road along the route.

    Ten separate 200 m crossings and one continuous 2 km run are the same total
    exposure and very different journeys.
    """
    longest = current = 0.0
    for leg in legs:
        if leg.hazard_max >= threshold:
            current += leg.length_m
            longest = max(longest, current)
        else:
            current = 0.0
    return longest


def _points_of_failure(
    legs: list[SegmentLeg],
    config: AstraModelConfig,
    critical_segments: frozenset[str],
) -> list[PointOfFailure]:
    """Segments that on their own decide whether the journey happens.

    Two independent qualifications, and a segment is listed if it meets either:

    * **No alternative.** Removing it disconnects the network, computed from the
      graph. In this corridor that is most of the road, which is exactly the
      finding worth surfacing rather than suppressing.
    * **Likely to fail.** A bridge or culvert, or a stretch whose own failure
      probability is at or above the configured concern threshold.

    Listing every segment of a single path would be true and useless, so the list
    is ranked by failure probability and the reason names which test it met.
    """
    concern = config.route.point_of_failure_p_fail.value
    results: list[PointOfFailure] = []
    for leg in legs:
        no_alternative = leg.segment_id in critical_segments
        reasons: list[str] = []
        if leg.is_bridge:
            reasons.append("carries a bridge or culvert")
        if leg.p_fail >= concern:
            reasons.append(
                f"hazard exposure {leg.hazard_max:.2f} along {leg.length_m:.0f} m"
            )
        if not reasons:
            continue
        if no_alternative:
            reasons.append("and the network offers no way around it")
        results.append(
            PointOfFailure(
                segment_id=leg.segment_id,
                name=leg.name,
                reason=", ".join(reasons),
                p_fail=leg.p_fail,
                length_m=leg.length_m,
                no_alternative=no_alternative,
            )
        )
    results.sort(key=lambda entry: (entry.no_alternative, entry.p_fail), reverse=True)
    return results


def route_geometry(route: Route, network: RoadNetwork) -> list[list[float]]:
    """The route drawn end to end, with each segment oriented to follow the last.

    Segments are undirected in the graph, so a segment may be stored running the
    opposite way to the direction of travel. Concatenating them unreversed draws
    a route that zig-zags back on itself, which looks like a bug in the router
    and is in fact a bug in the drawing.
    """
    points: list[tuple[float, float]] = []
    for leg in route.legs:
        segment: Segment | None = network.by_id.get(leg.segment_id)
        if segment is None:
            continue
        coordinates = list(segment.coordinates)
        if segment.from_node != leg.from_node:
            coordinates.reverse()
        points.extend(coordinates[1:] if points else coordinates)
    return [list(point) for point in points]
