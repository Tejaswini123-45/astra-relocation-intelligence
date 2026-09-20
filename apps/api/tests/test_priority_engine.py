"""Engines 2 and 3: consequence separated from hazard, and phasing that explains itself.

The tests here build habitations by hand so that each property can be checked
against arithmetic a reader can do on paper.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from astra.domain.enums import PhaseTier, ProvenanceClass, StructureType, ZoneClass
from astra.domain.model_config import MODEL_CONFIG
from astra.domain.models import CriticalFacility, DemographicProfile, GeoPoint, Habitation
from astra.engines.context import IncidentRecord
from astra.engines.priority import PriorityEngine, _weak_structure_share

ENGINE = PriorityEngine()


def _habitation(
    *,
    hid: str = "H-01",
    population: int = 500,
    households: int = 100,
    elderly: int = 50,
    children: int = 40,
    disability: int = 15,
    medical: int = 10,
    low_income: int = 40,
    kutcha: float = 0.3,
    semi_pucca: float = 0.4,
    facilities: int = 2,
    lon: float = 79.5,
    lat: float = 30.5,
) -> Habitation:
    return Habitation(
        id=hid,
        name=f"Test {hid}",
        centroid=GeoPoint(lon=lon, lat=lat),
        population=population,
        households=households,
        demographics=DemographicProfile(
            elderly_60_plus=elderly,
            children_under_5=children,
            persons_with_disability=disability,
            medically_dependent=medical,
            low_income_households=low_income,
        ),
        structure_mix={
            StructureType.KUTCHA: kutcha,
            StructureType.SEMI_PUCCA: semi_pucca,
            StructureType.PUCCA: round(1.0 - kutcha - semi_pucca, 6),
        },
        critical_facilities=[
            CriticalFacility(
                id=f"{hid}-F{index}",
                kind="SCHOOL",
                name="school",
                location=GeoPoint(lon=lon, lat=lat),
            )
            for index in range(facilities)
        ],
        district="Chamoli",
        state="Uttarakhand",
        provenance=ProvenanceClass.SYNTHETIC_CALIBRATED,
    )


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


def test_hazard_component_blends_footprint_mean_and_maximum() -> None:
    priority = MODEL_CONFIG.priority
    component = ENGINE.hazard_component(mean_composite=60.0, max_composite=100.0)
    expected = (
        priority.footprint_w_mean.value * 0.60 + priority.footprint_w_max.value * 1.00
    )
    assert component.value == pytest.approx(expected, abs=1e-9)
    assert {factor.factor for factor in component.factors} == {
        "footprint_mean_composite",
        "footprint_max_composite",
    }


def test_hazard_component_is_not_just_the_maximum() -> None:
    """Taking the maximum alone would pin every settlement in this terrain to 1.0."""
    peak_only = ENGINE.hazard_component(mean_composite=20.0, max_composite=100.0)
    assert peak_only.value < 1.0


def test_exposure_grows_with_population_and_facilities() -> None:
    small = ENGINE.exposure(
        _habitation(
            population=150, households=30, elderly=15, children=12, disability=4,
            medical=3, low_income=12, facilities=0,
        )
    )
    large = ENGINE.exposure(
        _habitation(
            population=1200, households=240, elderly=120, children=96, disability=34,
            medical=25, low_income=96, facilities=3,
        )
    )
    assert large.value > small.value
    assert 0.0 <= small.value <= 1.0 and 0.0 <= large.value <= 1.0


def test_exposure_uses_fixed_references_not_the_cohort() -> None:
    """A habitation's exposure must not change when another one is added."""
    habitation = _habitation(population=750, households=150, facilities=2)
    first = ENGINE.exposure(habitation)
    second = ENGINE.exposure(habitation)
    assert first.value == second.value
    priority = MODEL_CONFIG.priority
    expected = (
        priority.exposure_w_population.value
        * (750 / priority.exposure_reference_population.value)
        + priority.exposure_w_households.value
        * (150 / priority.exposure_reference_households.value)
        + priority.exposure_w_facilities.value
        * (2 / priority.exposure_reference_facilities.value)
    )
    assert first.value == pytest.approx(expected, abs=1e-6)


def test_exposure_saturates_at_the_reference_size() -> None:
    huge = ENGINE.exposure(
        _habitation(population=100_000, households=20_000, facilities=40)
    )
    assert huge.value == pytest.approx(1.0, abs=1e-6)


