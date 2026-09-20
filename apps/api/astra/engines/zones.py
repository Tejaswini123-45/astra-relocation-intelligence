"""Red-zone derivation: from a scored surface to inspectable polygons.

CLAUDE.md section 5.1: red zones are spatial, not a scalar cutoff. The composite
surface is thresholded into classes, morphologically cleaned so that single-cell
speckle does not become a "zone", buffered, and only then turned into polygons
with attributes attached.

Every polygon carries the arithmetic behind it: its class, its mean and maximum
composite score, the dominant hazard inside it, the population it intersects,
its evidence confidence and the rule version that produced it. A zone that
cannot say why it is a zone does not ship.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from pyproj import Geod
from rasterio import features
from scipy import ndimage
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from astra.domain.enums import HazardType, ZoneClass
from astra.domain.model_config import MODEL_CONFIG, AstraModelConfig
from astra.domain.models import Habitation
from astra.engines.grid import AnalysisGrid
from astra.engines.hazard import ZONE_ORDER, HazardResult

GEOD = Geod(ellps="WGS84")

ZONE_RULE_VERSION = "1.0.0"
"""Bumped when the cleaning, buffering or thresholding rules change."""


@dataclass(frozen=True)
class ZonePolygon:
    """One analytical red zone."""

    id: str
    zone_class: ZoneClass
    geometry: BaseGeometry
    area_km2: float
    mean_composite: float
    max_composite: float
    dominant_hazard: HazardType
    hazard_mix: dict[HazardType, float]
    mean_confidence: float
    cell_count: int
    population_intersected: int
    habitation_ids: list[str]
    rule_version: str = ZONE_RULE_VERSION

    def as_feature(self) -> dict:
        return {
            "type": "Feature",
            "id": self.id,
            "geometry": mapping(self.geometry),
            "properties": {
                "id": self.id,
                "zone_class": self.zone_class.value,
                "classification_label": "ASTRA analytical classification",
                "area_km2": round(self.area_km2, 3),
                "mean_composite": round(self.mean_composite, 2),
                "max_composite": round(self.max_composite, 2),
                "dominant_hazard": self.dominant_hazard.value,
                "hazard_mix": {k.value: round(v, 3) for k, v in self.hazard_mix.items()},
                "mean_confidence": round(self.mean_confidence, 3),
                "cell_count": self.cell_count,
                "population_intersected": self.population_intersected,
                "habitation_ids": self.habitation_ids,
                "rule_version": self.rule_version,
            },
        }


def _geodesic_area_km2(geometry: BaseGeometry) -> float:
    area, _ = GEOD.geometry_area_perimeter(geometry)
    return abs(area) / 1e6


def _clean_class_mask(
    mask: np.ndarray, grid: AnalysisGrid, min_area_ha: float
) -> np.ndarray:
    """Remove speckle below the minimum mapping unit, then close small holes.

    A susceptibility surface always produces isolated cells above a threshold.
    Publishing those as "zones" would be noise presented as a finding.
    """
    min_cells = max(1, int(round(min_area_ha * 10_000 / grid.cell.area_m2)))
    labelled, count = ndimage.label(mask, structure=np.ones((3, 3), dtype=int))
    if count == 0:
        return mask
    sizes = np.bincount(labelled.ravel())
    keep = np.zeros(sizes.shape, dtype=bool)
    keep[1:] = sizes[1:] >= min_cells
    cleaned = keep[labelled]
    # Close pinholes so a zone is not perforated by single sub-threshold cells.
    return ndimage.binary_closing(cleaned, structure=np.ones((3, 3), dtype=bool))


def _dilate(mask: np.ndarray, grid: AnalysisGrid, distance_m: float) -> np.ndarray:
    """Grow a mask outwards by a ground distance, on the raster.

    Buffering the polygons instead would let neighbouring zones overlap and
    double-count their area. Growing the mask keeps the zones a partition of the
    grid, so the areas reported always add up.
    """
    cells = int(round(distance_m / grid.cell.mean_m))
    if cells < 1:
        return mask
    structure = ndimage.generate_binary_structure(2, 2)
    return ndimage.binary_dilation(mask, structure=structure, iterations=cells)


def derive_zones(
    result: HazardResult,
    habitations: list[Habitation] | None = None,
    config: AstraModelConfig | None = None,
) -> list[ZonePolygon]:
    """Turn the classified composite surface into attributed zone polygons.

    Zones are built from the most severe class downwards and each class is
    excluded from the ones below it, so the published polygons never overlap and
    the reported areas and populations sum correctly.
    """
    config = config or MODEL_CONFIG
    grid = result.grid
    min_area_ha = config.hazard.min_mapping_unit_ha.value
    buffer_m = config.hazard.zone_buffer_m.value
    simplify_deg = abs(grid.transform.a) * 0.5

    habitation_points = [
        (
            habitation,
            shape(
                {
                    "type": "Point",
                    "coordinates": [habitation.centroid.lon, habitation.centroid.lat],
                }
            ),
        )
        for habitation in (habitations or [])
    ]

    zones: list[ZonePolygon] = []
    counter = 0
    claimed = np.zeros(grid.shape, dtype=bool)

    for ordinal in range(len(ZONE_ORDER) - 1, 0, -1):
        zone_class = ZONE_ORDER[ordinal]
        # At or above this class, so a buffered Critical zone does not leave a
        # hole in the Elevated zone around it.
        mask = result.zone_class >= ordinal
        if not mask.any():
            continue
        cleaned = _clean_class_mask(mask, grid, min_area_ha)
        grown = _dilate(cleaned, grid, buffer_m) & ~claimed
        if not grown.any():
            continue
        claimed |= grown

        shapes = features.shapes(
            grown.astype("uint8"), mask=grown, transform=grid.transform
        )
        for geom_dict, value in shapes:
            if value != 1:
                continue
            geometry = shape(geom_dict).simplify(simplify_deg, preserve_topology=True)
            if geometry.is_empty or not geometry.is_valid:
                continue

            cell_mask = features.geometry_mask(
                [mapping(geometry)],
                out_shape=grid.shape,
                transform=grid.transform,
                invert=True,
            ) & grown
            if not cell_mask.any():
                continue
            if cell_mask.sum() * grid.cell.area_m2 < min_area_ha * 10_000:
                continue

            composite_values = result.composite[cell_mask]
            confidence_values = result.confidence[cell_mask]
            dominant_values = result.dominant[cell_mask]

            counts = np.bincount(
                dominant_values.astype(int), minlength=len(result.hazards_modelled)
            )
            total = float(counts.sum())
            mix = {
                result.hazards_modelled[index]: float(count) / total
                for index, count in enumerate(counts)
                if count > 0
            }
            dominant = max(mix, key=lambda hazard: mix[hazard])

            intersecting = [
                habitation
                for habitation, point in habitation_points
                if geometry.covers(point)
            ]

            counter += 1
            zones.append(
                ZonePolygon(
                    id=f"Z-{zone_class.value[:3]}-{counter:03d}",
                    zone_class=zone_class,
                    geometry=geometry,
                    area_km2=_geodesic_area_km2(geometry),
                    mean_composite=float(np.mean(composite_values)),
                    max_composite=float(np.max(composite_values)),
                    dominant_hazard=dominant,
                    hazard_mix=mix,
                    mean_confidence=float(np.mean(confidence_values)),
                    cell_count=int(cell_mask.sum()),
                    population_intersected=sum(h.population for h in intersecting),
                    habitation_ids=[h.id for h in intersecting],
                )
            )

    zones.sort(key=lambda zone: (-zone.max_composite, -zone.area_km2))
    return zones


def zone_summary(zones: list[ZonePolygon]) -> dict[str, dict[str, float]]:
    """Totals per class, for the header strip. Computed once, here, not in the UI."""
    summary: dict[str, dict[str, float]] = {}
    for zone_class in ZONE_ORDER:
        selected = [zone for zone in zones if zone.zone_class is zone_class]
        summary[zone_class.value] = {
            "count": len(selected),
            "area_km2": round(sum(zone.area_km2 for zone in selected), 3),
            "population_intersected": sum(zone.population_intersected for zone in selected),
        }
    return summary


def merged_geometry(zones: list[ZonePolygon], zone_class: ZoneClass) -> BaseGeometry | None:
    """Union of every polygon in one class, used by the site suitability gate."""
    selected = [zone.geometry for zone in zones if zone.zone_class is zone_class]
    if not selected:
        return None
    return unary_union(selected)


def metres_to_degrees(metres: float, latitude: float) -> float:
    """Convert a ground distance to degrees of longitude at a given latitude."""
    return metres / (111_320.0 * math.cos(math.radians(latitude)))
