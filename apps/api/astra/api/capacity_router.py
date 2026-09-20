"""Capacity endpoints: what each site can actually absorb, and what would change it.

The number that matters here is not the size of a site but the number of people
its scarcest service can support - and the intervention that would raise it.
Every figure on this route is computed from the vendored surfaces and the cited
per-person norms.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from astra.api.schemas import (
    InterventionResponse,
    SiteCapacityListResponse,
    SiteCapacityResponse,
    UsableAreaResponse,
)
from astra.domain.model_config import MODEL_CONFIG
from astra.domain.notices import DECISION_AUTHORITY, SITE_TENURE_LIMITATION
from astra.engines.capacity import SiteCapacity, marginal_sentence
from astra.engines.capacity_service import (
    baseline_capacity,
    bottleneck_summary,
    total_effective_capacity,
)
from astra.engines.service import baseline_risk

router = APIRouter(prefix="/capacity", tags=["capacity"])


def _serialise(entry: SiteCapacity) -> SiteCapacityResponse:
    usable = entry.usable_area
    return SiteCapacityResponse(
        site_id=entry.site.id,
        name=entry.site.name,
        centroid=entry.site.centroid,
        elevation_m=entry.site.elevation_m,
        distance_to_road_m=entry.site.distance_to_road_m,
        recorded_parcel_area_m2=entry.site.gross_area_m2,
        suitable=entry.suitable,
        gates=entry.gates,
        failed_gates=[gate.gate.value for gate in entry.failed_gates],
        usable_area=UsableAreaResponse(
            usable_m2=round(usable.usable_m2, 1),
            usable_ha=round(usable.usable_m2 / 10_000, 2),
            measured_m2=round(usable.measured_m2, 1),
            radius_m=usable.radius_m,
            buildable_fraction=round(usable.buildable_fraction, 4),
            slope_pass_fraction=round(usable.slope_pass_fraction, 4),
            flood_pass_fraction=round(usable.flood_pass_fraction, 4),
            footprint_mean_slope_deg=round(usable.footprint_mean_slope_deg, 2),
            footprint_mean_hand_m=round(usable.footprint_mean_hand_m, 1),
            method=usable.method,
            confidence=usable.confidence,
            refinement_agreement=usable.refinement_agreement,
            refinement_note=usable.refinement_note,
        ),
        services=entry.services,
        theoretical_capacity=entry.theoretical_capacity,
        effective_capacity=entry.effective_capacity,
        bottleneck=entry.bottleneck,
        interventions=[
            InterventionResponse(
                service=intervention.service,
                description=intervention.description,
                unit_size=intervention.unit_size,
                unit=intervention.unit,
                capacity_before=intervention.capacity_before,
                capacity_after=intervention.capacity_after,
                capacity_gain=intervention.capacity_gain,
                next_bottleneck=intervention.next_bottleneck,
                next_bottleneck_capacity=intervention.next_bottleneck_capacity,
                unlocks=intervention.unlocks,
            )
            for intervention in entry.interventions
        ],
        marginal_headline=marginal_sentence(entry),
        pending_constraints=entry.pending_constraints,
        limitation=entry.limitation,
    )


@router.get("/sites", response_model=SiteCapacityListResponse)
def capacity_sites() -> SiteCapacityListResponse:
    """Carrying capacity for every candidate site, with the binding constraint named."""
    results = baseline_capacity()
    run = baseline_risk()
    population = sum(habitation.population for habitation in run.context.habitations)
    effective_total = total_effective_capacity(results)
    capacity = MODEL_CONFIG.capacity
    return SiteCapacityListResponse(
        sites=[_serialise(entry) for entry in results],
        suitable_sites=sum(1 for entry in results if entry.suitable),
        total_effective_capacity=effective_total,
        total_theoretical_capacity=round(
            sum(entry.theoretical_capacity for entry in results if entry.suitable), 1
        ),
        population_needing_relocation=population,
        unmet_demand=round(max(population - effective_total, 0.0), 1),
        bottleneck_counts=bottleneck_summary(results),
        norms=[
            capacity.site_area_m2_per_person,
            capacity.water_litres_per_person_day,
            capacity.persons_per_latrine,
            capacity.persons_per_health_facility,
            capacity.shelter_occupancy_persons_per_unit,
            capacity.power_kva_per_household,
            capacity.gate_max_slope_deg,
            capacity.gate_min_hand_m,
            capacity.gate_hazard_buffer_m,
            capacity.gate_max_road_distance_m,
        ],
        decision_authority=DECISION_AUTHORITY,
        limitation=SITE_TENURE_LIMITATION,
        model_config_version=MODEL_CONFIG.version,
        engine_version=MODEL_CONFIG.engine_version,
    )


@router.get("/sites/{site_id}", response_model=SiteCapacityResponse)
def capacity_site(site_id: str) -> SiteCapacityResponse:
    """One site's full capacity assessment."""
    match = next(
        (entry for entry in baseline_capacity() if entry.site.id == site_id), None
    )
    if match is None:
        raise HTTPException(status_code=404, detail=f"unknown site '{site_id}'")
    return _serialise(match)


@router.get("/sites/{site_id}/interventions", response_model=list[InterventionResponse])
def capacity_interventions(site_id: str) -> list[InterventionResponse]:
    """What one unit of each intervention would unlock at this site, ranked."""
    return capacity_site(site_id).interventions
