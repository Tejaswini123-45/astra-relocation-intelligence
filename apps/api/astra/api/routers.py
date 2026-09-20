"""HTTP routers.

Endpoints are added as the slice that produces their data lands. An endpoint
that would return an empty list because its engine has not been built yet is not
registered at all - a judge clicking through the API should never meet a hollow
route (CLAUDE.md section 2.3).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from astra import __version__
from astra.api.schemas import (
    DerivedLayerSummary,
    HabitationsResponse,
    LayersResponse,
    ModelConfigResponse,
    ProvenanceResponse,
    ScenarioListResponse,
    ScenarioResponse,
    SitesResponse,
    StudyAreaDataResponse,
    ValidationCheckResponse,
)
from astra.data import osm
from astra.data.fixtures import load_fixtures
from astra.data.layers import layer_catalogue
from astra.data.provenance import get_registry
from astra.data.rasters import read_manifest
from astra.data.scenarios import get_scenario, list_scenarios
from astra.data.study_area import STUDY_AREAS, get_study_area
from astra.data.validate import validate_all
from astra.domain.model_config import (
    ENGINE_VERSION,
    MODEL_CONFIG,
    MODEL_CONFIG_VERSION,
)
from astra.domain.models import HealthStatus
from astra.domain.notices import NOTICES
from astra.domain.registry import FORMULAS
from astra.settings import get_settings

router = APIRouter()

STARTED_AT = datetime.now(UTC)


@router.get("/health", response_model=HealthStatus, tags=["system"])
def health() -> HealthStatus:
    """Liveness plus the honest system state the UI header renders."""
    report = validate_all()
    settings = get_settings()
    return HealthStatus(
        status="ok" if report.ok else "degraded",
        api_version=__version__,
        engine_version=ENGINE_VERSION,
        model_config_version=MODEL_CONFIG_VERSION,
        environment=settings.env,
        fixtures_valid=report.ok,
        fixture_count=report.fixture_count,
        llm_mode=settings.llm_mode,  # type: ignore[arg-type]
        how_this_works=NOTICES.how_this_works,
        decision_authority=NOTICES.decision_authority,
        started_at=STARTED_AT,
        checked_at=datetime.now(UTC),
    )


@router.get("/model/config", response_model=ModelConfigResponse, tags=["transparency"])
def model_config() -> ModelConfigResponse:
    """Every weight, threshold, norm and formula ASTRA uses, with provenance."""
    return ModelConfigResponse(
        config=MODEL_CONFIG,
        constants=MODEL_CONFIG.constants(),
        formulas=sorted(FORMULAS.values(), key=lambda spec: spec.formula_id),
        notices=NOTICES,
    )


@router.get("/provenance", response_model=ProvenanceResponse, tags=["transparency"])
def provenance() -> ProvenanceResponse:
    """The dataset registry: real, derived, synthetic and demo-config, unblended."""
    registry = get_registry()
    settings = get_settings()
    payload = json.loads(settings.provenance_path.read_text(encoding="utf-8"))
    return ProvenanceResponse(
        registry_version=payload.get("registry_version", "unknown"),
        datasets=registry.records,
        counts_by_class=registry.counts(),
        note=payload.get("note", ""),
    )


@router.get("/layers", response_model=LayersResponse, tags=["transparency"])
def layers() -> LayersResponse:
    """The layer catalogue. Layers not yet produced are declared but unavailable."""
    catalogue = layer_catalogue()
    return LayersResponse(
        layers=catalogue,
        available_count=sum(1 for layer in catalogue if layer.available),
        declared_count=len(catalogue),
    )


@router.get("/validation/fixtures", response_model=ValidationCheckResponse, tags=["transparency"])
def fixture_validation() -> ValidationCheckResponse:
    """The integrity gate result, visible in the interface rather than buried in a log."""
    report = validate_all()
    return ValidationCheckResponse(
        ok=report.ok,
        summary=report.summary(),
        checks=report.checked,
        errors=report.errors,
        warnings=report.warnings,
        fixture_count=report.fixture_count,
        dataset_count=report.dataset_count,
    )


@router.get("/scenarios", response_model=ScenarioListResponse, tags=["scenarios"])
def scenarios() -> ScenarioListResponse:
    return ScenarioListResponse(
        scenarios=list_scenarios(),
        study_areas=list(STUDY_AREAS.values()),
    )


@router.get("/scenarios/{scenario_id}", response_model=ScenarioResponse, tags=["scenarios"])
def scenario_detail(scenario_id: str) -> ScenarioResponse:
    scenario = get_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"unknown scenario '{scenario_id}'")
    return ScenarioResponse(
        scenario=scenario,
        study_area=get_study_area(scenario.study_area_id),
    )


@router.get("/habitations", response_model=HabitationsResponse, tags=["exposure"])
def habitations() -> HabitationsResponse:
    """The habitation layer. Synthetic, fictional, terrain-calibrated records."""
    bundle = load_fixtures()
    if not bundle.habitations:
        raise HTTPException(
            status_code=503,
            detail="habitation fixtures are not seeded; run scripts/seed_fixtures.py",
        )
    return HabitationsResponse(
        habitations=bundle.habitations,
        total_population=sum(h.population for h in bundle.habitations),
        total_households=sum(h.households for h in bundle.habitations),
        disclaimer=NOTICES.scenario_disclaimer,
    )


@router.get("/sites", response_model=SitesResponse, tags=["capacity"])
def sites() -> SitesResponse:
    """Candidate relocation sites. Capacity analysis arrives with its engine."""
    bundle = load_fixtures()
    if not bundle.sites:
        raise HTTPException(
            status_code=503,
            detail="site fixtures are not seeded; run scripts/seed_fixtures.py",
        )
    return SitesResponse(
        sites=bundle.sites,
        total_gross_area_m2=round(sum(s.gross_area_m2 for s in bundle.sites), 1),
        limitation=NOTICES.site_tenure_limitation,
    )


@router.get("/study-area/data", response_model=StudyAreaDataResponse, tags=["transparency"])
def study_area_data() -> StudyAreaDataResponse:
    """The derived-surface build state: what was computed, by which method, and its range."""
    settings = get_settings()
    manifest_path = settings.derived_dir / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(
            status_code=503,
            detail="derived layers are not built; run scripts/build_derived.py",
        )
    manifest = read_manifest(manifest_path)
    generation_path = settings.fixtures_dir / "generation_manifest.json"
    generation = read_manifest(generation_path) if generation_path.exists() else {}
    area = get_study_area(manifest.get("study_area"))
    layers_payload = [
        DerivedLayerSummary(
            name=name,
            file=entry["file"],
            unit=entry["unit"],
            dtype=entry["dtype"],
            bytes=entry["bytes"],
            min=entry["min"],
            max=entry["max"],
            mean=entry["mean"],
            note=entry["note"],
        )
        for name, entry in manifest.get("layers", {}).items()
    ]
    return StudyAreaDataResponse(
        study_area=area,
        grid=manifest.get("grid", {}),
        methods=manifest.get("methods", {}),
        channel_threshold_km2=manifest.get("channel_threshold_km2", 0.0),
        layers=layers_payload,
        generation=generation,
        terrain_preview_url="/study-area/terrain.jpg",
        terrain_preview_bbox=area.bbox.as_list(),
        landcover_refinement=manifest.get("landcover_refinement"),
    )


@router.get("/study-area/terrain.jpg", tags=["transparency"], response_class=FileResponse)
def terrain_preview() -> FileResponse:
    """Shaded relief rendered from the vendored DEM, georeferenced by the study bbox."""
    path = get_settings().derived_dir / "terrain_preview.jpg"
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail="terrain preview is not built; run scripts/build_derived.py",
        )
    return FileResponse(path, media_type="image/jpeg")


@router.get("/layers/roads.geojson", tags=["transparency"])
def roads_geojson() -> dict:
    """The OSM road network as GeoJSON, for map context and route work."""
    settings = get_settings()
    path = settings.raw_dir / "osm" / "osm_alaknanda_network.json"
    if not path.exists():
        raise HTTPException(
            status_code=503, detail="OSM extract is not vendored; run scripts/ingest.py"
        )
    ways = osm.load_ways(path)
    features = [
        {
            "type": "Feature",
            "id": way.osm_id,
            "geometry": way.as_linestring(),
            "properties": {
                "osm_id": way.osm_id,
                "kind": "road" if way.highway else "waterway",
                "road_class": way.road_class.value if way.highway else None,
                "highway": way.highway,
                "waterway": way.waterway,
                "bridge": way.is_bridge,
                "name": way.name,
            },
        }
        for way in ways
    ]
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "source": "OpenStreetMap contributors, ODbL 1.0",
            "road_ways": sum(1 for way in ways if way.highway),
            "waterway_ways": sum(1 for way in ways if way.waterway),
        },
    }
