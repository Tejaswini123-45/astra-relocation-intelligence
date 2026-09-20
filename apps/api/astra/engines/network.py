"""The routed road graph: OSM ways turned into something you can travel on.

An OSM way is a polyline that may run for kilometres and cross a dozen junctions.
A graph edge has to be the stretch between two decision points, because that is
the unit that carries a speed, a bridge, a hazard exposure and - crucially - a
closure. This module does that split and nothing else: it builds the graph and
attaches the facts. What those facts mean for a journey is Engine 5's job, in
``routes.py``.

Three decisions worth stating, because every travel time in ASTRA rests on them:

* **Junctions come from shared OSM node identifiers, not from geometric
  proximity.** Two roads whose lines cross on screen are not connected unless
  OSM says they share a node - which is exactly right in a valley where a
  highway passes under a footbridge.
* **Segment length is measured along the polyline**, summing great-circle steps
  between consecutive vertices. A straight-line distance between endpoints would
  understate every hairpin on a Himalayan road, and understate them worst where
  the terrain is most difficult.
* **Hazard exposure is sampled from the composite surface along the segment**,
  cell by cell, and kept as both a mean and a maximum. A road whose average
  exposure is mild but which crosses one Critical cell is not a mild road, and
  the maximum is what says so.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from functools import cached_property

import numpy as np

from astra.data.osm import OsmWay, roads
from astra.domain.enums import ProvenanceClass, RoadClass
from astra.domain.model_config import MODEL_CONFIG, AstraModelConfig
from astra.engines.grid import AnalysisGrid

EARTH_RADIUS_M = 6_371_000.0

#: Free-flow speed per road class, read from the config so the numbers a judge
#: sees on the transparency screen are the numbers the router used.
SPEED_KEYS: dict[RoadClass, str] = {
    RoadClass.NATIONAL_HIGHWAY: "speed_national_highway_kmh",
    RoadClass.STATE_HIGHWAY: "speed_state_highway_kmh",
    RoadClass.DISTRICT_ROAD: "speed_district_road_kmh",
    RoadClass.VILLAGE_ROAD: "speed_village_road_kmh",
    RoadClass.TRACK: "speed_track_kmh",
}


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in metres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def polyline_length_m(coordinates: tuple[tuple[float, float], ...]) -> float:
    """Length along the line, not across it."""
    return sum(
        haversine_m(*coordinates[i], *coordinates[i + 1])
        for i in range(len(coordinates) - 1)
    )


@dataclass(frozen=True)
class Segment:
    """One edge of the routed graph, with everything a journey needs to know."""

    id: str
    osm_id: int
    from_node: str
    to_node: str
    coordinates: tuple[tuple[float, float], ...]
    length_m: float
    road_class: RoadClass
    is_bridge: bool
    hazard_mean: float
    hazard_max: float
    #: Share of sampled points along this segment that fell inside the scored
    #: hazard surface. A segment that leaves the study area is scored on the part
    #: of it ASTRA can see, and this says how much of it that was.
    hazard_coverage: float = 1.0
    name: str | None = None
    provenance: ProvenanceClass = ProvenanceClass.REAL_OPEN

    def speed_kmh(self, config: AstraModelConfig | None = None) -> float:
        route = (config or MODEL_CONFIG).route
        return float(getattr(route, SPEED_KEYS[self.road_class]).value)

    def travel_time_min(self, config: AstraModelConfig | None = None) -> float:
        """Free-flow travel time. No congestion model: there is no data for one."""
        return self.length_m / 1000.0 / self.speed_kmh(config) * 60.0

    def p_fail(self, config: AstraModelConfig | None = None) -> float:
        """Probability this segment is impassable when it matters.

        Two independent contributions, both `DEMO_CONFIG` and both visible on
        screen.

        **Hazard, scaled by exposed length.** Failure is treated as independent
        per unit of road, so the quantity that matters is *exposed length*: mean
        hazard along the segment times how long it is. A segment carrying the
        reference length of fully exposed road takes the whole coefficient, and a
        segment with twice that takes the compounded chance of failing somewhere
        along it::

            exposed_length = hazard_mean x length
            p_hazard       = 1 - (1 - coefficient) ** (exposed_length / reference)

        This matters more than it looks. A flat per-segment probability would
        make a route's reliability depend on how many junctions OSM happens to
        have mapped along it - the same road, drawn as one way or as six, would
        report different survivability. Scaling by exposed length makes
        reliability a property of the road.

        Mean rather than maximum exposure, deliberately, and the reason is the
        opposite of comfort: OSM ways in this corridor run up to 20 km without a
        junction, and a maximum over a stretch that long saturates at the worst
        cell it happens to touch. Every long road would then look identical and
        the model would stop discriminating. The maximum is still carried on the
        segment and is what flags it as hazard-exposed on the map.

        **Bridge dependency, flat.** A bridge or culvert either stands or it does
        not; its failure chance is not a function of how long the span is.
        """
        route = (config or MODEL_CONFIG).route
        per_reference = min(max(route.p_fail_hazard_coefficient.value, 0.0), 0.99)
        exponent = self.exposed_length_m / route.p_fail_reference_length_m.value
        p_hazard = 1.0 - (1.0 - per_reference) ** exponent
        p_bridge = route.p_fail_bridge_dependency.value if self.is_bridge else 0.0
        combined = 1.0 - (1.0 - p_hazard) * (1.0 - p_bridge)
        return float(min(max(combined, 0.0), 0.99))

    @property
    def exposed_length_m(self) -> float:
        """Metres of road weighted by how hazardous the ground under it is."""
        return self.length_m * min(max(self.hazard_mean, 0.0), 1.0)

    @property
    def midpoint(self) -> tuple[float, float]:
        return self.coordinates[len(self.coordinates) // 2]

    def as_linestring(self) -> dict:
        return {
            "type": "LineString",
            "coordinates": [list(point) for point in self.coordinates],
        }


@dataclass(frozen=True)
class RoadNode:
    """A junction or way endpoint, addressed by its OSM node identifier."""

    id: str
    lon: float
    lat: float


@dataclass
class RoadNetwork:
    """The graph, plus the lookups routing needs."""

    nodes: dict[str, RoadNode]
    segments: list[Segment]
    #: Ways carrying a highway tag that arrived without usable node identifiers.
    #: They are drawn on the map but cannot be routed over, and the count is
    #: reported rather than quietly absorbed.
    unrouted_ways: int = 0
    #: Segments that lie entirely outside the scored hazard surface. Dropped from
    #: the graph rather than routed over at an implied zero risk, and counted so
    #: the omission is visible.
    unscored_segments: int = 0

    @cached_property
    def by_id(self) -> dict[str, Segment]:
        return {segment.id: segment for segment in self.segments}

    @cached_property
    def bridges(self) -> list[Segment]:
        return [segment for segment in self.segments if segment.is_bridge]

    @property
    def total_length_km(self) -> float:
        return round(sum(segment.length_m for segment in self.segments) / 1000.0, 2)

    @cached_property
    def critical_segments(self) -> frozenset[str]:
        """Segments whose loss disconnects the network: there is no way around them.

        This is the graph-theoretic definition of a bridge, and it is the honest
        answer to "is there an alternative". It is not the same as the OSM bridge
        tag - a stretch of hillside road with no parallel route is critical and
        carries no structure at all, while a culvert on a ring road is a
        structure with an alternative beside it.

        In this corridor the answer is uncomfortable and worth showing: most of
        the network has no alternative. See :attr:`redundancy`.
        """
        import networkx as nx

        graph = nx.Graph()
        graph.add_nodes_from(self.nodes)
        for segment in self.segments:
            graph.add_edge(segment.from_node, segment.to_node)
        cut_edges = {frozenset(edge) for edge in nx.bridges(graph)}
        # A cut edge in the simple graph is only genuinely critical if exactly one
        # segment runs between those nodes; parallel segments are alternatives.
        parallel: dict[frozenset[str], int] = {}
        for segment in self.segments:
            key = frozenset({segment.from_node, segment.to_node})
            parallel[key] = parallel.get(key, 0) + 1
        return frozenset(
            segment.id
            for segment in self.segments
            if frozenset({segment.from_node, segment.to_node}) in cut_edges
            and parallel[frozenset({segment.from_node, segment.to_node})] == 1
        )

    @cached_property
    def redundancy(self) -> dict[str, float]:
        """How much choice the network offers, as computed numbers.

        ``independent_loops`` is the cycle rank: the number of genuinely
        alternative ways through the network. A tree has none.
        """
        import networkx as nx

        graph = nx.Graph()
        graph.add_nodes_from(self.nodes)
        for segment in self.segments:
            graph.add_edge(segment.from_node, segment.to_node)
        components = nx.number_connected_components(graph) if graph.number_of_nodes() else 0
        loops = graph.number_of_edges() - graph.number_of_nodes() + components
        critical = len(self.critical_segments)
        return {
            "independent_loops": float(max(loops, 0)),
            "segments_without_alternative": float(critical),
            "share_without_alternative": round(
                critical / len(self.segments), 4
            )
            if self.segments
            else 0.0,
        }

    @cached_property
    def routable_nodes(self) -> frozenset[str]:
        """Nodes on the main connected network, which is what you can travel on.

        The extract contains a dozen small components: a service loop inside a
        compound, a spur that leaves the bounding box and comes back. Snapping a
        site onto one of those would produce a route that is perfectly valid
        inside a hundred metres of road and reaches nothing. The main component
        is identified once from the undamaged network, so a later closure can
        genuinely disconnect a place rather than silently re-snapping it
        somewhere else.
        """
        import networkx as nx

        graph = nx.Graph()
        graph.add_nodes_from(self.nodes)
        graph.add_edges_from(
            (segment.from_node, segment.to_node) for segment in self.segments
        )
        if graph.number_of_nodes() == 0:
            return frozenset()
        return frozenset(max(nx.connected_components(graph), key=len))

    def nearest_node(self, lon: float, lat: float) -> tuple[RoadNode, float]:
        """The nearest node on the main network, and how far off-network it is."""
        candidates = self.routable_nodes or set(self.nodes)
        best: RoadNode | None = None
        best_distance = math.inf
        for node_id in candidates:
            node = self.nodes[node_id]
            distance = haversine_m(lon, lat, node.lon, node.lat)
            if distance < best_distance:
                best, best_distance = node, distance
        assert best is not None, "an empty network cannot be snapped to"
        return best, best_distance


def _sample_hazard(
    coordinates: tuple[tuple[float, float], ...],
    grid: AnalysisGrid,
    composite: np.ndarray,
) -> tuple[float, float, float]:
    """Mean and maximum normalised composite hazard along a polyline, and coverage.

    Sampled at the polyline vertices plus interpolated points at roughly the grid
    resolution, so a long segment between two vertices cannot skip over the cell
    that makes it dangerous.

    Coverage is the share of sample points that fell inside the scored surface.
    It matters because the OSM extract runs past the edge of the study area, and
    a road ASTRA has not scored is not a safe road - it is an unscored one. The
    caller drops segments with no coverage rather than routing over them at an
    implied zero risk.
    """
    step_m = max(grid.cell.x_m, grid.cell.y_m)
    values: list[float] = []
    sampled = 0
    for index in range(len(coordinates) - 1):
        start, end = coordinates[index], coordinates[index + 1]
        span = haversine_m(*start, *end)
        steps = max(1, int(span // step_m))
        for k in range(steps):
            t = k / steps
            lon = start[0] + (end[0] - start[0]) * t
            lat = start[1] + (end[1] - start[1]) * t
            sampled += 1
            cell = grid.index_of(lon, lat)
            if cell is None:
                continue
            value = composite[cell]
            if np.isfinite(value):
                values.append(float(value))
    sampled += 1
    last = grid.index_of(*coordinates[-1])
    if last is not None and np.isfinite(composite[last]):
        values.append(float(composite[last]))
    if not values:
        return 0.0, 0.0, 0.0
    return (
        float(np.mean(values) / 100.0),
        float(np.max(values) / 100.0),
        float(len(values) / max(sampled, 1)),
    )


def build_network(
    ways: list[OsmWay], grid: AnalysisGrid, composite: np.ndarray
) -> RoadNetwork:
    """Split road ways at their shared nodes and attach hazard exposure.

    ``composite`` is the 0-100 hazard surface on ``grid``; exposure is stored
    normalised to 0-1 so the failure-probability constants read as fractions.
    """
    road_ways = [way for way in roads(ways) if len(way.node_ids) == len(way.coordinates)]
    unrouted = sum(1 for way in roads(ways) if len(way.node_ids) != len(way.coordinates))

    # A node is a junction if more than one way touches it. Way endpoints are
    # always split points, otherwise a dead-end spur would vanish into its parent.
    occurrences: Counter[int] = Counter()
    for way in road_ways:
        occurrences.update(set(way.node_ids))

    nodes: dict[str, RoadNode] = {}
    segments: list[Segment] = []
    unscored = 0
    for way in road_ways:
        split_at = {0, len(way.node_ids) - 1}
        split_at.update(
            index
            for index, node in enumerate(way.node_ids)
            if occurrences[node] > 1
        )
        ordered = sorted(split_at)
        for part, (start, end) in enumerate(zip(ordered, ordered[1:], strict=False)):
            coordinates = way.coordinates[start : end + 1]
            if len(coordinates) < 2:
                continue
            length_m = polyline_length_m(coordinates)
            if length_m <= 0.0:
                continue
            hazard_mean, hazard_max, coverage = _sample_hazard(
                coordinates, grid, composite
            )
            if coverage <= 0.0:
                # Entirely outside the scored surface. Routing over it would mean
                # assuming a road ASTRA has never looked at is a safe one, and the
                # router would then prefer it for exactly that reason.
                unscored += 1
                continue
            from_node = f"N{way.node_ids[start]}"
            to_node = f"N{way.node_ids[end]}"
            nodes.setdefault(from_node, RoadNode(from_node, *coordinates[0]))
            nodes.setdefault(to_node, RoadNode(to_node, *coordinates[-1]))
            segments.append(
                Segment(
                    id=f"W{way.osm_id}-{part}",
                    osm_id=way.osm_id,
                    from_node=from_node,
                    to_node=to_node,
                    coordinates=coordinates,
                    length_m=length_m,
                    road_class=way.road_class,
                    is_bridge=way.is_bridge,
                    hazard_mean=round(hazard_mean, 4),
                    hazard_max=round(hazard_max, 4),
                    hazard_coverage=round(coverage, 4),
                    name=way.name,
                )
            )
    return RoadNetwork(
        nodes=nodes,
        segments=segments,
        unrouted_ways=unrouted,
        unscored_segments=unscored,
    )
