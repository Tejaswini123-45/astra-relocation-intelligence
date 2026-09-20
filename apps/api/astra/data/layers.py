"""The map layer catalogue.

Availability is not a hand-maintained flag: each layer declares the artifacts it
is served from, and a layer is available exactly when those artifacts exist on
disk. A layer whose producing slice has not landed therefore cannot advertise
itself, and one that has landed cannot be forgotten.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from astra.domain.enums import ProvenanceClass
from astra.domain.models import LayerDescriptor
from astra.settings import get_settings


@dataclass(frozen=True)
class LayerSpec:
    """A declared layer plus the files that have to exist for it to be real."""

    id: str
    title: str
    description: str
    provenance: ProvenanceClass
    dataset_ids: tuple[str, ...]
    geometry_type: str
    artifacts: tuple[str, ...]
    unit: str | None = None
    default_visible: bool = False

    def is_available(self, data_dir: Path) -> bool:
        if not self.artifacts:
            return False
        return all((data_dir / artifact).exists() for artifact in self.artifacts)

    def descriptor(self, data_dir: Path) -> LayerDescriptor:
        return LayerDescriptor(
            id=self.id,
            title=self.title,
            description=self.description,
            provenance=self.provenance,
            dataset_ids=list(self.dataset_ids),
            geometry_type=self.geometry_type,  # type: ignore[arg-type]
            available=self.is_available(data_dir),
            unit=self.unit,
            default_visible=self.default_visible,
        )


LAYER_SPECS: tuple[LayerSpec, ...] = (
    LayerSpec(
        id="terrain.hillshade",
        title="Terrain and hillshade",
        description="Elevation-derived relief for the study corridor.",
        provenance=ProvenanceClass.DERIVED,
        dataset_ids=("copernicus-dem-30m",),
        geometry_type="raster",
        artifacts=("derived/hillshade.tif",),
        unit="0-255",
        default_visible=True,
    ),
    LayerSpec(
        id="terrain.elevation",
        title="Elevation",
        description="Copernicus DEM GLO-30 clipped to the corridor, in metres.",
        provenance=ProvenanceClass.REAL_OPEN,
        dataset_ids=("copernicus-dem-30m",),
        geometry_type="raster",
        artifacts=("raw/dem/copernicus_dem_30m_alaknanda.tif",),
        unit="m",
    ),
    LayerSpec(
        id="terrain.slope",
        title="Slope",
        description="Slope angle by the Horn (1981) method over the DEM.",
        provenance=ProvenanceClass.DERIVED,
        dataset_ids=("copernicus-dem-30m",),
        geometry_type="raster",
        artifacts=("derived/slope_deg.tif",),
        unit="degrees",
    ),
    LayerSpec(
        id="terrain.ruggedness",
        title="Terrain ruggedness",
        description="Riley terrain ruggedness index over the DEM.",
        provenance=ProvenanceClass.DERIVED,
        dataset_ids=("copernicus-dem-30m",),
        geometry_type="raster",
        artifacts=("derived/ruggedness.tif",),
        unit="m",
    ),
    LayerSpec(
        id="hydrology.drainage",
        title="Drainage network",
        description="Channels extracted by D8 flow accumulation over the filled DEM.",
        provenance=ProvenanceClass.DERIVED,
        dataset_ids=("copernicus-dem-30m",),
        geometry_type="raster",
        artifacts=("derived/drainage.tif",),
    ),
    LayerSpec(
        id="hydrology.hand",
        title="Height above nearest drainage",
        description="HAND surface driving the flood susceptibility sub-model.",
        provenance=ProvenanceClass.DERIVED,
        dataset_ids=("copernicus-dem-30m",),
        geometry_type="raster",
        artifacts=("derived/hand_m.tif",),
        unit="m",
    ),
    LayerSpec(
        id="hydrology.upstream_area",
        title="Upstream contributing area",
        description="Catchment area draining through each cell, for the flash-flood model.",
        provenance=ProvenanceClass.DERIVED,
        dataset_ids=("copernicus-dem-30m",),
        geometry_type="raster",
        artifacts=("derived/upstream_area_km2.tif",),
        unit="km2",
    ),
    LayerSpec(
        id="landcover.worldcover",
        title="Land cover",
        description="ESA WorldCover classes, reclassified to buildable and protected.",
        provenance=ProvenanceClass.REAL_OPEN,
        dataset_ids=("esa-worldcover-10m",),
        geometry_type="raster",
        artifacts=("raw/landcover/esa_worldcover_2021_alaknanda.tif",),
    ),
    LayerSpec(
        id="landcover.buildable",
        title="Buildable ground",
        description="Land cover classes that permit construction, per the gate rules.",
        provenance=ProvenanceClass.DERIVED,
        dataset_ids=("esa-worldcover-10m",),
        geometry_type="raster",
        artifacts=("derived/landcover_buildable.tif",),
    ),
    LayerSpec(
        id="population.worldpop",
        title="Population surface",
        description="WorldPop 2020 UN-adjusted 1 km population counts.",
        provenance=ProvenanceClass.REAL_OPEN,
        dataset_ids=("worldpop-1km-2020",),
        geometry_type="raster",
        artifacts=("raw/population/worldpop_1km_2020_alaknanda.tif",),
        unit="persons/km2",
    ),
    LayerSpec(
        id="hazard.landslide",
        title="Landslide susceptibility",
        description="Weighted overlay of slope, ruggedness, incident density, rainfall.",
        provenance=ProvenanceClass.DERIVED,
        dataset_ids=("copernicus-dem-30m", "landslide-inventory", "rainfall-gridded"),
        geometry_type="raster",
        artifacts=("derived/hazard_landslide.tif",),
        unit="index 0-100",
    ),
    LayerSpec(
        id="hazard.flood",
        title="Flood susceptibility",
        description="HAND, drainage proximity, historical inundation and rainfall.",
        provenance=ProvenanceClass.DERIVED,
        dataset_ids=("copernicus-dem-30m", "rainfall-gridded"),
        geometry_type="raster",
        artifacts=("derived/hazard_flood.tif",),
        unit="index 0-100",
    ),
    LayerSpec(
        id="hazard.cloudburst",
        title="Cloudburst and flash-flood susceptibility",
        description="Extreme rainfall frequency, catchment steepness and confluences.",
        provenance=ProvenanceClass.DERIVED,
        dataset_ids=("copernicus-dem-30m", "rainfall-gridded"),
        geometry_type="raster",
        artifacts=("derived/hazard_cloudburst.tif",),
        unit="index 0-100",
    ),
    LayerSpec(
        id="hazard.composite",
        title="Multi-hazard composite",
        description="Dominance-preserving composite of the per-hazard surfaces.",
        provenance=ProvenanceClass.DERIVED,
        dataset_ids=("copernicus-dem-30m", "landslide-inventory", "rainfall-gridded"),
        geometry_type="raster",
        artifacts=("derived/hazard_composite.tif",),
        unit="index 0-100",
        default_visible=True,
    ),
    LayerSpec(
        id="hazard.red_zones",
        title="Red zones (ASTRA analytical classification)",
        description=(
            "Thresholded, cleaned and buffered composite polygons. An ASTRA analytical "
            "classification, not a statutory designation."
        ),
        provenance=ProvenanceClass.DERIVED,
        dataset_ids=("copernicus-dem-30m", "landslide-inventory", "rainfall-gridded"),
        geometry_type="polygon",
        artifacts=("derived/red_zones.geojson",),
        default_visible=True,
    ),
    LayerSpec(
        id="hazard.confidence",
        title="Evidence confidence",
        description="Where the evidence is thin. Rendered distinctly, never as certainty.",
        provenance=ProvenanceClass.DERIVED,
        dataset_ids=("copernicus-dem-30m", "landslide-inventory"),
        geometry_type="raster",
        artifacts=("derived/confidence.tif",),
    ),
    LayerSpec(
        id="history.incidents",
        title="Historical incidents",
        description="Recorded landslide incident points with severity, trigger and date.",
        provenance=ProvenanceClass.REAL_OPEN,
        dataset_ids=("landslide-inventory",),
        geometry_type="point",
        artifacts=("raw/incidents/nasa_glc_uttarakhand.csv",),
    ),
    LayerSpec(
        id="exposure.habitations",
        title="Habitations",
        description=(
            "Synthetic, terrain-calibrated settlements with fictional names. Not real "
            "villages."
        ),
        provenance=ProvenanceClass.SYNTHETIC_CALIBRATED,
        dataset_ids=("astra-habitations",),
        geometry_type="point",
        artifacts=("fixtures/habitations.json",),
        default_visible=True,
    ),
    LayerSpec(
        id="sites.candidates",
        title="Candidate relocation sites",
        description="Synthetic candidate sites with modelled service supply.",
        provenance=ProvenanceClass.SYNTHETIC_CALIBRATED,
        dataset_ids=("astra-sites",),
        geometry_type="point",
        artifacts=("fixtures/sites.json",),
        default_visible=True,
    ),
    LayerSpec(
        id="network.roads",
        title="Road network",
        description="OpenStreetMap road ways with class and bridge tags.",
        provenance=ProvenanceClass.REAL_OPEN,
        dataset_ids=("osm-extract",),
        geometry_type="line",
        artifacts=("raw/osm/osm_alaknanda_network.json",),
        default_visible=True,
    ),
    LayerSpec(
        id="network.road_distance",
        title="Distance to road",
        description="Distance from every cell to the nearest mapped road.",
        provenance=ProvenanceClass.DERIVED,
        dataset_ids=("osm-extract",),
        geometry_type="raster",
        artifacts=("derived/road_distance_m.tif",),
        unit="m",
    ),
    LayerSpec(
        id="network.routes",
        title="Evaluated routes",
        description="Fastest and safest routes with per-route reliability.",
        provenance=ProvenanceClass.DERIVED,
        dataset_ids=("osm-extract",),
        geometry_type="line",
        artifacts=("derived/routes.geojson",),
    ),
    LayerSpec(
        id="plan.assignments",
        title="Optimised assignments",
        description="Solver output: who moves where, in which phase.",
        provenance=ProvenanceClass.DERIVED,
        dataset_ids=("astra-habitations", "astra-sites"),
        geometry_type="line",
        artifacts=("derived/assignments.geojson",),
    ),
)

SPECS_BY_ID: dict[str, LayerSpec] = {spec.id: spec for spec in LAYER_SPECS}


def layer_catalogue(data_dir: Path | None = None) -> list[LayerDescriptor]:
    """The catalogue as the API serves it, with availability resolved from disk."""
    root = data_dir or get_settings().data_dir
    return [spec.descriptor(root) for spec in LAYER_SPECS]
