"""The formula registry: every computed number in ASTRA names its own formula.

CLAUDE.md section 6: *"Every displayed number is inspectable in one click,
returning: value, formula ID and version, inputs with their values, weighted
contributions, provenance class per input, confidence, timestamp."*

This module is the lookup table that makes that one click possible. An engine
that computes a number attaches a ``formula_id``; the explainability API
resolves it here to a human-readable expression, its inputs and the config keys
it consumes. A formula that is not registered cannot be surfaced, which is the
point: it forces every number to declare its arithmetic.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from astra.domain.model_config import MODEL_CONFIG, Constant


class FormulaSpec(BaseModel):
    """A documented, deterministic computation. Served by ``GET /model/config``."""

    model_config = ConfigDict(frozen=True)

    formula_id: str = Field(description="Stable ID attached to every value it produces.")
    version: str = Field(description="Bumped when the expression itself changes.")
    title: str = Field(description="Short human label used in the UI.")
    expression: str = Field(description="The arithmetic, written out.")
    inputs: list[str] = Field(description="Named inputs the expression consumes.")
    config_keys: list[str] = Field(
        default_factory=list,
        description="Keys in the model config supplying weights or thresholds.",
    )
    engine: str = Field(description="Engine module that owns this formula.")
    notes: str | None = Field(
        default=None, description="Interpretation caveats shown alongside the result."
    )
    citation: str | None = Field(
        default=None, description="Published methodology this follows, if any."
    )


FORMULAS: dict[str, FormulaSpec] = {
    spec.formula_id: spec
    for spec in [
        FormulaSpec(
            formula_id="hazard.hsi",
            version="1.0.0",
            title="Per-hazard susceptibility index",
            expression="HSI_h = 100 * sum_f( w_hf * n_f(x) ), with sum_f w_hf = 1",
            inputs=["normalised factor rasters n_f(x)"],
            config_keys=["w.landslide.*", "w.flood.*", "w.cloudburst.*", "w.coastal.*"],
            engine="engines.hazard",
            notes=(
                "Weighted linear overlay of normalised 0-1 factor rasters, the "
                "methodology used in published landslide hazard zonation."
            ),
            citation=MODEL_CONFIG.hazard.landslide_weights.method_citation,
        ),
        FormulaSpec(
            formula_id="hazard.composite",
            version="1.0.0",
            title="Multi-hazard composite score",
            expression="C = min(100, max_h(HSI_h) + lambda * second_highest_h(HSI_h))",
            inputs=["HSI per hazard"],
            config_keys=["hazard.composite_lambda"],
            engine="engines.hazard",
            notes=(
                "Dominance preserving: the strongest hazard sets the level and a "
                "second coincident hazard adds to it. Averaging would hide exactly "
                "the multi-hazard cells that matter most."
            ),
        ),
        FormulaSpec(
            formula_id="hazard.zone_class",
            version="1.0.0",
            title="Red-zone classification",
            expression=(
                "class = CRITICAL if C >= t_critical else ELEVATED if C >= t_elevated "
                "else WATCH if C >= t_watch else LOW"
            ),
            inputs=["composite score C"],
            config_keys=[
                "hazard.zone_threshold.CRITICAL",
                "hazard.zone_threshold.ELEVATED",
                "hazard.zone_threshold.WATCH",
            ],
            engine="engines.hazard",
            notes=(
                "An ASTRA analytical classification. It is not a statutory zone "
                "designation and does not declare any real place unsafe."
            ),
        ),
        FormulaSpec(
            formula_id="exposure.index",
            version="1.0.0",
            title="Exposure index",
            expression=(
                "E = w_pop * norm(population) + w_hh * norm(households) "
                "+ w_fac * norm(critical_facilities)"
            ),
            inputs=["population", "households", "critical facility count"],
            config_keys=[
                "exposure.w_population",
                "exposure.w_households",
                "exposure.w_facilities",
            ],
            engine="engines.priority",
        ),
        FormulaSpec(
            formula_id="vulnerability.index",
            version="1.0.0",
            title="Vulnerability index",
            expression="V = sum_k( w_k * share_k ), V in [0, 1]",
            inputs=[
                "elderly share",
                "under-five share",
                "disability share",
                "medical dependency share",
                "low-income household share",
                "kutcha and semi-pucca dwelling share",
            ],
            config_keys=["vulnerability.w_*"],
            engine="engines.priority",
            notes=(
                "Demographic proportions are calibrated to published district "
                "figures; the absolute counts they are applied to are synthetic."
            ),
        ),
        FormulaSpec(
            formula_id="history.weighted_density",
            version="1.0.0",
            title="Recency-weighted incident history",
            expression="Hist = sum_i( severity_i * exp(-dt_i / tau) ) for d_i <= r",
            inputs=["historical incident points with severity and date"],
            config_keys=["history.tau_days", "history.radius_m"],
            engine="engines.priority",
            notes=(
                "Past incidents are one weighted factor, not proof of future hazard."
            ),
        ),
        FormulaSpec(
            formula_id="priority.score",
            version="1.0.0",
            title="Relocation priority score",
            expression="P = 100 * normalise( wH*H + wE*E + wV*V + wHist*Hist )",
            inputs=["hazard H", "exposure E", "vulnerability V", "history Hist"],
            config_keys=[
                "priority.w_hazard",
                "priority.w_exposure",
                "priority.w_vulnerability",
                "priority.w_history",
            ],
            engine="engines.priority",
            notes=(
                "A ranking score, not a probability. Evidence confidence is computed "
                "separately and is never multiplied into this number."
            ),
        ),
        FormulaSpec(
            formula_id="confidence.index",
            version="1.0.0",
            title="Evidence confidence",
            expression=(
                "conf = w_dc*completeness + w_pm*provenance_mix "
                "+ w_er*recency + w_sr*resolution"
            ),
            inputs=[
                "input completeness",
                "provenance class mix",
                "evidence age",
                "coarsest input resolution",
            ],
            config_keys=["confidence.w_*", "confidence.band.*"],
            engine="engines.validate",
            notes="Orthogonal to priority. Reported as an independent badge and band.",
        ),
        FormulaSpec(
            formula_id="capacity.per_service",
            version="1.0.0",
            title="Per-service capacity",
            expression="cap_s = supply_s / norm_s",
            inputs=["service supply at the site", "published per-person norm"],
            config_keys=[
                "capacity.water_lpcd",
                "capacity.persons_per_latrine",
                "capacity.site_area_m2_per_person",
                "capacity.persons_per_health_facility",
                "capacity.shelter_occupancy",
                "capacity.power_kva_per_household",
            ],
            engine="engines.capacity",
            citation=MODEL_CONFIG.capacity.water_litres_per_person_day.citation,
        ),
        FormulaSpec(
            formula_id="capacity.effective",
            version="1.0.0",
            title="Effective carrying capacity and binding bottleneck",
            expression=(
                "theoretical = cap_land; effective = min_s(cap_s); "
                "bottleneck = argmin_s(cap_s)"
            ),
            inputs=["per-service capacities"],
            engine="engines.capacity",
            notes=(
                "A site can only absorb as many people as its scarcest service "
                "supports. The named bottleneck is what an SDMA can actually act on."
            ),
        ),
        FormulaSpec(
            formula_id="capacity.marginal_intervention",
            version="1.0.0",
            title="Marginal intervention analysis",
            expression=(
                "gain_s = min_s'( cap_s' | supply_s + delta_s ) - effective_capacity"
            ),
            inputs=["per-service capacities", "unit intervention size per service"],
            engine="engines.capacity",
            notes="Reports the next binding constraint after the intervention.",
        ),
        FormulaSpec(
            formula_id="route.reliability",
            version="1.0.0",
            title="Route reliability",
            expression="R = product over segments of (1 - p_fail_seg)",
            inputs=["per-segment hazard exposure", "bridge dependency flags"],
            config_keys=[
                "route.p_fail.hazard_coefficient",
                "route.p_fail.bridge_dependency",
            ],
            engine="engines.routes",
        ),
        FormulaSpec(
            formula_id="route.safest_objective",
            version="1.0.0",
            title="Safest route objective",
            expression="minimise time * (1 + alpha * risk)",
            inputs=["segment travel time", "segment risk"],
            config_keys=["route.safest_alpha"],
            engine="engines.routes",
        ),
        FormulaSpec(
            formula_id="optimiser.objective",
            version="1.0.0",
            title="Relocation assignment objective",
            expression=(
                "minimise b1*unmet_demand*priority + b2*people*travel_time "
                "+ b3*people*route_risk + b4*site_overload "
                "+ b5*livelihood_disruption + b6*fragmentation + b7*phase_delay"
            ),
            inputs=["candidate assignments x[h][s][phase]"],
            config_keys=["opt.beta1_unmet_demand", "opt.beta2_travel_time",
                         "opt.beta3_route_risk", "opt.beta4_site_overload",
                         "opt.beta5_livelihood_disruption", "opt.beta6_fragmentation",
                         "opt.beta7_phase_delay"],
            engine="engines.optimizer",
            notes=(
                "Solved with CP-SAT under a fixed seed and time limit. If the limit "
                "is reached the deterministic greedy fallback runs and the response "
                "is labelled FALLBACK."
            ),
        ),
        FormulaSpec(
            formula_id="optimiser.livelihood_disruption",
            version="1.0.0",
            title="Livelihood disruption",
            expression=(
                "L = w_tt*norm(travel_to_livelihood_centre) + w_c*connectivity_penalty "
                "+ w_rr*(1 - road_reliability) + w_ma*(1 - market_access)"
            ),
            inputs=[
                "travel time to origin livelihood centre",
                "destination connectivity class",
                "road reliability",
                "market and service access",
            ],
            config_keys=["livelihood.w_*"],
            engine="engines.optimizer",
            notes="A computed composite. Not a fixed kilometre rule.",
        ),
        FormulaSpec(
            formula_id="validation.success_rate_curve",
            version="1.0.0",
            title="Susceptibility success-rate curve",
            expression=(
                "SRC(x) = fraction of held-out historical incidents falling inside "
                "the top x% of the composite susceptibility surface"
            ),
            inputs=["held-out incident inventory", "composite surface"],
            engine="engines.validate",
            notes=(
                "Inventory completeness and spatial reporting bias limit the ceiling "
                "of this validation; the figure is reported as measured."
            ),
        ),
        FormulaSpec(
            formula_id="validation.rank_stability",
            version="1.0.0",
            title="Weight sensitivity and rank stability",
            expression=(
                "perturb every weight by +/- p over N runs; report Spearman rho "
                "against baseline and the share of runs preserving the top-k set"
            ),
            inputs=["baseline priority ranking"],
            config_keys=[
                "validation.sensitivity_runs",
                "validation.sensitivity_perturbation",
                "validation.rank_stability_top_k",
            ],
            engine="engines.validate",
        ),
    ]
}


def get_formula(formula_id: str) -> FormulaSpec:
    """Resolve a formula ID. Raises if a number references an unregistered formula."""
    try:
        return FORMULAS[formula_id]
    except KeyError as exc:  # pragma: no cover - guarded by tests
        raise KeyError(
            f"formula '{formula_id}' is not registered; every computed value must "
            "reference a registered formula so it can be explained in one hop"
        ) from exc


def constants_for(formula_id: str) -> list[Constant]:
    """Config constants a formula consumes, resolved through wildcard prefixes."""
    spec = get_formula(formula_id)
    resolved: list[Constant] = []
    for constant in MODEL_CONFIG.constants():
        for key in spec.config_keys:
            if key.endswith("*"):
                if constant.key.startswith(key[:-1]):
                    resolved.append(constant)
                    break
            elif constant.key == key:
                resolved.append(constant)
                break
    return resolved