def test_vulnerability_is_scaled_against_the_reference_profile() -> None:
    """Without scaling, a weighted average of small shares lands near 0.2 always."""
    reference = ENGINE._vulnerability_reference()
    assert 0.2 < reference < 0.6

    acute = _habitation(
        population=1000,
        households=200,
        elderly=250,
        children=150,
        disability=60,
        medical=50,
        low_income=140,
        kutcha=1.0,
        semi_pucca=0.0,
    )
    assert ENGINE.vulnerability(acute).value == pytest.approx(1.0, abs=0.02)


def test_vulnerability_of_a_resilient_settlement_is_low() -> None:
    resilient = _habitation(
        population=1000,
        households=200,
        elderly=40,
        children=30,
        disability=5,
        medical=3,
        low_income=10,
        kutcha=0.0,
        semi_pucca=0.05,
    )
    assert ENGINE.vulnerability(resilient).value < 0.25


def test_weak_structure_share_counts_kutcha_and_semi_pucca() -> None:
    habitation = _habitation(kutcha=0.25, semi_pucca=0.35)
    assert _weak_structure_share(habitation) == pytest.approx(0.60, abs=1e-9)


def test_history_decays_with_age_and_ignores_distant_events() -> None:
    habitation = _habitation()
    tau_years = MODEL_CONFIG.priority.history_tau_days.value / 365.25

    recent = IncidentRecord(
        lon=79.5, lat=30.5, weight=1.0, age_years=0.0, size="medium",
        category="landslide", trigger="rain", date="2026-01-01", fatalities=0,
    )
    old = IncidentRecord(
        lon=79.5, lat=30.5, weight=1.0, age_years=tau_years * 3, size="medium",
        category="landslide", trigger="rain", date="2000-01-01", fatalities=0,
    )
    far = IncidentRecord(
        lon=79.72, lat=30.63, weight=5.0, age_years=0.0, size="very_large",
        category="landslide", trigger="rain", date="2026-01-01", fatalities=0,
    )

    assert ENGINE.history(habitation, [recent]).value > ENGINE.history(
        habitation, [old]
    ).value
    assert ENGINE.history(habitation, [far]).value == 0.0
    assert ENGINE.history(habitation, []).value == 0.0


def test_history_note_states_it_is_not_a_prediction() -> None:
    component = ENGINE.history(_habitation(), [])
    assert component.note and "not treated as proof of future hazard" in component.note


# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------


def test_priority_is_the_weighted_sum_of_its_four_components() -> None:
    hazard = ENGINE.hazard_component(70.0, 90.0)
    habitation = _habitation()
    exposure = ENGINE.exposure(habitation)
    vulnerability = ENGINE.vulnerability(habitation)
    history = ENGINE.history(habitation, [])

    score, factors = ENGINE.priority(hazard, exposure, vulnerability, history)
    priority = MODEL_CONFIG.priority
    expected = 100.0 * (
        priority.w_hazard.value * hazard.value
        + priority.w_exposure.value * exposure.value
        + priority.w_vulnerability.value * vulnerability.value
        + priority.w_history.value * history.value
    )
    # The published contributions are rounded to six decimals, so the score can
    # differ from the unrounded arithmetic in the seventh. What must hold exactly
    # is that the published score equals the sum of the published contributions.
    assert score == pytest.approx(expected, abs=1e-3)
    assert sum(factor.contribution for factor in factors) * 100.0 == pytest.approx(
        score, abs=1e-9
    )
    assert {factor.factor for factor in factors} == {
        "hazard",
        "exposure",
        "vulnerability",
        "history",
    }


def test_hazard_and_consequence_stay_separable() -> None:
    """Two settlements on identical ground can still rank differently, and must."""
    hazard = ENGINE.hazard_component(70.0, 90.0)
    fragile = _habitation(
        population=900, households=180, elderly=200, children=120, disability=45,
        medical=35, low_income=140, kutcha=0.8, semi_pucca=0.2, facilities=3,
    )
    robust = _habitation(
        hid="H-02", population=200, households=40, elderly=8, children=6, disability=1,
        medical=1, low_income=4, kutcha=0.0, semi_pucca=0.05, facilities=0,
    )
    fragile_score, _ = ENGINE.priority(
        hazard, ENGINE.exposure(fragile), ENGINE.vulnerability(fragile),
        ENGINE.history(fragile, []),
    )
    robust_score, _ = ENGINE.priority(
        hazard, ENGINE.exposure(robust), ENGINE.vulnerability(robust),
        ENGINE.history(robust, []),
    )
    assert fragile_score > robust_score


