"""Risk endpoints: the hazard surfaces, the analytical red zones and their arithmetic.

Every route here answers from a computed run. Nothing is read from a fixture of
precomputed answers, and every score can be taken apart into the factors that
produced it in one further request.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from shapely.geometry import mapping

from astra.api.schemas import (
    HabitationHazardResponse,
    HabitationHazardRow,
    RiskCellResponse,
    RiskSummaryResponse,
    ZoneClassSummary,
    ZoneFeature,
    ZoneFeatureProperties,
    ZonesResponse,
)
from astra.domain.model_config import MODEL_CONFIG
from astra.domain.models import Geometry
from astra.domain.notices import (
    CLASSIFICATION_LABEL,
    DECISION_AUTHORITY,
    PRIORITY_NOT_PROBABILITY,
    SCENARIO_DISCLAIMER,
)
from astra.domain.registry import get_formula
from astra.engines.context import load_incidents
from astra.engines.service import (
    baseline_risk,
    confidence_at_cell,
    hazard_at_cell,
    hazard_class_share,
    hazard_over_footprint,
    hazard_statistics,
    zone_of,
)
from astra.settings import get_settings

router = APIRouter(prefix="/risk", tags=["risk"])

#: Radius over which hazard is summarised for a habitation. Settlements in this
#: corridor are compact; 300 m covers the built footprint plus its immediate
#: slope without reaching into the next valley.
FOOTPRINT_RADIUS_M = 300.0


@router.get("/summary", response_model=RiskSummaryResponse)
def risk_summary() -> RiskSummaryResponse:
    """What Engine 1 computed for the baseline scenario, and over what inputs."""
    run = baseline_risk()
    hazard_config = MODEL_CONFIG.hazard
    all_incidents = len(load_incidents())
    return RiskSummaryResponse(
        study_area=run.context.study_area,
        grid_rows=run.grid.rows,
        grid_cols=run.grid.cols,
        cell_x_m=round(run.grid.cell.x_m, 2),
        cell_y_m=round(run.grid.cell.y_m, 2),
        hazards_modelled=list(run.result.hazards_modelled),
        class_share_percent=hazard_class_share(run),
        hazard_statistics=hazard_statistics(run),
        zone_summary={
            key: ZoneClassSummary(**value) for key, value in run.summary().items()
        },
        incidents_used=len(run.context.incidents),
        incidents_excluded=all_incidents - len(run.context.incidents),
        rainfall_points=len(run.context.rainfall),
        composite_lambda=hazard_config.composite_lambda.value,
        zone_thresholds={
            "CRITICAL": hazard_config.zone_threshold_critical.value,
            "ELEVATED": hazard_config.zone_threshold_elevated.value,
            "WATCH": hazard_config.zone_threshold_watch.value,
        },
        overlay_url="/risk/overlay/composite.png",
        overlay_bbox=run.context.study_area.bbox.as_list(),
        terrain_url="/study-area/terrain.jpg",
        computed_ms=round(run.computed_ms, 1),
        model_config_version=run.result.config_version,
        engine_version=run.result.engine_version,
    )


#: Decimal places kept on zone vertices in the wire format. Five decimals of a
#: degree is about a metre on the ground - two orders of magnitude finer than the
#: 100 m cells zones are built from - and it roughly halves the payload every map
#: screen downloads. Areas and intersections are computed on the full-precision
#: geometry before this point; only the transport is rounded.
COORDINATE_DECIMALS = 5


def _rounded(coordinates):
    if coordinates and isinstance(coordinates[0], (int, float)):
        return [round(value, COORDINATE_DECIMALS) for value in coordinates]
    return [_rounded(part) for part in coordinates]


def _wire_geometry(geometry) -> Geometry:
    shape = mapping(geometry)
    return Geometry.model_validate(
        {"type": shape["type"], "coordinates": _rounded(shape["coordinates"])}
    )


def serialise_zones(run, zone_class: str | None = None) -> ZonesResponse:
    """The zone payload for any risk run, baseline or scenario.

    Shared so a simulated red-zone map is byte-for-byte the same shape as the
    baseline one, and a screen that renders one renders the other.
    """
    zones = run.zones
    if zone_class:
        wanted = zone_class.upper()
        zones = [zone for zone in zones if zone.zone_class.value == wanted]

    features = [
        ZoneFeature(
            id=zone.id,
            geometry=_wire_geometry(zone.geometry),
            properties=ZoneFeatureProperties(
                id=zone.id,
                zone_class=zone.zone_class,
                classification_label=CLASSIFICATION_LABEL,
                area_km2=round(zone.area_km2, 3),
                mean_composite=round(zone.mean_composite, 2),
                max_composite=round(zone.max_composite, 2),
                dominant_hazard=zone.dominant_hazard,
                hazard_mix={
                    hazard.value: round(share, 3)
                    for hazard, share in zone.hazard_mix.items()
                },
                mean_confidence=round(zone.mean_confidence, 3),
                cell_count=zone.cell_count,
                population_intersected=zone.population_intersected,
                habitation_ids=zone.habitation_ids,
                rule_version=zone.rule_version,
            ),
        )
        for zone in zones
    ]
    return ZonesResponse(
        features=features,
        summary={key: ZoneClassSummary(**value) for key, value in run.summary().items()},
        classification_label=CLASSIFICATION_LABEL,
        decision_authority=DECISION_AUTHORITY,
        model_config_version=run.result.config_version,
        engine_version=run.result.engine_version,
        computed_ms=round(run.computed_ms, 1),
    )


@router.get("/zones", response_model=ZonesResponse)
def risk_zones(
    zone_class: str | None = Query(
        default=None, description="Filter to one class: CRITICAL, ELEVATED or WATCH."
    ),
) -> ZonesResponse:
    """The analytical red zones, each carrying the arithmetic behind it."""
    return serialise_zones(baseline_risk(), zone_class)


@router.get("/cell", response_model=RiskCellResponse)
def risk_cell(
    lon: float = Query(description="Longitude, WGS84."),
    lat: float = Query(description="Latitude, WGS84."),
) -> RiskCellResponse:
    """Take one point on the map apart: every hazard, every factor, every weight."""
    run = baseline_risk()
    index = run.grid.index_of(lon, lat)
    if index is None:
        raise HTTPException(
            status_code=404,
            detail=f"({lon}, {lat}) falls outside the study area analysis grid",
        )
    row, col = index
    hazard = hazard_at_cell(run, row, col)
    zone = zone_of(run, lon, lat)
    return RiskCellResponse(
        lon=lon,
        lat=lat,
        row=row,
        col=col,
        cell_bbox=run.grid.cell_bounds(row, col).as_list(),
        hazard=hazard,
        confidence=confidence_at_cell(run, row, col),
        zone_id=zone.id if zone else None,
        zone_class=hazard.zone_class,
        formula=get_formula("hazard.hsi"),
        composite_formula=get_formula("hazard.composite"),
        model_config_version=run.result.config_version,
        engine_version=run.result.engine_version,
        computed_at=datetime.now(UTC),
    )


@router.get("/habitations", response_model=HabitationHazardResponse)
def risk_habitations() -> HabitationHazardResponse:
    """Hazard summarised over each habitation footprint, ranked by composite."""
    run = baseline_risk()
    rows: list[HabitationHazardRow] = []
    for habitation in run.context.habitations:
        try:
            hazard, mean_composite, max_composite = hazard_over_footprint(
                run, habitation.centroid.lon, habitation.centroid.lat, FOOTPRINT_RADIUS_M
            )
        except ValueError:
            continue
        index = run.grid.index_of(habitation.centroid.lon, habitation.centroid.lat)
        assert index is not None
        zone = zone_of(run, habitation.centroid.lon, habitation.centroid.lat)
        rows.append(
            HabitationHazardRow(
                habitation_id=habitation.id,
                name=habitation.name,
                population=habitation.population,
                households=habitation.households,
                centroid=habitation.centroid,
                hazard=hazard,
                footprint_mean_composite=round(mean_composite, 2),
                footprint_max_composite=round(max_composite, 2),
                footprint_radius_m=FOOTPRINT_RADIUS_M,
                confidence=confidence_at_cell(run, *index),
                zone_id=zone.id if zone else None,
            )
        )
    rows.sort(key=lambda row: row.hazard.composite, reverse=True)
    return HabitationHazardResponse(
        habitations=rows,
        classification_label=CLASSIFICATION_LABEL,
        decision_authority=DECISION_AUTHORITY,
        scenario_disclaimer=SCENARIO_DISCLAIMER,
        note=(
            "This is hazard only. Exposure, vulnerability and relocation priority are "
            "computed separately and reported separately. "
            + PRIORITY_NOT_PROBABILITY
        ),
    )


@router.get("/overlay/composite.png", response_class=FileResponse)
def composite_overlay() -> FileResponse:
    """The colour-mapped composite surface, aligned to the study bounding box."""
    path = get_settings().derived_dir / "hazard_composite.png"
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail="hazard overlay is not built; run scripts/build_hazard.py",
        )
    return FileResponse(path, media_type="image/png")
