"""Engine 4: capacity is the scarcest service, and a gate failure has a name.

The unit tests build sites by hand so every capacity figure can be checked
against arithmetic; the last block runs the engine over the real corridor.
"""

from __future__ import annotations

import numpy as np
import pytest

from astra.domain.enums import ProvenanceClass, ServiceType, SuitabilityGate, ZoneClass
from astra.domain.model_config import MODEL_CONFIG
from astra.domain.models import CandidateSite, GeoPoint, ServiceSupply
from astra.engines.capacity import CapacityEngine, _contiguous_patch, marginal_sentence

ENGINE = CapacityEngine()
CAPACITY = MODEL_CONFIG.capacity


def _site(
    *,
    water_litres: float = 30_000.0,
    latrines: float = 40.0,
    facilities: float = 0.2,
    kva: float = 200.0,
    shelter_units: float = 800.0,
    area_m2: float = 60_000.0,
    road_distance_m: float = 100.0,
) -> CandidateSite:
    return CandidateSite(
        id="S-01",
        name="Test Bench",
        centroid=GeoPoint(lon=79.5, lat=30.5),
        gross_area_m2=area_m2,
        mean_slope_deg=6.0,
        elevation_m=1500.0,
        distance_to_road_m=road_distance_m,
        existing_shelter_units=0,
        constructable_units=0,
        services=[
            ServiceSupply(
                service=ServiceType.WATER,
                supply=water_litres,
                unit="L/day",
                provenance=ProvenanceClass.SYNTHETIC_CALIBRATED,
            ),
            ServiceSupply(
                service=ServiceType.SANITATION,
                supply=latrines,
                unit="latrine units",
                provenance=ProvenanceClass.SYNTHETIC_CALIBRATED,
            ),
            ServiceSupply(
                service=ServiceType.HEALTHCARE,
                supply=facilities,
                unit="facility units",
                provenance=ProvenanceClass.SYNTHETIC_CALIBRATED,
            ),
            ServiceSupply(
                service=ServiceType.POWER,
                supply=kva,
                unit="kVA",
                provenance=ProvenanceClass.SYNTHETIC_CALIBRATED,
            ),
            ServiceSupply(
                service=ServiceType.SHELTER,
                supply=shelter_units,
                unit="shelter units",
                provenance=ProvenanceClass.SYNTHETIC_CALIBRATED,
            ),
        ],
        district="Chamoli",
        state="Uttarakhand",
    )


# ---------------------------------------------------------------------------
# Per-service capacity
# ---------------------------------------------------------------------------


def test_each_service_capacity_is_supply_divided_by_its_norm() -> None:
    services = ENGINE.service_capacities(_site(), usable_area_m2=45_000.0)
    by_service = {entry.service: entry for entry in services}

    assert by_service[ServiceType.LAND].capacity_persons == pytest.approx(
        45_000.0 / CAPACITY.site_area_m2_per_person.value, abs=0.1
    )
    assert by_service[ServiceType.WATER].capacity_persons == pytest.approx(
        30_000.0 / CAPACITY.water_litres_per_person_day.value, abs=0.1
    )
    assert by_service[ServiceType.SANITATION].capacity_persons == pytest.approx(
        40.0 * CAPACITY.persons_per_latrine.value, abs=0.1
    )
    assert by_service[ServiceType.HEALTHCARE].capacity_persons == pytest.approx(
        0.2 * CAPACITY.persons_per_health_facility.value, abs=0.1
    )


def test_every_service_capacity_carries_its_norm_and_citation() -> None:
    for entry in ENGINE.service_capacities(_site(), usable_area_m2=45_000.0):
        assert entry.norm_value > 0
        assert entry.supply_unit
        if entry.norm_provenance is not ProvenanceClass.DEMO_CONFIG:
            assert entry.norm_citation, f"{entry.service} cites no standard"


def test_effective_capacity_is_the_minimum_and_names_the_binding_service() -> None:
    # Water deliberately scarce: 9,000 L/day supports 600 people at 15 L each.
    services = ENGINE.service_capacities(
        _site(water_litres=9_000.0), usable_area_m2=200_000.0
    )
    effective, bottleneck = ENGINE.effective(services)
    assert bottleneck is ServiceType.WATER
    assert effective == pytest.approx(600.0, abs=0.1)
    assert effective == min(entry.capacity_persons for entry in services)