# ---------------------------------------------------------------------------
# Phasing and override rules (Engine 3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (95.0, PhaseTier.IMMEDIATE),
        (55.0, PhaseTier.IMMEDIATE),
        (54.9, PhaseTier.SHORT_TERM),
        (45.0, PhaseTier.SHORT_TERM),
        (44.9, PhaseTier.MEDIUM_TERM),
        (35.0, PhaseTier.MEDIUM_TERM),
        (34.9, PhaseTier.NOT_PRIORITISED),
    ],
)
def test_tier_thresholds_are_exact(score: float, expected: PhaseTier) -> None:
    phase, reason, rules = ENGINE.phase_for(score, 0.2, ZoneClass.WATCH, 100)
    assert phase is expected
    assert reason
    assert rules == []


def test_critical_zone_and_high_vulnerability_override_the_score() -> None:
    threshold = MODEL_CONFIG.priority.override_critical_vulnerability.value
    phase, reason, rules = ENGINE.phase_for(10.0, threshold, ZoneClass.CRITICAL, 50)
    assert phase is PhaseTier.IMMEDIATE
    assert rules and "critical_zone_vulnerability" in rules[0]
    assert "override" in reason.lower()


def test_critical_zone_and_large_population_override_the_score() -> None:
    population = int(MODEL_CONFIG.priority.override_critical_zone_population.value)
    phase, _, rules = ENGINE.phase_for(10.0, 0.1, ZoneClass.CRITICAL, population)
    assert phase is PhaseTier.IMMEDIATE
    assert rules and "critical_zone_population" in rules[0]


def test_overrides_only_fire_inside_a_critical_zone() -> None:
    phase, _, rules = ENGINE.phase_for(10.0, 0.95, ZoneClass.ELEVATED, 5000)
    assert phase is PhaseTier.NOT_PRIORITISED
    assert rules == []


def test_an_escalation_is_never_silent() -> None:
    _, _, rules = ENGINE.phase_for(10.0, 0.9, ZoneClass.CRITICAL, 9000)
    assert len(rules) == 2, "both override rules fired and both must be named"
    for rule in rules:
        assert rule.startswith("OVERRIDE ")


# ---------------------------------------------------------------------------
# Against the real corridor
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def result():
    from astra.engines.service import baseline_risk

    return PriorityEngine().compute(baseline_risk())


def test_every_habitation_is_ranked_exactly_once(result) -> None:
    ranks = [row.rank for row in result.rows]
    assert sorted(ranks) == list(range(1, len(result.rows) + 1))
    assert len({row.id for row in result.rows}) == len(result.rows)


def test_ranking_is_ordered_by_priority_score(result) -> None:
    scores = [row.priority_score for row in result.rows]
    assert scores == sorted(scores, reverse=True)


def test_the_corridor_ranking_discriminates(result) -> None:
    """A ranking where everything scores the same would decide nothing."""
    scores = [row.priority_score for row in result.rows]
    assert max(scores) - min(scores) > 10.0


def test_phase_totals_account_for_every_habitation(result) -> None:
    totals = result.totals()
    assert sum(entry["habitations"] for entry in totals.values()) == len(result.rows)
    assert sum(entry["population"] for entry in totals.values()) == sum(
        row.habitation.population for row in result.rows
    )


def test_confidence_is_reported_and_never_folded_into_the_score(result) -> None:
    for row in result.rows:
        assert 0.0 <= row.confidence.value <= 1.0
        assert row.confidence.band.value in {"HIGH", "MEDIUM", "LOW"}
        assert row.confidence.note and "not in the ranking" in row.confidence.note
        # The score is reproducible from its own factors, with no confidence term.
        recomputed = 100.0 * sum(f.contribution for f in row.priority_factors)
        assert recomputed == pytest.approx(row.priority_score, abs=0.02)


def test_pending_constraint_checks_are_declared_not_implied(result) -> None:
    for row in result.rows:
        assert row.pending_checks, "unbuilt constraint checks must be stated"


def test_result_is_deterministic(result) -> None:
    from astra.engines.service import baseline_risk

    again = PriorityEngine().compute(baseline_risk())
    assert [(row.id, row.priority_score) for row in again.rows] == [
        (row.id, row.priority_score) for row in result.rows
    ]


def test_computed_at_is_timezone_aware(result) -> None:
    assert result.computed_at.tzinfo is not None
    assert result.computed_at <= datetime.now(UTC)
