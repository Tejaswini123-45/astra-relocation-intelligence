"""Engines 2 and 3 - exposure, vulnerability, history, and phased prioritisation.

Hazard is not consequence. This module keeps the three quantities structurally
separate all the way to the API:

* **Hazard** is what the terrain and the weather do, computed by Engine 1 and
  summarised over the habitation footprint.
* **Exposure** is how many people and how much critical infrastructure are there
  to be harmed.
* **Vulnerability** is how badly the people who are there would be harmed.

and adds a fourth, weaker input:

* **History** is recency-weighted evidence of past events near the settlement -
  one weighted factor, never treated as proof of future hazard.

    P = 100 x ( wH*H + wE*E + wV*V + wHist*Hist )

Confidence is computed alongside and never multiplied into P. A habitation can
score 92 on evidence ASTRA itself rates Medium, and the interface has to be able
to say exactly that.

Phase tiering (Engine 3) applies documented thresholds plus override rules that
are named in the output whenever they fire, so an escalation is never silent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from astra.domain.enums import ConfidenceBand, PhaseTier, ProvenanceClass, ZoneClass
from astra.domain.model_config import MODEL_CONFIG, AstraModelConfig
from astra.domain.models import (
    CompositeHazard,
    ConfidenceReport,
    FactorContribution,
    Habitation,
    ValueExplanation,
)
from astra.domain.notices import HISTORY_NOT_PREDICTION, PRIORITY_NOT_PROBABILITY
from astra.domain.registry import get_formula
from astra.engines.context import IncidentRecord
from astra.engines.service import RiskRun, confidence_band

FORMULA_EXPOSURE = "exposure.index"
FORMULA_VULNERABILITY = "vulnerability.index"
FORMULA_HISTORY = "history.weighted_density"
FORMULA_PRIORITY = "priority.score"
FORMULA_CONFIDENCE = "confidence.index"

#: Recorded when the tiering rules that need a capacity or route engine have not
#: run yet. Surfaced in the API response rather than left implicit.
PENDING_CAPACITY = "matched capacity check (Engine 4)"
PENDING_ROUTES = "route reliability check (Engine 5)"


def _contribution(
    factor: str,
    raw: float | None,
    normalised: float,
    weight: float,
    provenance: ProvenanceClass,
    unit: str | None = None,
) -> FactorContribution:
    normalised = float(np.clip(normalised, 0.0, 1.0))
    return FactorContribution(
        factor=factor,
        raw_value=None if raw is None else round(float(raw), 4),
        normalised_value=round(normalised, 6),
        weight=round(float(weight), 6),
        contribution=round(float(weight) * normalised, 6),
        provenance=provenance,
        unit=unit,
    )


@dataclass(frozen=True)
class ComponentScore:
    """One of the four inputs to the priority score, with its own decomposition."""

    value: float
    factors: list[FactorContribution]
    formula_id: str
    note: str | None = None


@dataclass
class HabitationPriority:
    """The complete assessment of one habitation. Nothing here is collapsed."""

    habitation: Habitation
    hazard: CompositeHazard
    footprint_mean: float
    footprint_max: float
    hazard_component: ComponentScore
    exposure: ComponentScore
    vulnerability: ComponentScore
    history: ComponentScore
    priority_score: float
    priority_factors: list[FactorContribution]
    confidence: ConfidenceReport
    zone_class: ZoneClass
    zone_id: str | None
    phase: PhaseTier
    phase_reason: str
    rules_applied: list[str] = field(default_factory=list)
    pending_checks: list[str] = field(default_factory=list)
    rank: int = 0

    @property
    def id(self) -> str:
        return self.habitation.id


@dataclass
class PriorityResult:
    rows: list[HabitationPriority]
    computed_at: datetime
    config_version: str
    engine_version: str

    def by_phase(self, phase: PhaseTier) -> list[HabitationPriority]:
        return [row for row in self.rows if row.phase is phase]

    def totals(self) -> dict[str, dict[str, int]]:
        """Population and settlement counts per phase, computed once, here."""
        summary: dict[str, dict[str, int]] = {}
        for phase in PhaseTier:
            selected = self.by_phase(phase)
            if not selected and phase in (PhaseTier.CAPACITY_BLOCKED,):
                continue
            summary[phase.value] = {
                "habitations": len(selected),
                "population": sum(row.habitation.population for row in selected),
                "households": sum(row.habitation.households for row in selected),
            }
        return summary


class PriorityEngine:
    """Computes exposure, vulnerability, history, priority and phase."""

    def __init__(self, config: AstraModelConfig | None = None) -> None:
        self.config = config or MODEL_CONFIG

    # -- components ----------------------------------------------------------

    def hazard_component(
        self, mean_composite: float, max_composite: float
    ) -> ComponentScore:
        """Composite hazard over the footprint, blended from its mean and maximum."""
        priority = self.config.priority
        w_mean = priority.footprint_w_mean.value
        w_max = priority.footprint_w_max.value
        factors = [
            _contribution(
                "footprint_mean_composite",
                mean_composite,
                mean_composite / 100.0,
                w_mean,
                ProvenanceClass.DERIVED,
                "index 0-100",
            ),
            _contribution(
                "footprint_max_composite",
                max_composite,
                max_composite / 100.0,
                w_max,
                ProvenanceClass.DERIVED,
                "index 0-100",
            ),
        ]
        return ComponentScore(
            value=float(sum(factor.contribution for factor in factors)),
            factors=factors,
            formula_id="hazard.composite",
            note=(
                "Hazard over the footprint: the mean describes the ground the "
                "settlement sits on, the maximum what can reach it."
            ),
        )

    def exposure(self, habitation: Habitation) -> ComponentScore:
        """How much there is to lose: people, households and critical facilities."""
        priority = self.config.priority
        facilities = len(habitation.critical_facilities)
        factors = [
            _contribution(
                "population",
                habitation.population,
                habitation.population / priority.exposure_reference_population.value,
                priority.exposure_w_population.value,
                ProvenanceClass.SYNTHETIC_CALIBRATED,
                "persons",
            ),
            _contribution(
                "households",
                habitation.households,
                habitation.households / priority.exposure_reference_households.value,
                priority.exposure_w_households.value,
                ProvenanceClass.SYNTHETIC_CALIBRATED,
                "households",
            ),
            _contribution(
                "critical_facilities",
                facilities,
                facilities / priority.exposure_reference_facilities.value,
                priority.exposure_w_facilities.value,
                ProvenanceClass.SYNTHETIC_CALIBRATED,
                "facilities",
            ),
        ]
        return ComponentScore(
            value=float(sum(factor.contribution for factor in factors)),
            factors=factors,
            formula_id=FORMULA_EXPOSURE,
            note=(
                "Measured against fixed reference sizes, so a habitation's exposure "
                "score does not change when another habitation is added or removed."
            ),
        )

    def vulnerability(self, habitation: Habitation) -> ComponentScore:
        """How badly the people who are there would be harmed."""
        priority = self.config.priority
        shares = habitation.demographics.shares(
            habitation.population, habitation.households
        )
        kutcha_share = _weak_structure_share(habitation)
        factors = [
            _contribution(
                "elderly_60_plus",
                habitation.demographics.elderly_60_plus,
                shares["elderly"],
                priority.vuln_w_elderly.value,
                ProvenanceClass.SYNTHETIC_CALIBRATED,
                "share of residents",
            ),
            _contribution(
                "children_under_5",
                habitation.demographics.children_under_5,
                shares["children_u5"],
                priority.vuln_w_children_u5.value,
                ProvenanceClass.SYNTHETIC_CALIBRATED,
                "share of residents",
            ),
            _contribution(
                "persons_with_disability",
                habitation.demographics.persons_with_disability,
                shares["disability"],
                priority.vuln_w_disability.value,
                ProvenanceClass.SYNTHETIC_CALIBRATED,
                "share of residents",
            ),
            _contribution(
                "medically_dependent",
                habitation.demographics.medically_dependent,
                shares["medical_dependency"],
                priority.vuln_w_medical_dependency.value,
                ProvenanceClass.SYNTHETIC_CALIBRATED,
                "share of residents",
            ),
            _contribution(
                "low_income_households",
                habitation.demographics.low_income_households,
                shares["low_income"],
                priority.vuln_w_low_income.value,
                ProvenanceClass.SYNTHETIC_CALIBRATED,
                "share of households",
            ),
            _contribution(
                "kutcha_semi_pucca_share",
                kutcha_share,
                kutcha_share,
                priority.vuln_w_kutcha_share.value,
                ProvenanceClass.SYNTHETIC_CALIBRATED,
                "share of dwellings",
            ),
        ]
        raw = float(sum(factor.contribution for factor in factors))
        return ComponentScore(
            value=float(np.clip(raw / self._vulnerability_reference(), 0.0, 1.0)),
            factors=factors,
            formula_id=FORMULA_VULNERABILITY,
            note=(
                "Scaled against a stated reference profile of an acutely vulnerable "
                "settlement, so vulnerability sits on the same 0-1 scale as hazard. "
                "Demographic composition is an ASTRA demonstration assumption for a "
                "Himalayan hill district, not a census measurement: it is the least "
                "certain input in the priority score and lowers the confidence badge."
            ),
        )

    def _vulnerability_reference(self) -> float:
        """The weighted score of an acutely vulnerable settlement.

        A weighted average of demographic shares lands near 0.2 for any realistic
        settlement, because most of those shares are small. Dividing by the score
        of a stated reference profile puts vulnerability on the same 0-1 scale as
        hazard, so the configured 0.25 weight actually carries 0.25 of the
        decision instead of a fifth of that.
        """
        priority = self.config.priority
        reference = (
            priority.vuln_w_elderly.value * priority.vuln_reference_elderly.value
            + priority.vuln_w_children_u5.value
            * priority.vuln_reference_children_u5.value
            + priority.vuln_w_disability.value * priority.vuln_reference_disability.value
            + priority.vuln_w_medical_dependency.value
            * priority.vuln_reference_medical_dependency.value
            + priority.vuln_w_low_income.value * priority.vuln_reference_low_income.value
            + priority.vuln_w_kutcha_share.value * priority.vuln_reference_kutcha.value
        )
        return max(reference, 1e-9)

    def history(
        self, habitation: Habitation, incidents: list[IncidentRecord]
    ) -> ComponentScore:
        """Recency-weighted evidence of past events near the settlement."""
        priority = self.config.priority
        tau_days = priority.history_tau_days.value
        radius_m = priority.history_radius_m.value

        total = 0.0
        counted = 0
        nearest_km: float | None = None
        for incident in incidents:
            distance_m = _haversine_m(
                habitation.centroid.lon, habitation.centroid.lat, incident.lon, incident.lat
            )
            if distance_m > radius_m:
                continue
            counted += 1
            nearest_km = (
                distance_m / 1000.0
                if nearest_km is None
                else min(nearest_km, distance_m / 1000.0)
            )
            total += incident.weight * math.exp(-(incident.age_years * 365.25) / tau_days)

        reference = priority.history_reference_weighted_events.value
        normalised = min(total / reference, 1.0) if reference > 0 else 0.0
        factors = [
            _contribution(
                "recency_weighted_incidents",
                total,
                normalised,
                1.0,
                ProvenanceClass.REAL_OPEN,
                "weighted events",
            )
        ]
        note = (
            f"{counted} recorded incidents within {radius_m:.0f} m, weighted by severity "
            f"and decayed with a {tau_days / 365.25:.0f}-year time constant. "
            + HISTORY_NOT_PREDICTION
        )
        if nearest_km is not None:
            note = f"Nearest recorded incident {nearest_km:.1f} km away. " + note
        return ComponentScore(
            value=float(normalised),
            factors=factors,
            formula_id=FORMULA_HISTORY,
            note=note,
        )

    # -- priority ------------------------------------------------------------

    def priority(
        self,
        hazard: ComponentScore,
        exposure: ComponentScore,
        vulnerability: ComponentScore,
        history: ComponentScore,
    ) -> tuple[float, list[FactorContribution]]:
        """P = 100 x (wH*H + wE*E + wV*V + wHist*Hist)."""
        priority = self.config.priority
        factors = [
            _contribution(
                "hazard", hazard.value * 100.0, hazard.value,
                priority.w_hazard.value, ProvenanceClass.DERIVED, "index 0-100",
            ),
            _contribution(
                "exposure", None, exposure.value,
                priority.w_exposure.value, ProvenanceClass.SYNTHETIC_CALIBRATED, "index 0-1",
            ),
            _contribution(
                "vulnerability", None, vulnerability.value,
                priority.w_vulnerability.value,
                ProvenanceClass.SYNTHETIC_CALIBRATED, "index 0-1",
            ),
            _contribution(
                "history", None, history.value,
                priority.w_history.value, ProvenanceClass.REAL_OPEN, "index 0-1",
            ),
        ]
        score = 100.0 * sum(factor.contribution for factor in factors)
        return float(np.clip(score, 0.0, 100.0)), factors

    def confidence(
        self,
        run: RiskRun,
        habitation: Habitation,
        history: ComponentScore,
    ) -> ConfidenceReport:
        """Evidence confidence for this habitation's assessment, computed separately.

        It is deliberately lower than the confidence in the terrain beneath the
        settlement: the terrain is measured, the demographic composition is not.
        """
        config = self.config.confidence
        index = run.grid.index_of(habitation.centroid.lon, habitation.centroid.lat)
        terrain_confidence = (
            float(run.result.confidence[index]) if index is not None else 0.5
        )

        required = (
            habitation.population > 0,
            habitation.households > 0,
            bool(habitation.structure_mix),
            habitation.elevation_m is not None,
            habitation.livelihood_centre is not None,
        )
        completeness = sum(1 for present in required if present) / len(required)

        # Hazard inputs are real or derived; exposure and vulnerability are not.
        synthetic_penalty = self.config.priority.confidence_synthetic_penalty.value
        provenance_mix = (
            self.config.priority.w_hazard.value * terrain_confidence
            + self.config.priority.w_history.value * 1.0
            + (
                self.config.priority.w_exposure.value
                + self.config.priority.w_vulnerability.value
            )
            * synthetic_penalty
        )

        recency = float(np.clip(history.value, 0.0, 1.0))
        resolution = 0.75

        components = [
            _contribution(
                "data_completeness", None, completeness,
                config.w_data_completeness.value, ProvenanceClass.DERIVED,
            ),
            _contribution(
                "provenance_mix", None, provenance_mix,
                config.w_provenance_mix.value, ProvenanceClass.DERIVED,
            ),
            _contribution(
                # Named for what it measures: how much recency-weighted incident
                # evidence exists near this settlement, not how new the file is.
                "evidence_support", None, recency,
                config.w_evidence_recency.value, ProvenanceClass.REAL_OPEN,
            ),
            _contribution(
                "spatial_resolution", None, resolution,
                config.w_spatial_resolution.value, ProvenanceClass.DERIVED,
            ),
        ]
        value = float(np.clip(sum(c.contribution for c in components), 0.0, 1.0))
        return ConfidenceReport(
            value=round(value, 4),
            band=confidence_band(value),
            components=components,
            note=(
                "Confidence in the evidence behind this assessment, not in the ranking. "
                "It is lower than the confidence in the terrain because the demographic "
                "composition is an ASTRA assumption. " + PRIORITY_NOT_PROBABILITY
            ),
        )

    # -- phasing (Engine 3) --------------------------------------------------

    def phase_for(
        self,
        score: float,
        vulnerability: float,
        zone_class: ZoneClass,
        population: int,
    ) -> tuple[PhaseTier, str, list[str]]:
        """Assign a phase, naming every rule that fired.

        Overrides exist because a purely additive score can average away a
        situation that an SDMA would treat as urgent on sight: a vulnerable or
        populous settlement standing on Critical ground.
        """
        priority = self.config.priority
        rules: list[str] = []

        if zone_class is ZoneClass.CRITICAL:
            if vulnerability >= priority.override_critical_vulnerability.value:
                rules.append(
                    f"OVERRIDE tier.override.critical_zone_vulnerability: inside a "
                    f"Critical zone with vulnerability {vulnerability:.2f} >= "
                    f"{priority.override_critical_vulnerability.value:.2f}"
                )
            if population >= priority.override_critical_zone_population.value:
                rules.append(
                    f"OVERRIDE tier.override.critical_zone_population: {population} "
                    f"residents inside a Critical zone >= "
                    f"{priority.override_critical_zone_population.value:.0f}"
                )
        if rules:
            return (
                PhaseTier.IMMEDIATE,
                "Escalated to Immediate by an override rule, regardless of score.",
                rules,
            )

        if score >= priority.tier_immediate_min_priority.value:
            return (
                PhaseTier.IMMEDIATE,
                f"Priority {score:.1f} at or above the Immediate threshold "
                f"{priority.tier_immediate_min_priority.value:.0f}.",
                [],
            )
        if score >= priority.tier_short_term_min_priority.value:
            return (
                PhaseTier.SHORT_TERM,
                f"Priority {score:.1f} at or above the Short-term threshold "
                f"{priority.tier_short_term_min_priority.value:.0f}.",
                [],
            )
        if score >= priority.tier_medium_term_min_priority.value:
            return (
                PhaseTier.MEDIUM_TERM,
                f"Priority {score:.1f} at or above the Medium-term threshold "
                f"{priority.tier_medium_term_min_priority.value:.0f}.",
                [],
            )
        return (
            PhaseTier.NOT_PRIORITISED,
            f"Priority {score:.1f} below the Medium-term threshold "
            f"{priority.tier_medium_term_min_priority.value:.0f}. Monitored, not queued "
            "for relocation.",
            [],
        )

    # -- orchestration -------------------------------------------------------

    def compute(self, run: RiskRun) -> PriorityResult:
        from astra.engines.service import hazard_over_footprint, zone_of

        radius = self.config.priority.footprint_radius_m.value
        rows: list[HabitationPriority] = []

        for habitation in run.context.habitations:
            hazard, mean_composite, max_composite = hazard_over_footprint(
                run, habitation.centroid.lon, habitation.centroid.lat, radius
            )
            hazard_component = self.hazard_component(mean_composite, max_composite)
            exposure = self.exposure(habitation)
            vulnerability = self.vulnerability(habitation)
            history = self.history(habitation, run.context.incidents)
            score, factors = self.priority(
                hazard_component, exposure, vulnerability, history
            )
            zone = zone_of(run, habitation.centroid.lon, habitation.centroid.lat)
            zone_class = zone.zone_class if zone else hazard.zone_class
            phase, reason, rules = self.phase_for(
                score, vulnerability.value, zone_class, habitation.population
            )
            rows.append(
                HabitationPriority(
                    habitation=habitation,
                    hazard=hazard,
                    footprint_mean=round(mean_composite, 2),
                    footprint_max=round(max_composite, 2),
                    hazard_component=hazard_component,
                    exposure=exposure,
                    vulnerability=vulnerability,
                    history=history,
                    priority_score=round(score, 2),
                    priority_factors=factors,
                    confidence=self.confidence(run, habitation, history),
                    zone_class=zone_class,
                    zone_id=zone.id if zone else None,
                    phase=phase,
                    phase_reason=reason,
                    rules_applied=rules,
                    pending_checks=[PENDING_CAPACITY, PENDING_ROUTES],
                )
            )

        rows.sort(key=lambda row: row.priority_score, reverse=True)
        for rank, row in enumerate(rows, start=1):
            row.rank = rank

        return PriorityResult(
            rows=rows,
            computed_at=datetime.now(UTC),
            config_version=self.config.version,
            engine_version=self.config.engine_version,
        )


def priority_explanation(row: HabitationPriority, config: AstraModelConfig) -> ValueExplanation:
    """The inspectable payload behind one habitation's priority score."""
    spec = get_formula(FORMULA_PRIORITY)
    return ValueExplanation(
        value=row.priority_score,
        label=f"Relocation priority, {row.habitation.name}",
        unit="index 0-100",
        formula_id=spec.formula_id,
        formula_version=spec.version,
        engine_version=config.engine_version,
        model_config_version=config.version,
        factors=row.priority_factors,
        confidence=row.confidence,
        computed_at=datetime.now(UTC),
        notes=PRIORITY_NOT_PROBABILITY,
    )


def _weak_structure_share(habitation: Habitation) -> float:
    """Share of dwellings that are kutcha or semi-pucca.

    The structural typology proxy in the vulnerability index: a settlement of
    mud-and-stone housing on a slope is not in the same position as one of
    reinforced concrete, whatever the two share demographically.
    """
    weak = 0.0
    for structure, share in habitation.structure_mix.items():
        name = getattr(structure, "value", structure)
        if name in ("KUTCHA", "SEMI_PUCCA"):
            weak += float(share)
    return float(np.clip(weak, 0.0, 1.0))


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in metres."""
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))


def band_of(value: float) -> ConfidenceBand:
    return confidence_band(value)
