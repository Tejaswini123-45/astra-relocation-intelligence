"""The Decision Brief (§8.4): the page an SDMA officer acts on.

A brief is not a new computation. It is the standing state of every engine -
hazard, priority, capacity, routes, the solved plan and the validation artifact -
read off the same objects the screens render, and arranged in the order an
official reads them: what is happening, who is at risk, where they can go, what
to do in each phase, what could break, and how far to trust any of it.

Three properties are enforced here rather than promised:

* **Every figure is read, never re-derived.** Plan totals, phases, site loads and
  plan dependencies come through the same serialisers the Plan, Sites and Routes
  screens use, so a brief cannot print a number a screen disagrees with.
* **A generated brief is frozen.** It is stored as the exact payload that was
  rendered, beside a decision-ledger row carrying the input hash, so a brief
  printed next week can be traced to the state that produced it.
* **It says what it is.** The basis (baseline or live), the decision authority,
  the synthetic-scenario notice and the limitations travel inside the brief.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from astra.data.scenarios import BASELINE
from astra.data.store import dumps, loads, new_id, session
from astra.domain.enums import ConfidenceBand, PhaseTier, SolverStatus, ZoneClass
from astra.domain.model_config import MODEL_CONFIG
from astra.domain.notices import NOTICES

ACTION_PHASES = (PhaseTier.IMMEDIATE, PhaseTier.SHORT_TERM, PhaseTier.MEDIUM_TERM)

#: Ties on the modal confidence band resolve towards the lower band. A brief that
#: rounds evidence confidence up is the one mistake it cannot afford.
BAND_ORDER = (ConfidenceBand.LOW, ConfidenceBand.MEDIUM, ConfidenceBand.HIGH)


@dataclass(frozen=True)
class StandingState:
    """The picture every screen is showing: the latest live run, else the baseline."""

    basis: str
    run_id: str | None
    events_ingested: int
    review_required: bool
    review_headline: str | None
    risk: Any
    priority: Any
    capacity: list[Any]
    routes: Any
    plan: Any
    plan_inputs: Any


def standing_state() -> StandingState:
    from astra.api.priority_router import baseline_priority
    from astra.engines.capacity_service import baseline_capacity
    from astra.engines.optimizer_service import baseline_plan
    from astra.engines.pipeline import get_registry
    from astra.engines.routes_service import baseline_routes
    from astra.engines.service import baseline_risk

    registry = get_registry()
    run = registry.latest_completed
    events = len(registry.log.snapshot())
    if run is not None and run.outputs is not None:
        outputs = run.outputs
        return StandingState(
            basis="LIVE",
            run_id=run.id,
            events_ingested=events,
            review_required=bool(run.review and run.review.required),
            review_headline=run.review.headline if run.review else None,
            risk=outputs.risk,
            priority=outputs.priority,
            capacity=outputs.capacity,
            routes=outputs.routes,
            plan=outputs.plan,
            plan_inputs=outputs.plan_inputs,
        )
    plan, inputs = baseline_plan()
    return StandingState(
        basis="BASELINE",
        run_id=None,
        events_ingested=events,
        review_required=False,
        review_headline=None,
        risk=baseline_risk(),
        priority=baseline_priority(),
        capacity=baseline_capacity(),
        routes=baseline_routes(),
        plan=plan,
        plan_inputs=inputs,
    )


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or f"{singular}s")


def _movement(assignment) -> dict[str, Any]:
    return {
        "habitation_id": assignment.habitation_id,
        "habitation_name": assignment.habitation_name,
        "site_id": assignment.site_id,
        "site_name": assignment.site_name,
        "phase": str(assignment.phase),
        "people": assignment.people,
        "travel_time_min": round(assignment.travel_time_min, 1),
        "route_reliability": round(assignment.route_reliability, 3),
    }


def _validation() -> dict[str, Any]:
    """The back-test and sensitivity headline, as the Model screen serves it."""
    from astra.settings import get_settings

    path = get_settings().derived_dir / "validation.json"
    if not path.exists():
        return {
            "available": False,
            "auc": None,
            "auc_ci_low": None,
            "auc_ci_high": None,
            "incidents": None,
            "runs": None,
            "spearman_median": None,
            "top_k": None,
            "top_k_unchanged_share": None,
            "backtest_headline": None,
            "sensitivity_headline": None,
            "limitation": (
                "The validation artifact has not been built in this deployment, so "
                "the brief quotes no validation figures."
            ),
            "stale": False,
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    variants = {variant["id"]: variant for variant in payload["backtest"]["variants"]}
    headline = variants.get("spatial_cv", {})
    return {
        "available": True,
        "auc": headline.get("auc"),
        "auc_ci_low": headline.get("auc_ci_low"),
        "auc_ci_high": headline.get("auc_ci_high"),
        "incidents": payload["backtest"]["incidents_in_study_area"],
        "runs": payload["sensitivity"]["runs"],
        "spearman_median": payload["sensitivity"]["spearman_median"],
        "top_k": payload["sensitivity"]["top_k"],
        "top_k_unchanged_share": payload["sensitivity"]["top_k_unchanged_share"],
        "backtest_headline": payload["backtest"]["headline"],
        "sensitivity_headline": payload["sensitivity"]["headline"],
        "limitation": payload["backtest"]["limitation"],
        "stale": (
            payload.get("model_config_version") != MODEL_CONFIG.version
            or payload.get("engine_version") != MODEL_CONFIG.engine_version
        ),
    }


def build(state: StandingState) -> dict[str, Any]:
    """Assemble the brief for one standing state. Writes nothing."""
    from astra.api.plan_router import serialise_plan
    from astra.api.routes_router import plan_dependencies
    from astra.engines.capacity import marginal_sentence
    from astra.engines.capacity_service import total_effective_capacity
    from astra.engines.narration import narrate

    plan = serialise_plan(state.plan, state.plan_inputs, state.routes)
    rows = sorted(state.priority.rows, key=lambda row: row.rank)
    priority_config = MODEL_CONFIG.priority
    threshold = MODEL_CONFIG.route.min_reliability_threshold.value

    # -- situation ---------------------------------------------------------
    zone_summary = state.risk.summary()
    critical = zone_summary.get(ZoneClass.CRITICAL.value, {})
    critical_area = round(float(critical.get("area_km2", 0.0)), 3)
    zone_count = sum(int(entry["count"]) for entry in zone_summary.values())
    by_phase = {str(phase): totals for phase, totals in state.priority.totals().items()}
    immediate = by_phase.get(PhaseTier.IMMEDIATE.value, {"habitations": 0, "population": 0})
    in_hazard = sum(
        1 for row in rows if row.zone_class in (ZoneClass.CRITICAL, ZoneClass.ELEVATED)
    )
    totals = plan.totals
    situation_headline = (
        f"{critical_area:.1f} km2 of the corridor is classified Critical "
        f"({NOTICES.classification_label}). {int(immediate['population']):,} residents "
        f"in {int(immediate['habitations'])} "
        f"{_plural(int(immediate['habitations']), 'habitation')} fall in the immediate "
        f"tier. The plan places {totals.population_assigned:,} of "
        f"{totals.population_assessed:,} residents it assessed for relocation"
        + (
            f", leaving {totals.population_unmet:,} without a destination."
            if totals.population_unmet
            else "."
        )
    )

    # -- who is at risk ----------------------------------------------------
    priorities = [
        {
            "rank": row.rank,
            "habitation_id": row.habitation.id,
            "name": row.habitation.name,
            "population": row.habitation.population,
            "priority_score": round(row.priority_score, 1),
            "phase": row.phase.value,
            "phase_reason": row.phase_reason,
            "rules_applied": list(row.rules_applied),
            "zone_class": row.zone_class.value,
            "dominant_hazard": row.hazard.dominant_hazard.value,
            "hazard_composite": round(row.hazard.composite, 1),
            "components": {
                "hazard": round(row.hazard_component.value, 3),
                "exposure": round(row.exposure.value, 3),
                "vulnerability": round(row.vulnerability.value, 3),
                "history": round(row.history.value, 3),
            },
            "confidence_band": row.confidence.band.value,
        }
        for row in rows
    ]

    # -- where they can go -------------------------------------------------
    load = {entry.site_id: entry for entry in plan.site_load}
    suitable = [entry for entry in state.capacity if entry.suitable]
    sites = [
        {
            "site_id": entry.site.id,
            "name": entry.site.name,
            "suitable": entry.suitable,
            "failed_gates": [gate.gate.value for gate in entry.failed_gates],
            "theoretical_capacity": round(entry.theoretical_capacity, 0),
            "effective_capacity": round(entry.effective_capacity, 0),
            "bottleneck": entry.bottleneck.value if entry.bottleneck else None,
            "marginal_headline": marginal_sentence(entry),
            "assigned": load[entry.site.id].assigned if entry.site.id in load else None,
            "remaining": load[entry.site.id].remaining if entry.site.id in load else None,
        }
        for entry in state.capacity
    ]
    effective_total = total_effective_capacity(state.capacity)
    theoretical_total = round(sum(entry.theoretical_capacity for entry in suitable), 1)
    bottlenecks = sorted(
        {site["bottleneck"] for site in sites if site["suitable"] and site["bottleneck"]}
    )

    # -- what to do, phase by phase ----------------------------------------
    phase_totals = {str(entry.phase): entry for entry in plan.phases}
    tier_floor = {
        PhaseTier.IMMEDIATE: priority_config.tier_immediate_min_priority.value,
        PhaseTier.SHORT_TERM: priority_config.tier_short_term_min_priority.value,
        PhaseTier.MEDIUM_TERM: priority_config.tier_medium_term_min_priority.value,
    }
    interventions = [
        site["marginal_headline"]
        for site in sites
        if site["suitable"] and site["marginal_headline"]
    ]
    actions: list[dict[str, Any]] = []
    for phase in ACTION_PHASES:
        entry = phase_totals.get(phase.value)
        movements = sorted(
            (_movement(a) for a in plan.assignments if str(a.phase) == phase.value),
            key=lambda move: (-move["people"], move["habitation_id"], move["site_id"]),
        )
        people = entry.people_moved if entry else 0
        steps: list[str] = []
        if entry and people:
            steps.append(
                f"Move {people:,} residents from {entry.habitations} "
                f"{_plural(entry.habitations, 'habitation')} to {entry.sites_used} "
                f"{_plural(entry.sites_used, 'site')}, on journeys within the "
                f"{entry.travel_ceiling_min:.0f}-minute ceiling for this phase."
            )
        else:
            steps.append("The plan moves nobody in this phase under current conditions.")
        if phase is PhaseTier.SHORT_TERM and interventions:
            steps.append("Relieve receiving-site bottlenecks: " + " ".join(interventions[:3]))
        if phase is PhaseTier.MEDIUM_TERM and totals.population_unmet:
            steps.append(
                f"{totals.population_unmet:,} assessed residents still have no "
                "destination; the unmet-demand reasons below name why."
            )
        actions.append(
            {
                "phase": phase.value,
                "tier_rule": (
                    f"Priority at or above {tier_floor[phase]:g} (DEMO_CONFIG), plus "
                    "the documented override rules."
                ),
                "steps": steps,
                "people_moved": people,
                "habitations": entry.habitations if entry else 0,
                "sites_used": entry.sites_used if entry else 0,
                "travel_ceiling_min": entry.travel_ceiling_min if entry else None,
                "movements": movements,
            }
        )

    # -- what could break --------------------------------------------------
    pairs = state.routes.pairs
    suitable_ids = {entry.site.id for entry in suitable}
    usable = {
        habitation_id
        for (habitation_id, site_id), pair in pairs.items()
        if pair.feasible and site_id in suitable_ids
    }
    all_habitations = {row.habitation.id for row in rows}
    blocked = sorted(all_habitations - usable)
    dependencies = plan_dependencies(state.plan, state.routes)
    weakest = sorted(
        (_movement(a) for a in plan.assignments),
        key=lambda move: (move["route_reliability"], move["habitation_id"]),
    )[:5]
    routes = {
        "closed_segments": sorted(state.routes.closed_segments),
        "pairs_evaluated": len(pairs),
        "feasible_pairs": sum(1 for pair in pairs.values() if pair.feasible),
        "reliability_threshold": threshold,
        "habitations_without_reachable_suitable_site": blocked,
        "dependencies": [row.model_dump(mode="json") for row in dependencies[:5]],
        "weakest_movements": weakest,
    }

    # -- how far to trust it -----------------------------------------------
    counts = Counter(row.confidence.band.value for row in rows)
    modal = max(
        BAND_ORDER, key=lambda band: (counts.get(band.value, 0), -BAND_ORDER.index(band))
    )
    confidence = {
        "modal_band": modal.value,
        "bands": {band.value: counts.get(band.value, 0) for band in BAND_ORDER},
        "note": (
            f"Modal evidence-confidence band across the {len(rows)} assessed "
            "habitations; ties resolve to the lower band. Confidence is reported "
            "beside priority and never multiplied into it."
        ),
    }
    validation = _validation()

    # -- what the extra layers change --------------------------------------
    top = rows[0] if rows else None
    escalated = sum(1 for row in rows if row.rules_applied)
    phase_people = {phase.value: 0 for phase in ACTION_PHASES}
    for action in actions:
        phase_people[action["phase"]] = action["people_moved"]
    comparison = [
        {
            "question": "Where is the ground hazardous?",
            "static_map": (
                f"{zone_count} zones; {critical_area:.1f} km2 Critical. "
                f"{in_hazard} of {len(rows)} habitations sit on Critical or Elevated ground."
            ),
            "astra": (
                "The same surface, with every zone and cell decomposed into its "
                "dominant hazard and weighted factor contributions."
            ),
        },
        {
            "question": "Who moves first?",
            "static_map": "Not answered: every settlement inside a zone looks alike.",
            "astra": (
                f"A ranked queue led by {top.habitation.name} ({top.habitation.id}) at "
                f"priority {top.priority_score:.1f}; {escalated} "
                f"{_plural(escalated, 'habitation')} escalated by a named override rule."
                if top
                else "No habitations assessed."
            ),
        },
        {
            "question": "Where can they go?",
            "static_map": "Not answered: ground outside a zone is not capacity.",
            "astra": (
                f"{len(suitable)} of {len(state.capacity)} candidate sites pass every "
                f"gate; {effective_total:,.0f} effective places against "
                f"{theoretical_total:,.0f} on land alone, bound by "
                + (", ".join(b.lower() for b in bottlenecks) or "no single service")
                + "."
            ),
        },
        {
            "question": "Can they get there?",
            "static_map": "Not answered: roads are drawn, not assessed.",
            "astra": (
                f"{routes['feasible_pairs']} of {routes['pairs_evaluated']} "
                f"habitation-to-site routes clear reliability {threshold:g}; "
                f"{len(blocked)} {_plural(len(blocked), 'habitation')} "
                "cannot reach a suitable site reliably."
            ),
        },
        {
            "question": "How many are actually placed, and when?",
            "static_map": "Not answered.",
            "astra": (
                f"{totals.population_assigned:,} of {totals.population_assessed:,} placed "
                f"by {plan.solver} ({plan.status.value}): immediate "
                f"{phase_people[PhaseTier.IMMEDIATE.value]:,}, short-term "
                f"{phase_people[PhaseTier.SHORT_TERM.value]:,}, medium-term "
                f"{phase_people[PhaseTier.MEDIUM_TERM.value]:,}; "
                f"{totals.population_unmet:,} unmet, each with a named reason."
            ),
        },
        {
            "question": "What if conditions change?",
            "static_map": "The map has to be redrawn.",
            "astra": (
                "A rainfall, closure or site change re-runs the whole chain and the "
                "plan is shown before and after, with what it invalidates."
            ),
        },
    ]

    # -- prose, from the computed result only ------------------------------
    advisory = narrate(
        "plan",
        {
            "totals": totals.model_dump(mode="json"),
            "phases": [entry.model_dump(mode="json") for entry in plan.phases],
            "unmet_reasons": [entry.model_dump(mode="json") for entry in plan.unmet[:3]],
            "solver_status": plan.status.value,
        },
    )

    capacity_config = MODEL_CONFIG.capacity
    hazard_config = MODEL_CONFIG.hazard
    assumptions = [
        constant.model_dump(mode="json")
        for constant in (
            priority_config.w_hazard,
            priority_config.w_exposure,
            priority_config.w_vulnerability,
            priority_config.w_history,
            priority_config.tier_immediate_min_priority,
            hazard_config.composite_lambda,
            hazard_config.zone_threshold_critical,
            MODEL_CONFIG.route.min_reliability_threshold,
            capacity_config.site_area_m2_per_person,
            capacity_config.water_litres_per_person_day,
            capacity_config.persons_per_latrine,
        )
    ]

    limitations = [
        NOTICES.scenario_disclaimer,
        NOTICES.site_tenure_limitation,
        NOTICES.priority_not_probability,
        NOTICES.history_not_prediction,
    ]
    if validation["limitation"]:
        limitations.append(validation["limitation"])
    if plan.status is SolverStatus.FALLBACK:
        limitations.append(
            "This plan came from the deterministic greedy fallback because the solver "
            "reached its time limit. It is a workable allocation, not an optimal one."
        )

    basis_note = (
        f"Built on live run {state.run_id} over {state.events_ingested} ingested "
        f"{_plural(state.events_ingested, 'observation')}. The baseline assessment is "
        "unchanged and remains available for comparison."
        if state.basis == "LIVE"
        else "Built on the baseline assessment; no live observation had been ingested."
    )

    return {
        "id": None,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "basis": state.basis,
        "basis_note": basis_note,
        "run_id": state.run_id,
        "events_ingested": state.events_ingested,
        "scenario_id": BASELINE.id,
        "scenario_name": BASELINE.name,
        "situation": {
            "headline": situation_headline,
            "zones": zone_summary,
            "zone_count": zone_count,
            "critical_area_km2": critical_area,
            "habitations_assessed": len(rows),
            "population_assessed": sum(row.habitation.population for row in rows),
            "habitations_in_critical_or_elevated": in_hazard,
            "by_phase": by_phase,
            "plan_requires_review": state.review_required,
            "review_headline": state.review_headline,
        },
        "priorities": priorities,
        "capacity": {
            "candidate_sites": len(state.capacity),
            "suitable_sites": len(suitable),
            "total_effective_capacity": effective_total,
            "total_theoretical_capacity": theoretical_total,
            "bottlenecks": bottlenecks,
            "sites": sites,
            "limitation": NOTICES.site_tenure_limitation,
        },
        "plan": {
            "status": plan.status.value,
            "solver": plan.solver,
            "objective_value": plan.objective_value,
            "headline": plan.headline,
            "totals": totals.model_dump(mode="json"),
            "unmet": [entry.model_dump(mode="json") for entry in plan.unmet],
            "capacity_blocked": list(plan.capacity_blocked),
        },
        "actions": actions,
        "routes": routes,
        "validation": validation,
        "confidence": confidence,
        "comparison": comparison,
        "narration": {"text": advisory.text, "mode": advisory.mode, "note": advisory.note},
        "assumptions": assumptions,
        "limitations": limitations,
        "audit": None,
        "how_this_works": NOTICES.how_this_works,
        "decision_authority": NOTICES.decision_authority,
        "classification_label": NOTICES.classification_label,
        "scenario_disclaimer": NOTICES.scenario_disclaimer,
        "priority_note": NOTICES.priority_not_probability,
        "engine_version": MODEL_CONFIG.engine_version,
        "model_config_version": MODEL_CONFIG.version,
    }


def generate(*, notes: str | None = None) -> dict[str, Any]:
    """Build the brief, write its decision-ledger row, and freeze it."""
    from astra.engines.audit import record_decision

    state = standing_state()
    payload = build(state)
    record = record_decision(
        scenario_id=BASELINE.id,
        trigger="decision brief",
        plan=state.plan,
        plan_inputs=state.plan_inputs,
        priority=state.priority,
        capacity=state.capacity,
        routes=state.routes,
        run_id=state.run_id,
        confidence=payload["confidence"]["modal_band"],
        notes=notes,
    )
    brief_id = new_id("BRF")
    payload["id"] = brief_id
    payload["audit"] = {
        "decision_id": record.id,
        "created_at": record.created_at,
        "input_summary_hash": record.input_summary_hash,
        "engine_version": record.engine_version,
        "model_config_version": record.model_config_version,
        "solver_status": record.solver_status,
        "objective_value": record.objective_value,
        "state": record.state,
    }
    with session() as connection:
        connection.execute(
            "INSERT INTO briefs (id, decision_id, created_at, basis, headline, payload) "
            "VALUES (?,?,?,?,?,?)",
            (
                brief_id,
                record.id,
                payload["generated_at"],
                payload["basis"],
                payload["situation"]["headline"],
                dumps(payload),
            ),
        )
    return payload


def get(brief_id: str) -> dict[str, Any] | None:
    """A generated brief, exactly as it was frozen."""
    with session() as connection:
        row = connection.execute(
            "SELECT payload FROM briefs WHERE id = ?", (brief_id,)
        ).fetchone()
    return loads(row["payload"]) if row else None


def recent(limit: int = 10) -> list[dict[str, Any]]:
    with session() as connection:
        rows = connection.execute(
            "SELECT id, decision_id, created_at, basis, headline FROM briefs "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (max(1, min(limit, 50)),),
        ).fetchall()
    return [dict(row) for row in rows]