def test_theoretical_capacity_is_land_alone() -> None:
    services = ENGINE.service_capacities(_site(water_litres=1.0), usable_area_m2=90_000.0)
    theoretical = ENGINE.theoretical(services)
    effective, _ = ENGINE.effective(services)
    assert theoretical == pytest.approx(
        90_000.0 / CAPACITY.site_area_m2_per_person.value, abs=0.1
    )
    assert effective < theoretical, "a scarce service must reduce what the land offers"


def test_a_tie_between_services_still_picks_one_and_reports_it() -> None:
    """Two services binding at the same figure must not produce a null bottleneck."""
    site = _site(water_litres=15.0 * 500, latrines=500 / 20, shelter_units=100.0)
    services = ENGINE.service_capacities(site, usable_area_m2=500 * 45.0)
    effective, bottleneck = ENGINE.effective(services)
    assert effective == pytest.approx(500.0, abs=0.5)
    assert bottleneck is not None


# ---------------------------------------------------------------------------
# Marginal interventions
# ---------------------------------------------------------------------------


def test_relieving_the_bottleneck_raises_capacity_and_names_the_next_one() -> None:
    site = _site(water_litres=9_000.0)  # water binds at 600
    services = ENGINE.service_capacities(site, usable_area_m2=200_000.0)
    interventions = ENGINE.interventions(site, services)
    best = interventions[0]

    assert best.service is ServiceType.WATER
    assert best.capacity_gain > 0
    assert best.capacity_after > best.capacity_before
    assert best.next_bottleneck is not None
    assert best.next_bottleneck_capacity == pytest.approx(best.capacity_after, abs=0.5)


def test_investing_in_a_service_that_does_not_bind_unlocks_nothing() -> None:
    site = _site(water_litres=9_000.0)
    services = ENGINE.service_capacities(site, usable_area_m2=200_000.0)
    interventions = {i.service: i for i in ENGINE.interventions(site, services)}
    assert interventions[ServiceType.POWER].capacity_gain == pytest.approx(0.0, abs=0.5)
    assert not interventions[ServiceType.POWER].unlocks


def test_interventions_are_ranked_by_what_they_unlock() -> None:
    site = _site(water_litres=9_000.0)
    services = ENGINE.service_capacities(site, usable_area_m2=200_000.0)
    gains = [i.capacity_gain for i in ENGINE.interventions(site, services)]
    assert gains == sorted(gains, reverse=True)


def test_the_marginal_sentence_is_assembled_from_computed_values() -> None:
    from astra.engines.capacity import SiteCapacity, UsableArea

    site = _site(water_litres=9_000.0)
    services = ENGINE.service_capacities(site, usable_area_m2=200_000.0)
    effective, bottleneck = ENGINE.effective(services)
    entry = SiteCapacity(
        site=site,
        gates=[],
        usable_area=UsableArea(
            usable_m2=200_000.0,
            measured_m2=400_000.0,
            buildable_fraction=0.5,
            slope_pass_fraction=0.8,
            flood_pass_fraction=0.9,
            radius_m=650.0,
            method="test",
        ),
        services=services,
        theoretical_capacity=ENGINE.theoretical(services),
        effective_capacity=effective,
        bottleneck=bottleneck,
        interventions=ENGINE.interventions(site, services),
    )
    sentence = marginal_sentence(entry)
    assert sentence is not None
    assert "raises effective capacity" in sentence
    assert "next binding constraint" in sentence
    assert "600" in sentence


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def _gates(**overrides):
    defaults = dict(
        zone_class=ZoneClass.LOW,
        distance_to_hazard_m=900.0,
        mean_slope_deg=8.0,
        buildable_fraction=0.4,
        hand_m=60.0,
    )
    defaults.update(overrides)
    return ENGINE.gates(_site(), **defaults)


def test_a_site_clear_of_every_constraint_passes_all_gates() -> None:
    assert all(gate.passed for gate in _gates())


def test_a_site_inside_an_elevated_zone_fails_the_hazard_gate() -> None:
    gates = {gate.gate: gate for gate in _gates(zone_class=ZoneClass.ELEVATED)}
    assert not gates[SuitabilityGate.OUTSIDE_HAZARD_ZONES].passed


def test_a_site_too_close_to_an_unsafe_zone_fails_even_when_its_own_cell_is_low() -> None:
    buffer_m = CAPACITY.gate_hazard_buffer_m.value
    gates = {gate.gate: gate for gate in _gates(distance_to_hazard_m=buffer_m - 1)}
    failure = gates[SuitabilityGate.OUTSIDE_HAZARD_ZONES]
    assert not failure.passed
    assert failure.observed == pytest.approx(buffer_m - 1)
    assert failure.threshold == buffer_m


