"""The config module is the single source of numeric truth. These tests hold it to that."""

from __future__ import annotations

import math

import pytest

from astra.domain.enums import HazardType, ProvenanceClass, ServiceType, ZoneClass
from astra.domain.model_config import MODEL_CONFIG, Constant, WeightSet


def test_every_constant_key_is_unique() -> None:
    keys = [c.key for c in MODEL_CONFIG.constants()]
    assert len(keys) == len(set(keys)), "duplicate constant keys break one-hop tracing"


def test_every_constant_is_self_describing() -> None:
    for constant in MODEL_CONFIG.constants():
        assert constant.description.strip(), f"{constant.key} has no description"
        assert math.isfinite(constant.value), f"{constant.key} is not finite"


def test_non_demo_constants_carry_a_citation() -> None:
    """A number presented as a standard must name the standard."""
    for constant in MODEL_CONFIG.constants():
        if constant.provenance is not ProvenanceClass.DEMO_CONFIG:
            assert constant.citation, f"{constant.key} claims a standard without citing it"


def test_published_norms_match_their_standards() -> None:
    """Sphere minima, pinned. A silent edit here would change every capacity figure."""
    capacity = MODEL_CONFIG.capacity
    assert capacity.water_litres_per_person_day.value == 15.0
    assert capacity.persons_per_latrine.value == 20.0
    assert capacity.covered_area_m2_per_person.value == 3.5
    assert capacity.site_area_m2_per_person.value == 45.0
    for constant in (
        capacity.water_litres_per_person_day,
        capacity.persons_per_latrine,
        capacity.covered_area_m2_per_person,
        capacity.site_area_m2_per_person,
    ):
        assert "Sphere" in (constant.citation or "")


@pytest.mark.parametrize("hazard", list(HazardType))
def test_hazard_factor_weights_sum_to_one(hazard: HazardType) -> None:
    weights = MODEL_CONFIG.hazard.weights_for(hazard)
    assert abs(sum(c.value for c in weights.weights.values()) - 1.0) < 1e-9


def test_weight_set_rejects_weights_that_do_not_sum_to_one() -> None:
    with pytest.raises(ValueError, match="must be 1.0"):
        WeightSet(
            hazard=HazardType.FLOOD,
            weights={
                "a": Constant(
                    key="a",
                    value=0.5,
                    provenance=ProvenanceClass.DEMO_CONFIG,
                    description="half",
                ),
                "b": Constant(
                    key="b",
                    value=0.2,
                    provenance=ProvenanceClass.DEMO_CONFIG,
                    description="a fifth",
                ),
            },
        )


def test_constant_cannot_claim_a_standard_without_citing_it() -> None:
    with pytest.raises(ValueError, match="carries no citation"):
        Constant(
            key="fake.norm",
            value=1.0,
            provenance=ProvenanceClass.REAL_OPEN,
            description="a number pretending to be a published standard",
        )


def test_priority_and_vulnerability_weights_sum_to_one() -> None:
    priority = MODEL_CONFIG.priority
    assert (
        abs(
            priority.w_hazard.value
            + priority.w_exposure.value
            + priority.w_vulnerability.value
            + priority.w_history.value
            - 1.0
        )
        < 1e-9
    )
    assert (
        abs(
            priority.vuln_w_elderly.value
            + priority.vuln_w_children_u5.value
            + priority.vuln_w_disability.value
            + priority.vuln_w_medical_dependency.value
            + priority.vuln_w_low_income.value
            + priority.vuln_w_kutcha_share.value
            - 1.0
        )
        < 1e-9
    )


@pytest.mark.parametrize(
    ("composite", "expected"),
    [
        (100.0, ZoneClass.CRITICAL),
        (78.0, ZoneClass.CRITICAL),
        (77.99, ZoneClass.ELEVATED),
        (62.0, ZoneClass.ELEVATED),
        (61.99, ZoneClass.WATCH),
        (52.0, ZoneClass.WATCH),
        (51.99, ZoneClass.LOW),
        (0.0, ZoneClass.LOW),
    ],
)
def test_zone_thresholds_are_exact_at_the_boundary(composite: float, expected: ZoneClass) -> None:
    assert MODEL_CONFIG.hazard.zone_class_for(composite) is expected


def test_zone_thresholds_are_strictly_ordered() -> None:
    hazard = MODEL_CONFIG.hazard
    assert (
        hazard.zone_threshold_critical.value
        > hazard.zone_threshold_elevated.value
        > hazard.zone_threshold_watch.value
        > 0
    )


def test_every_service_except_access_has_a_demand_norm() -> None:
    for service in ServiceType:
        norm = MODEL_CONFIG.capacity.norm_for(service)
        if service is ServiceType.ACCESS:
            assert norm is None, "access capacity comes from route throughput, not a norm"
        else:
            assert norm is not None and norm.value > 0


def test_config_is_immutable_at_runtime() -> None:
    """Nothing may mutate a weight mid-run; a scenario must produce a new config."""
    with pytest.raises(ValueError):
        MODEL_CONFIG.hazard.composite_lambda.value = 0.9  # type: ignore[misc]
