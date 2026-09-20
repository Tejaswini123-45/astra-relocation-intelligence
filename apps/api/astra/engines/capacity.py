"""Engine 4 - site suitability gates and multi-constraint carrying capacity.

A site's capacity is not how many people fit on the land. It is how many people
the scarcest service there can support:

    cap_s        = supply_s / norm_s        for each service s
    theoretical  = cap_land
    effective    = min_s(cap_s)
    bottleneck   = argmin_s(cap_s)

and the useful question that follows is not "how big is this site" but "what one
intervention raises the number of people it can take, and by how much". That is
what makes a capacity figure actionable rather than descriptive.

Two rules this module holds to:

* A gate failure is reported as a named gate with its observed value and its
  threshold, never folded into a low score. "Fails the flood gate at HAND 11 m
  against a 20 m threshold" is actionable; "suitability 0.42" is not.
* A service whose engine has not been built yet is reported as pending, not
  assumed. Access capacity comes from route throughput (Engine 5); until that
  exists it is listed as a constraint not yet applied, and the effective capacity
  says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from astra.domain.enums import (
    ConfidenceBand,
    ProvenanceClass,
    ServiceType,
    SuitabilityGate,
    ZoneClass,
)
from astra.domain.model_config import MODEL_CONFIG, AstraModelConfig
from astra.domain.models import CandidateSite, GateResult, ServiceCapacity
from astra.domain.notices import SITE_TENURE_LIMITATION

FORMULA_PER_SERVICE = "capacity.per_service"
FORMULA_EFFECTIVE = "capacity.effective"
FORMULA_MARGINAL = "capacity.marginal_intervention"

#: Kept for the case where the route engine cannot run - a missing OSM extract -
#: so the gap stays visible in the response instead of access silently becoming
#: unconstrained.
PENDING_ACCESS = (
    "Access throughput is set by route reliability and travel time (Engine 5). "
    "That engine could not run for this site, so access is not treated as a "
    "binding constraint here and the effective capacity below may be optimistic."
)


@dataclass(frozen=True)
class UsableArea:
    """Buildable ground measured on the real surfaces, with how it was measured."""

    usable_m2: float
    measured_m2: float
    buildable_fraction: float
    slope_pass_fraction: float
    flood_pass_fraction: float
    radius_m: float
    method: str
    footprint_mean_slope_deg: float = 0.0
    footprint_mean_hand_m: float = 0.0
    refinement_agreement: float | None = None
    refinement_note: str | None = None

    @property
    def confidence(self) -> ConfidenceBand:
        if self.refinement_agreement is None:
            return ConfidenceBand.MEDIUM
        threshold = MODEL_CONFIG.capacity.landcover_agreement_high_confidence.value
        return (
            ConfidenceBand.HIGH
            if self.refinement_agreement >= threshold
            else ConfidenceBand.MEDIUM
        )


@dataclass(frozen=True)
class Intervention:
    """One unit of investment, and what it actually unlocks."""

    service: ServiceType
    description: str
    unit_size: float
    unit: str
    capacity_before: float
    capacity_after: float
    capacity_gain: float
    next_bottleneck: ServiceType | None
    next_bottleneck_capacity: float | None

    @property
    def unlocks(self) -> bool:
        return self.capacity_gain > 0.5


@dataclass
class SiteCapacity:
    """Everything Engine 4 computes for one candidate site."""

    site: CandidateSite
    gates: list[GateResult]
    usable_area: UsableArea
    services: list[ServiceCapacity]
    theoretical_capacity: float
    effective_capacity: float
    bottleneck: ServiceType | None
    interventions: list[Intervention]
    pending_constraints: list[str] = field(default_factory=list)
    limitation: str = SITE_TENURE_LIMITATION

    @property
    def id(self) -> str:
        return self.site.id

    @property
    def suitable(self) -> bool:
        return all(gate.passed for gate in self.gates)

    @property
    def failed_gates(self) -> list[GateResult]:
        return [gate for gate in self.gates if not gate.passed]


class CapacityEngine:
    """Suitability gates, usable area, per-service capacity and interventions."""

    def __init__(self, config: AstraModelConfig | None = None) -> None:
        self.config = config or MODEL_CONFIG

    # -- step 1: hard gates --------------------------------------------------

    def gates(
        self,
        site: CandidateSite,
        *,
        zone_class: ZoneClass,
        distance_to_hazard_m: float,
        mean_slope_deg: float,
        buildable_fraction: float,
        hand_m: float,
    ) -> list[GateResult]:
        """Binary suitability gates. Each failure names itself."""
        capacity = self.config.capacity
        buffer_m = capacity.gate_hazard_buffer_m.value

        hazard_pass = zone_class in (ZoneClass.LOW, ZoneClass.WATCH) and (
            distance_to_hazard_m >= buffer_m
        )
        return [
            GateResult(
                gate=SuitabilityGate.OUTSIDE_HAZARD_ZONES,
                passed=hazard_pass,
                observed=round(distance_to_hazard_m, 1),
                threshold=buffer_m,
                unit="m",
                detail=(
                    f"Site sits in a {zone_class.value} cell, "
                    f"{distance_to_hazard_m:.0f} m from the nearest Critical or "
                    f"Elevated zone; the gate requires {buffer_m:.0f} m of clearance "
                    "and a site outside those classes."
                ),
            ),
            GateResult(
                gate=SuitabilityGate.SLOPE_BUILDABLE,
                passed=mean_slope_deg <= capacity.gate_max_slope_deg.value,
                observed=round(mean_slope_deg, 2),
                threshold=capacity.gate_max_slope_deg.value,
                unit="degrees",
                detail=(
                    f"Mean slope {mean_slope_deg:.1f} degrees against a build-safe "
                    f"limit of {capacity.gate_max_slope_deg.value:.0f}."
                ),
            ),
            GateResult(
                gate=SuitabilityGate.LANDCOVER_PERMITTED,
                passed=buildable_fraction > 0.0,
                observed=round(buildable_fraction, 3),
                threshold=0.0,
                unit="fraction of site",
                detail=(
                    f"{buildable_fraction * 100:.0f}% of the measured site area falls "
                    "in a land-cover class that permits construction. Tree cover, "
                    "water, wetland, snow and ice are excluded."
                ),
            ),
            GateResult(
                gate=SuitabilityGate.ABOVE_FLOOD_LEVEL,
                passed=hand_m >= capacity.gate_min_hand_m.value,
                observed=round(hand_m, 1),
                threshold=capacity.gate_min_hand_m.value,
                unit="m",
                detail=(
                    f"Site stands {hand_m:.0f} m above its nearest channel against a "
                    f"{capacity.gate_min_hand_m.value:.0f} m requirement. Height above "
                    "nearest drainage is a stand-in for a return-period flood level, "
                    "not a hydraulic model result."
                ),
            ),
            GateResult(
                gate=SuitabilityGate.ROAD_ACCESSIBLE,
                passed=site.distance_to_road_m <= capacity.gate_max_road_distance_m.value,
                observed=round(site.distance_to_road_m, 1),
                threshold=capacity.gate_max_road_distance_m.value,
                unit="m",
                detail=(
                    f"Nearest mapped road {site.distance_to_road_m:.0f} m away against "
                    f"a {capacity.gate_max_road_distance_m.value:.0f} m limit."
                ),
            ),
        ]

    # -- step 2: usable area -------------------------------------------------

    def usable_area(
        self,
        *,
        buildable: np.ndarray,
        slope_deg: np.ndarray,
        hand_m: np.ndarray,
        cell_area_m2: float,
        radius_m: float,
        refinement_agreement: float | None = None,
        refinement_note: str | None = None,
    ) -> UsableArea:
        """Buildable ground: land cover permits it, slope allows it, flood spares it.

        The site is the contiguous patch of such ground around its centre, not the
        whole search window. Averaging slope over a window of Himalayan hillside
        would fail every candidate for the steepness of the mountain behind it,
        which would be arithmetically correct and completely useless.
        """
        capacity = self.config.capacity
        valid = np.isfinite(slope_deg) & np.isfinite(hand_m) & np.isfinite(buildable)
        if not valid.any():
            return UsableArea(
                usable_m2=0.0,
                measured_m2=0.0,
                buildable_fraction=0.0,
                slope_pass_fraction=0.0,
                flood_pass_fraction=0.0,
                radius_m=radius_m,
                method="no coverage",
            )

        is_buildable = buildable > 0.5
        slope_ok = slope_deg <= capacity.gate_max_slope_deg.value
        flood_ok = hand_m >= capacity.gate_min_hand_m.value
        usable = valid & is_buildable & slope_ok & flood_ok
        footprint = _contiguous_patch(usable)

        total_cells = int(valid.sum())
        footprint_cells = int(footprint.sum())
        return UsableArea(
            usable_m2=float(footprint_cells * cell_area_m2),
            measured_m2=float(total_cells * cell_area_m2),
            buildable_fraction=float(is_buildable[valid].mean()),
            slope_pass_fraction=float(slope_ok[valid].mean()),
            flood_pass_fraction=float(flood_ok[valid].mean()),
            radius_m=radius_m,
            footprint_mean_slope_deg=(
                float(np.nanmean(slope_deg[footprint])) if footprint_cells else 90.0
            ),
            footprint_mean_hand_m=(
                float(np.nanmean(hand_m[footprint])) if footprint_cells else 0.0
            ),
            method=(
                "ESA WorldCover buildable classes intersected with the slope limit and "
                "the height-above-drainage requirement, then reduced to the contiguous "
                "patch containing the site centre"
            ),
            refinement_agreement=refinement_agreement,
            refinement_note=refinement_note,
        )

    # -- step 3: per-service capacity ---------------------------------------

    def service_capacities(
        self,
        site: CandidateSite,
        usable_area_m2: float,
        access_capacity: float | None = None,
    ) -> list[ServiceCapacity]:
        """How many people each service at this site can support.

        ``access_capacity`` is the number of people the roads reaching this site
        can actually deliver, from Engine 5. Passed as ``None`` when the route
        engine has not run, in which case access is reported as pending rather
        than assumed unconstrained.
        """
        capacity = self.config.capacity
        results: list[ServiceCapacity] = []

        land_norm = capacity.site_area_m2_per_person
        results.append(
            ServiceCapacity(
                service=ServiceType.LAND,
                supply=round(usable_area_m2, 1),
                supply_unit="m2",
                norm_value=land_norm.value,
                norm_unit=land_norm.unit or "",
                norm_provenance=land_norm.provenance,
                norm_citation=land_norm.citation,
                capacity_persons=round(usable_area_m2 / land_norm.value, 1),
            )
        )

        shelter_supply = site.supply_for(ServiceType.SHELTER)
        shelter_units = (
            shelter_supply.supply if shelter_supply else float(site.existing_shelter_units)
        ) + float(site.constructable_units)
        shelter_norm = capacity.shelter_occupancy_persons_per_unit
        results.append(
            ServiceCapacity(
                service=ServiceType.SHELTER,
                supply=round(shelter_units, 1),
                supply_unit="units",
                norm_value=shelter_norm.value,
                norm_unit=shelter_norm.unit or "",
                norm_provenance=shelter_norm.provenance,
                norm_citation=shelter_norm.citation,
                capacity_persons=round(shelter_units * shelter_norm.value, 1),
            )
        )

        for service, norm in (
            (ServiceType.WATER, capacity.water_litres_per_person_day),
            (ServiceType.SANITATION, capacity.persons_per_latrine),
            (ServiceType.HEALTHCARE, capacity.persons_per_health_facility),
            (ServiceType.POWER, capacity.power_kva_per_household),
        ):
            supply = site.supply_for(service)
            if supply is None:
                continue
            if service is ServiceType.WATER:
                persons = supply.supply / norm.value
            elif service is ServiceType.SANITATION:
                persons = supply.supply * norm.value
            elif service is ServiceType.HEALTHCARE:
                persons = supply.supply * norm.value
            else:
                households = supply.supply / norm.value
                persons = households * capacity.persons_per_household.value
            results.append(
                ServiceCapacity(
                    service=service,
                    supply=round(supply.supply, 2),
                    supply_unit=supply.unit,
                    norm_value=norm.value,
                    norm_unit=norm.unit or "",
                    norm_provenance=norm.provenance,
                    norm_citation=norm.citation,
                    capacity_persons=round(max(persons, 0.0), 1),
                )
            )

        if access_capacity is not None:
            access_norm = capacity.access_persons_per_route_day
            results.append(
                ServiceCapacity(
                    service=ServiceType.ACCESS,
                    supply=round(access_capacity / access_norm.value, 2),
                    supply_unit="usable approach routes",
                    norm_value=access_norm.value,
                    norm_unit=access_norm.unit or "",
                    norm_provenance=access_norm.provenance,
                    norm_citation=access_norm.citation,
                    capacity_persons=round(max(access_capacity, 0.0), 1),
                )
            )
        return results

    @staticmethod
    def effective(services: list[ServiceCapacity]) -> tuple[float, ServiceType | None]:
        """Effective capacity and the service that binds it."""
        if not services:
            return 0.0, None
        binding = min(services, key=lambda entry: entry.capacity_persons)
        return binding.capacity_persons, binding.service

    @staticmethod
    def theoretical(services: list[ServiceCapacity]) -> float:
        """What the land alone would hold, before any service is considered."""
        for entry in services:
            if entry.service is ServiceType.LAND:
                return entry.capacity_persons
        return 0.0

    # -- step 4: marginal intervention analysis ------------------------------

    def interventions(
        self, site: CandidateSite, services: list[ServiceCapacity]
    ) -> list[Intervention]:
        """What one unit of each intervention unlocks, ranked by capacity gained."""
        capacity = self.config.capacity
        before, _ = self.effective(services)

        units: dict[ServiceType, tuple[float, str, str]] = {
            ServiceType.WATER: (
                capacity.intervention_water_litres_day.value,
                "L/day",
                "borewell with storage",
            ),
            ServiceType.SANITATION: (
                capacity.intervention_sanitation_units.value,
                "latrine units",
                "sanitation block",
            ),
            ServiceType.SHELTER: (
                capacity.intervention_shelter_units.value,
                "units",
                "shelter construction batch",
            ),
            ServiceType.HEALTHCARE: (
                capacity.intervention_healthcare_units.value,
                "facility units",
                "health sub-centre",
            ),
            ServiceType.POWER: (
                capacity.intervention_power_kva.value,
                "kVA",
                "distribution transformer",
            ),
            ServiceType.LAND: (
                capacity.intervention_land_m2.value,
                "m2",
                "one hectare of terracing or acquisition",
            ),
        }

        results: list[Intervention] = []
        for service, (unit_size, unit, description) in units.items():
            current = next((s for s in services if s.service is service), None)
            if current is None:
                continue
            # Access can bind capacity but is not on this list: unlocking it means
            # building or reopening a road, which is a different kind of decision
            # on a different timescale, and pricing it as one more service unit
            # here would be dishonest about what it takes.
            improved = self._with_added_supply(services, service, unit_size)
            after, next_binding = self.effective(improved)
            next_capacity = (
                next((s.capacity_persons for s in improved if s.service is next_binding), None)
                if next_binding
                else None
            )
            results.append(
                Intervention(
                    service=service,
                    description=description,
                    unit_size=unit_size,
                    unit=unit,
                    capacity_before=round(before, 1),
                    capacity_after=round(after, 1),
                    capacity_gain=round(after - before, 1),
                    next_bottleneck=next_binding,
                    next_bottleneck_capacity=(
                        round(next_capacity, 1) if next_capacity is not None else None
                    ),
                )
            )

        results.sort(key=lambda entry: entry.capacity_gain, reverse=True)
        return results

    def _with_added_supply(
        self, services: list[ServiceCapacity], service: ServiceType, added: float
    ) -> list[ServiceCapacity]:
        """Recompute the service table with one service's supply increased."""
        capacity = self.config.capacity
        updated: list[ServiceCapacity] = []
        for entry in services:
            if entry.service is not service:
                updated.append(entry)
                continue
            supply = entry.supply + added
            if service is ServiceType.LAND:
                persons = supply / capacity.site_area_m2_per_person.value
            elif service is ServiceType.SHELTER:
                persons = supply * capacity.shelter_occupancy_persons_per_unit.value
            elif service is ServiceType.WATER:
                persons = supply / capacity.water_litres_per_person_day.value
            elif service is ServiceType.SANITATION:
                persons = supply * capacity.persons_per_latrine.value
            elif service is ServiceType.HEALTHCARE:
                persons = supply * capacity.persons_per_health_facility.value
            elif service is ServiceType.ACCESS:
                persons = supply * capacity.access_persons_per_route_day.value
            else:
                persons = (
                    supply
                    / capacity.power_kva_per_household.value
                    * capacity.persons_per_household.value
                )
            updated.append(
                ServiceCapacity(
                    service=entry.service,
                    supply=round(supply, 2),
                    supply_unit=entry.supply_unit,
                    norm_value=entry.norm_value,
                    norm_unit=entry.norm_unit,
                    norm_provenance=entry.norm_provenance,
                    norm_citation=entry.norm_citation,
                    capacity_persons=round(max(persons, 0.0), 1),
                )
            )
        return updated