def test_every_gate_reports_its_observation_threshold_and_reason() -> None:
    for gate in _gates(zone_class=ZoneClass.CRITICAL, mean_slope_deg=40.0, hand_m=2.0):
        assert gate.detail, f"{gate.gate} gives no reason"
        assert gate.threshold is not None
        assert gate.observed is not None


def test_a_steep_site_fails_the_slope_gate_at_the_configured_limit() -> None:
    limit = CAPACITY.gate_max_slope_deg.value
    assert {g.gate: g for g in _gates(mean_slope_deg=limit)}[
        SuitabilityGate.SLOPE_BUILDABLE
    ].passed
    assert not {g.gate: g for g in _gates(mean_slope_deg=limit + 0.1)}[
        SuitabilityGate.SLOPE_BUILDABLE
    ].passed


def test_a_low_lying_site_fails_the_flood_gate() -> None:
    limit = CAPACITY.gate_min_hand_m.value
    assert {g.gate: g for g in _gates(hand_m=limit)}[
        SuitabilityGate.ABOVE_FLOOD_LEVEL
    ].passed
    assert not {g.gate: g for g in _gates(hand_m=limit - 0.1)}[
        SuitabilityGate.ABOVE_FLOOD_LEVEL
    ].passed


# ---------------------------------------------------------------------------
# Usable area
# ---------------------------------------------------------------------------


def test_usable_area_is_the_intersection_of_all_three_conditions() -> None:
    shape = (10, 10)
    buildable = np.ones(shape)
    slope = np.full(shape, 5.0)
    hand = np.full(shape, 50.0)
    slope[0:2, :] = 40.0  # too steep
    hand[:, 0:2] = 2.0  # too low

    area = ENGINE.usable_area(
        buildable=buildable,
        slope_deg=slope,
        hand_m=hand,
        cell_area_m2=900.0,
        radius_m=650.0,
    )
    # 10x10 minus 2 steep rows and 2 low columns, keeping the overlap once.
    expected_cells = 100 - (2 * 10) - (2 * 10) + (2 * 2)
    assert area.usable_m2 == pytest.approx(expected_cells * 900.0)
    assert area.measured_m2 == pytest.approx(100 * 900.0)


def test_usable_area_takes_only_the_patch_containing_the_site() -> None:
    """A site is one buildable area, not every scrap of gentle ground nearby."""
    shape = (11, 11)
    buildable = np.zeros(shape)
    buildable[4:7, 4:7] = 1.0  # the site itself, at the centre
    buildable[0:2, 0:2] = 1.0  # an unconnected patch in the corner

    area = ENGINE.usable_area(
        buildable=buildable,
        slope_deg=np.full(shape, 5.0),
        hand_m=np.full(shape, 50.0),
        cell_area_m2=900.0,
        radius_m=650.0,
    )
    assert area.usable_m2 == pytest.approx(9 * 900.0)


def test_contiguous_patch_handles_an_unusable_centre() -> None:
    usable = np.zeros((7, 7), dtype=bool)
    usable[0:3, 0:3] = True
    patch = _contiguous_patch(usable)
    assert patch.sum() == 9


def test_usable_area_reports_no_coverage_rather_than_zero_area() -> None:
    nan = np.full((4, 4), np.nan)
    area = ENGINE.usable_area(
        buildable=nan, slope_deg=nan, hand_m=nan, cell_area_m2=900.0, radius_m=650.0
    )
    assert area.usable_m2 == 0.0
    assert area.method == "no coverage"


def test_refinement_agreement_drives_the_usable_area_confidence() -> None:
    threshold = CAPACITY.landcover_agreement_high_confidence.value
    shape = (5, 5)
    kwargs = dict(
        buildable=np.ones(shape),
        slope_deg=np.full(shape, 5.0),
        hand_m=np.full(shape, 50.0),
        cell_area_m2=900.0,
        radius_m=650.0,
    )
    assert ENGINE.usable_area(**kwargs).confidence.value == "MEDIUM"
    assert (
        ENGINE.usable_area(**kwargs, refinement_agreement=threshold).confidence.value
        == "HIGH"
    )
    assert (
        ENGINE.usable_area(
            **kwargs, refinement_agreement=threshold - 0.01
        ).confidence.value
        == "MEDIUM"
    )


