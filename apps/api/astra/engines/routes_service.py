"""Wiring Engine 5 to the corridor.

The route engine takes a graph and two points. This module builds that graph from
the vendored OSM extract and the computed hazard surface, evaluates every
habitation-to-site pair, and answers the two questions the rest of ASTRA asks of
it:

* **Can these people reach this site, and how reliably?** The optimiser in the
  next slice consumes exactly this, and treats an infeasible pair as unavailable
  rather than expensive.
* **How many people can the approach to this site deliver?** That is the ACCESS
  capacity constraint Engine 4 declared pending, and it is now computed.

Closures are a first-class argument, not a mutation. Evaluating the corridor with
a bridge shut returns a new assessment beside the baseline; nothing is replaced,
so a before-and-after diff is a comparison of two real results.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from astra.data import osm
from astra.domain.model_config import MODEL_CONFIG
from astra.domain.models import CandidateSite, Habitation
from astra.engines.network import RoadNetwork, build_network
from astra.engines.routes import Route, RouteEngine, RoutePair, route_geometry
from astra.engines.service import RiskRun, baseline_risk
from astra.settings import get_settings


class RouteDataError(RuntimeError):
    """The OSM extract is missing. Routing is not attempted without a network."""


@dataclass(frozen=True)
class SiteAccess:
    """What the road network means for one candidate site."""

    site_id: str
    reachable_habitations: int
    feasible_habitations: int
    usable_routes: int
    best_reliability: float
    median_travel_time_min: float
    access_capacity_persons: float
    #: Population that could reach this site on a route above the reliability
    #: threshold. Not a capacity - it is demand that has a road.
    population_with_feasible_route: int


@dataclass(frozen=True)
class CorridorRoutes:
    """Every habitation-to-site pair under one set of closures."""

    network: RoadNetwork
    pairs: dict[tuple[str, str], RoutePair]
    closed_segments: frozenset[str]
    access: dict[str, SiteAccess]

    def pair(self, habitation_id: str, site_id: str) -> RoutePair | None:
        return self.pairs.get((habitation_id, site_id))

    @property
    def feasible_pair_count(self) -> int:
        return sum(1 for pair in self.pairs.values() if pair.safest.feasible)

    def geometry_of(self, route: Route) -> list[list[float]]:
        return route_geometry(route, self.network)


@lru_cache(maxsize=1)
def corridor_network() -> RoadNetwork:
    """The routed graph over the baseline hazard surface, built once."""
    settings = get_settings()
    path = settings.raw_dir / "osm" / "osm_alaknanda_network.json"
    if not path.exists():
        raise RouteDataError(
            f"OSM extract missing at {path}; run scripts/ingest.py before starting"
        )
    run: RiskRun = baseline_risk()
    return build_network(osm.load_ways(path), run.grid, run.result.composite)


def evaluate_corridor(
    *,
    closed_segments: frozenset[str] = frozenset(),
    habitations: list[Habitation] | None = None,
    sites: list[CandidateSite] | None = None,
    network: RoadNetwork | None = None,
) -> CorridorRoutes:
    """Route every habitation to every site under the given closures.

    ``network`` is supplied by a scenario whose perturbation moved the hazard
    surface: segment failure probability is sampled from that surface, so a
    rainfall scenario must be routed over a graph rebuilt against it rather than
    the baseline one.
    """
    network = network or corridor_network()
    run = baseline_risk()
    habitations = habitations if habitations is not None else list(run.context.habitations)
    sites = sites if sites is not None else list(run.context.sites)
    engine = RouteEngine(network=network, closed_segments=closed_segments)

    pairs: dict[tuple[str, str], RoutePair] = {}
    for habitation in habitations:
        for site in sites:
            pairs[(habitation.id, site.id)] = engine.pair(
                origin_id=habitation.id,
                destination_id=site.id,
                origin=(habitation.centroid.lon, habitation.centroid.lat),
                destination=(site.centroid.lon, site.centroid.lat),
            )

    return CorridorRoutes(
        network=network,
        pairs=pairs,
        closed_segments=closed_segments,
        access=_site_access(pairs, habitations, sites),
    )


def _site_access(
    pairs: dict[tuple[str, str], RoutePair],
    habitations: list[Habitation],
    sites: list[CandidateSite],
) -> dict[str, SiteAccess]:
    """Turn the pair matrix into the per-site access picture, including capacity."""
    route_config = MODEL_CONFIG.route
    throughput = route_config.throughput_persons_per_hour.value
    window = route_config.access_movement_window_hours.value
    population = {habitation.id: habitation.population for habitation in habitations}

    access: dict[str, SiteAccess] = {}
    for site in sites:
        site_pairs = [
            pairs[(habitation.id, site.id)]
            for habitation in habitations
            if (habitation.id, site.id) in pairs
        ]
        reachable = [pair for pair in site_pairs if pair.safest.legs]
        feasible = [pair for pair in reachable if pair.safest.feasible]

        # A site is approached over the roads that actually reach it. Distinct
        # approaches are counted by the last segment travelled: two habitations
        # arriving down the same final stretch share its throughput, and pretending
        # otherwise would multiply capacity by the number of origins.
        approaches = {
            pair.safest.legs[-1].segment_id for pair in feasible if pair.safest.legs
        }
        times = sorted(pair.safest.travel_time_min for pair in feasible)
        median = times[len(times) // 2] if times else 0.0

        access[site.id] = SiteAccess(
            site_id=site.id,
            reachable_habitations=len(reachable),
            feasible_habitations=len(feasible),
            usable_routes=len(approaches),
            best_reliability=round(
                max((pair.safest.reliability for pair in feasible), default=0.0), 4
            ),
            median_travel_time_min=round(median, 1),
            access_capacity_persons=round(len(approaches) * throughput * window, 1),
            population_with_feasible_route=sum(
                population.get(pair.origin_id, 0) for pair in feasible
            ),
        )
    return access


@lru_cache(maxsize=1)
def baseline_routes() -> CorridorRoutes:
    """The cached open-network assessment, warmed at API startup."""
    return evaluate_corridor()


def network_summary(network: RoadNetwork) -> dict:
    """Facts about the graph itself, for the transparency panel."""
    by_class: dict[str, int] = {}
    for segment in network.segments:
        by_class[segment.road_class.value] = by_class.get(segment.road_class.value, 0) + 1
    redundancy = network.redundancy
    return {
        "nodes": len(network.nodes),
        "segments": len(network.segments),
        "bridge_segments": len(network.bridges),
        "total_length_km": network.total_length_km,
        "segments_by_class": by_class,
        "unrouted_ways": network.unrouted_ways,
        "unscored_segments": network.unscored_segments,
        "independent_loops": int(redundancy["independent_loops"]),
        "segments_without_alternative": int(redundancy["segments_without_alternative"]),
        "share_without_alternative": redundancy["share_without_alternative"],
    }