def _contiguous_patch(usable: np.ndarray) -> np.ndarray:
    """The connected patch of usable cells containing the window centre.

    A site is one buildable area, not the sum of every scrap of gentle ground
    within half a kilometre of it.
    """
    from scipy import ndimage

    if not usable.any():
        return usable
    labelled, count = ndimage.label(usable, structure=np.ones((3, 3), dtype=int))
    if count == 0:
        return usable
    centre = (usable.shape[0] // 2, usable.shape[1] // 2)
    label = int(labelled[centre])
    if label == 0:
        # The centre cell itself is unusable. Take the largest patch in the window
        # instead of claiming none exists, and let the measured area say how small
        # it is.
        sizes = np.bincount(labelled.ravel())
        sizes[0] = 0
        label = int(sizes.argmax())
        if sizes[label] == 0:
            return np.zeros_like(usable)
    return labelled == label


def marginal_sentence(capacity: SiteCapacity) -> str | None:
    """The one line an official can act on, assembled from computed values only."""
    if not capacity.interventions:
        return None
    best = capacity.interventions[0]
    if not best.unlocks:
        return None
    sentence = (
        f"+{best.unit_size:g} {best.unit} of {best.service.value.lower()} "
        f"({best.description}) raises effective capacity "
        f"{best.capacity_before:.0f} to {best.capacity_after:.0f} "
        f"(+{best.capacity_gain:.0f} people)."
    )
    if best.next_bottleneck and best.next_bottleneck_capacity is not None:
        sentence += (
            f" The next binding constraint becomes "
            f"{best.next_bottleneck.value.lower()} at "
            f"{best.next_bottleneck_capacity:.0f}."
        )
    return sentence


def provenance_of_supply(site: CandidateSite, service: ServiceType) -> ProvenanceClass:
    supply = site.supply_for(service)
    return supply.provenance if supply else ProvenanceClass.SYNTHETIC_CALIBRATED