# ---------------------------------------------------------------------------
# Against the real corridor
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corridor():
    from astra.engines.capacity_service import compute_site_capacity

    return compute_site_capacity()


def test_every_candidate_site_is_assessed(corridor) -> None:
    assert len(corridor) == 6
    assert len({entry.id for entry in corridor}) == 6


def test_effective_never_exceeds_theoretical_capacity(corridor) -> None:
    for entry in corridor:
        assert entry.effective_capacity <= entry.theoretical_capacity + 1e-6


def test_the_binding_service_is_the_one_with_the_lowest_capacity(corridor) -> None:
    for entry in corridor:
        lowest = min(service.capacity_persons for service in entry.services)
        assert entry.effective_capacity == pytest.approx(lowest, abs=0.1)
        binding = next(
            service
            for service in entry.services
            if service.service is entry.bottleneck
        )
        assert binding.capacity_persons == pytest.approx(lowest, abs=0.1)


def test_some_sites_fail_a_gate_and_say_which(corridor) -> None:
    """Gates that never fire would prove nothing."""
    failing = [entry for entry in corridor if not entry.suitable]
    assert failing, "the corridor should exercise the suitability gates"
    for entry in failing:
        assert entry.failed_gates
        for gate in entry.failed_gates:
            assert gate.detail


def test_access_is_a_scored_constraint_once_the_route_engine_supplies_it() -> None:
    """The assessment the API serves takes its access figure from Engine 5."""
    from astra.engines.capacity_service import baseline_capacity

    for entry in baseline_capacity():
        assert not entry.pending_constraints
        access = [s for s in entry.services if s.service is ServiceType.ACCESS]
        assert len(access) == 1, f"{entry.id} should carry exactly one access row"
        assert access[0].capacity_persons > 0


def test_access_is_declared_pending_when_the_route_engine_cannot_run() -> None:
    """No road network means no access figure, not an unconstrained one."""
    from astra.engines.capacity_service import compute_site_capacity

    without_routes = compute_site_capacity(access=None)
    for entry in without_routes:
        assert entry.pending_constraints
        assert any("Engine 5" in note for note in entry.pending_constraints)
        assert all(
            service.service is not ServiceType.ACCESS for service in entry.services
        ), "access must never be scored without the engine that measures it"


def test_every_site_carries_the_tenure_limitation(corridor) -> None:
    for entry in corridor:
        assert "ownership" in entry.limitation


def test_capacity_assessment_is_deterministic(corridor) -> None:
    from astra.engines.capacity_service import compute_site_capacity

    again = compute_site_capacity()
    assert [(e.id, e.effective_capacity) for e in again] == [
        (e.id, e.effective_capacity) for e in corridor
    ]


def test_gate_failures_are_listed_below_the_sites_that_are_available(corridor) -> None:
    """A site that fails a hard gate must not head a list ordered by capacity.

    Its capacity figure is real but unusable: an SDMA cannot allocate anyone to
    it. Ordering has to say that before the number does.
    """
    suitability = [entry.suitable for entry in corridor]
    assert suitability == sorted(suitability, reverse=True)
    for group in (True, False):
        capacities = [
            entry.effective_capacity for entry in corridor if entry.suitable is group
        ]
        assert capacities == sorted(capacities, reverse=True)


def test_district_capacity_excludes_every_site_that_fails_a_gate(corridor) -> None:
    from astra.engines.capacity_service import total_effective_capacity

    expected = sum(entry.effective_capacity for entry in corridor if entry.suitable)
    assert total_effective_capacity(corridor) == pytest.approx(expected, abs=0.1)
    blocked = [entry for entry in corridor if not entry.suitable]
    assert blocked, "the corridor should exercise the gates"
    assert total_effective_capacity(corridor) < sum(
        entry.effective_capacity for entry in corridor
    )


def test_the_land_cover_refinement_runs_on_the_real_corridor(corridor) -> None:
    """The Random Forest second opinion is a real surface, not a claimed one."""
    from astra.engines.capacity_service import capacity_surfaces

    if capacity_surfaces().refinement is None:
        pytest.skip("refinement raster not built; usable area falls back to WorldCover")
    for entry in corridor:
        agreement = entry.usable_area.refinement_agreement
        assert agreement is not None
        assert 0.0 <= agreement <= 1.0
        assert "not a replacement" in (entry.usable_area.refinement_note or "")
