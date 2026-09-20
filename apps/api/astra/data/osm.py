"""OpenStreetMap extract parsing.

The Overpass response is vendored verbatim; this module turns it into the road
and waterway geometry the rest of ASTRA uses. The road-class mapping is stated
here explicitly because it decides free-flow speed and, through that, every
travel time in the plan.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from rasterio.features import rasterize
from rasterio.transform import Affine
from scipy import ndimage

from astra.data.terrain import CellSize
from astra.domain.enums import RoadClass

#: OSM highway tag to ASTRA road class. Anything unlisted is treated as a track,
#: which is the conservative choice: lowest speed, lowest reliability.
HIGHWAY_TO_CLASS: dict[str, RoadClass] = {
    "motorway": RoadClass.NATIONAL_HIGHWAY,
    "trunk": RoadClass.NATIONAL_HIGHWAY,
    "primary": RoadClass.STATE_HIGHWAY,
    "secondary": RoadClass.DISTRICT_ROAD,
    "tertiary": RoadClass.DISTRICT_ROAD,
    "unclassified": RoadClass.VILLAGE_ROAD,
    "residential": RoadClass.VILLAGE_ROAD,
    "road": RoadClass.VILLAGE_ROAD,
    "track": RoadClass.TRACK,
}


@dataclass(frozen=True)
class OsmWay:
    """One OSM way with its full geometry, as returned by ``out body geom``."""

    osm_id: int
    tags: dict[str, str]
    coordinates: tuple[tuple[float, float], ...]
    #: OSM node identifiers, positionally aligned with ``coordinates``. Two ways
    #: that share a node meet there, which is the only thing that makes the road
    #: network a graph rather than a pile of unconnected lines.
    node_ids: tuple[int, ...] = ()

    @property
    def highway(self) -> str | None:
        return self.tags.get("highway")

    @property
    def waterway(self) -> str | None:
        return self.tags.get("waterway")

    @property
    def road_class(self) -> RoadClass:
        return HIGHWAY_TO_CLASS.get(self.highway or "", RoadClass.TRACK)

    @property
    def is_bridge(self) -> bool:
        return self.tags.get("bridge") not in (None, "no") or self.tags.get(
            "man_made"
        ) == "bridge"

    @property
    def name(self) -> str | None:
        return self.tags.get("name")

    def as_linestring(self) -> dict[str, Any]:
        return {"type": "LineString", "coordinates": [list(c) for c in self.coordinates]}


def load_ways(path: Path) -> list[OsmWay]:
    """Parse the vendored Overpass JSON. Never touches the network."""
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    ways: list[OsmWay] = []
    for element in payload.get("elements", []):
        if element.get("type") != "way":
            continue
        geometry = element.get("geometry") or []
        coordinates = tuple(
            (float(point["lon"]), float(point["lat"]))
            for point in geometry
            if "lon" in point and "lat" in point
        )
        if len(coordinates) < 2:
            continue
        node_ids = tuple(int(node) for node in (element.get("nodes") or []))
        if len(node_ids) != len(coordinates):
            # ``out body geom`` returns both arrays in the same order. If a way
            # arrives with them out of step the topology cannot be trusted, so
            # the way keeps its geometry for display and is excluded from the
            # routed graph rather than being wired up on a guess.
            node_ids = ()
        ways.append(
            OsmWay(
                osm_id=int(element["id"]),
                tags={str(k): str(v) for k, v in (element.get("tags") or {}).items()},
                coordinates=coordinates,
                node_ids=node_ids,
            )
        )
    return ways


def roads(ways: Iterable[OsmWay]) -> list[OsmWay]:
    return [way for way in ways if way.highway]


def waterways(ways: Iterable[OsmWay]) -> list[OsmWay]:
    return [way for way in ways if way.waterway]


def distance_to_ways(
    ways: Iterable[OsmWay],
    transform: Affine,
    shape: tuple[int, int],
    cell: CellSize,
) -> np.ndarray:
    """Distance in metres from every cell to the nearest of these ways."""
    shapes = [(way.as_linestring(), 1) for way in ways]
    if not shapes:
        return np.full(shape, np.inf)
    burned = rasterize(
        shapes, out_shape=shape, transform=transform, fill=0, default_value=1, dtype="uint8"
    )
    distance = ndimage.distance_transform_edt(burned == 0, sampling=(cell.y_m, cell.x_m))
    return np.asarray(distance, dtype="float64")
