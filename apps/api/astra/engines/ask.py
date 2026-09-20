"""The natural-language interface: an allowlist, not an interpreter (§10).

A question in English is matched against a **fixed set of intents**. Each intent
names one function that reads already-computed state and returns a structured
answer. That answer is what renders; prose is assembled from it afterwards.

What this deliberately cannot do, by construction rather than by instruction:

* There is no query language. An intent is a Python function chosen from a tuple
  defined in this file; a question that matches none of them is refused with the
  list of things ASTRA can answer.
* Nothing here reads the filesystem, executes code, opens a shell, or builds SQL.
  The handlers call the same engine accessors the HTTP endpoints call.
* A language model, when one is configured, is used only to pick an intent from
  that same fixed list and to pull out an entity id. It cannot introduce an
  intent, and its choice is validated against the allowlist before anything runs.
  With no key configured the matcher is pure keyword scoring and the interface
  works identically.

The chat is an interface layer. Every answer it gives is reachable in two clicks
without typing anything, and the product is fully understandable without ever
using it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from astra.domain.notices import DECISION_AUTHORITY

HABITATION_ID = re.compile(r"\bH-\d{2}\b", re.IGNORECASE)
SITE_ID = re.compile(r"\bS-\d{2}\b", re.IGNORECASE)


class AskError(ValueError):
    """A question ASTRA will not answer, with what it can answer instead."""


@dataclass(frozen=True)
class Answer:
    """A structured result, plus the prose assembled from it."""

    intent: str
    question: str
    matched_on: str
    entity: str | None
    data: dict[str, Any]
    text: str
    follow_up: list[str]
    decision_authority: str = DECISION_AUTHORITY


@dataclass(frozen=True)
class Intent:
    """One thing ASTRA can be asked, and the one function that answers it."""

    id: str
    question: str
    keywords: tuple[str, ...]
    needs: str | None
    handler: Callable[[str | None], dict[str, Any]]
    render: Callable[[dict[str, Any]], str]


# ---------------------------------------------------------------------------
# Handlers - each reads computed state and returns structured data
# ---------------------------------------------------------------------------


def _priority():
    from astra.api.priority_router import baseline_priority

    return baseline_priority()


def _top_priority(_: str | None) -> dict[str, Any]:
    rows = sorted(_priority().rows, key=lambda row: row.rank)[:5]
    return {
        "habitations": [
            {
                "rank": row.rank,
                "habitation_id": row.habitation.id,
                "name": row.habitation.name,
                "population": row.habitation.population,
                "priority_score": round(row.priority_score, 1),
                "phase": row.phase.value,
                "zone_class": row.zone_class.value,
                "confidence_band": row.confidence.band.value,
            }
            for row in rows
        ]
    }


def _habitation_detail(entity: str | None) -> dict[str, Any]:
    rows = {row.habitation.id.upper(): row for row in _priority().rows}
    row = rows.get((entity or "").upper())
    if row is None:
        raise AskError(
            f"'{entity}' is not a habitation in this study area. "
            "They are " + ", ".join(sorted(rows))
        )
    return {
        "habitation_id": row.habitation.id,
        "name": row.habitation.name,
        "rank": row.rank,
        "of_habitations": len(rows),
        "population": row.habitation.population,
        "priority_score": round(row.priority_score, 1),
        "phase": row.phase.value,
        "phase_reason": row.phase_reason,
        "rules_applied": list(row.rules_applied),
        "hazard_composite": round(row.hazard.composite, 1),
        "dominant_hazard": row.hazard.dominant_hazard.value,
        "components": {
            "hazard": round(row.hazard_component.value, 3),
            "exposure": round(row.exposure.value, 3),
            "vulnerability": round(row.vulnerability.value, 3),
            "history": round(row.history.value, 3),
        },
        "confidence_band": row.confidence.band.value,
    }


def _site_capacity(entity: str | None) -> dict[str, Any]:
    from astra.engines.capacity_service import baseline_capacity

    entries = {entry.site.id.upper(): entry for entry in baseline_capacity()}
    entry = entries.get((entity or "").upper())
    if entry is None:
        raise AskError(
            f"'{entity}' is not a candidate site in this study area. "
            "They are " + ", ".join(sorted(entries))
        )
    intervention = entry.interventions[0] if entry.interventions else None
    return {
        "site_id": entry.site.id,
        "name": entry.site.name,
        "suitable": entry.suitable,
        "failed_gates": [gate.gate.value for gate in entry.failed_gates],
        "theoretical_capacity": round(entry.theoretical_capacity, 0),
        "effective_capacity": round(entry.effective_capacity, 0),
        "bottleneck": entry.bottleneck.value if entry.bottleneck else None,
        "top_intervention": (
            {
                "description": intervention.description,
                "capacity_before": intervention.capacity_before,
                "capacity_after": intervention.capacity_after,
                "capacity_gain": intervention.capacity_gain,
                "next_bottleneck": (
                    intervention.next_bottleneck.value
                    if intervention.next_bottleneck
                    else None
                ),
            }
            if intervention
            else None
        ),
        "limitation": entry.limitation,
    }


def _capacity_overview(_: str | None) -> dict[str, Any]:
    from astra.engines.capacity_service import baseline_capacity

    entries = baseline_capacity()
    suitable = [entry for entry in entries if entry.suitable]
    return {
        "candidate_sites": len(entries),
        "suitable_sites": len(suitable),
        "total_effective_capacity": round(
            sum(entry.effective_capacity for entry in suitable), 0
        ),
        "bottlenecks": sorted(
            {entry.bottleneck.value for entry in suitable if entry.bottleneck}
        ),
        "sites": [
            {
                "site_id": entry.site.id,
                "name": entry.site.name,
                "effective_capacity": round(entry.effective_capacity, 0),
                "bottleneck": entry.bottleneck.value if entry.bottleneck else None,
            }
            for entry in suitable
        ],
    }


def _plan_summary(_: str | None) -> dict[str, Any]:
    from astra.engines.optimizer_service import baseline_plan

    plan, _inputs = baseline_plan()
    return {
        "solver_status": plan.status.value,
        "population_assessed": plan.totals.population_assessed,
        "population_assigned": plan.totals.population_assigned,
        "population_unmet": plan.totals.population_unmet,
        "sites_used": plan.totals.sites_used,
        "mean_travel_time_min": round(plan.totals.mean_travel_time_min, 1),
        "mean_route_reliability": round(plan.totals.mean_route_reliability, 3),
        "assignments": len(plan.assignments),
    }


def _where_does_it_go(entity: str | None) -> dict[str, Any]:
    from astra.engines.optimizer_service import baseline_plan

    plan, _inputs = baseline_plan()
    wanted = (entity or "").upper()
    moves = [
        {
            "site_id": assignment.site_id,
            "phase": assignment.phase.value,
            "people": assignment.people,
            "travel_time_min": round(assignment.travel_time_min, 1),
            "route_reliability": round(assignment.route_reliability, 3),
        }
        for assignment in plan.assignments
        if assignment.habitation_id.upper() == wanted
    ]
    if not moves:
        raise AskError(
            f"the plan moves nobody from '{entity}'. That is either because it is "
            "not prioritised for relocation or because no destination it can reach "
            "has room; the Optimised Plan screen names which"
        )
    return {"habitation_id": wanted, "movements": moves}


def _unmet_demand(_: str | None) -> dict[str, Any]:
    from astra.engines.optimizer_service import baseline_plan, explain_unmet

    plan, inputs = baseline_plan()
    return {
        "population_unmet": plan.totals.population_unmet,
        "reasons": [
            {
                "habitation_id": entry.habitation_id,
                "people": entry.people,
                "reason": str(entry.reason),
                "detail": entry.detail,
            }
            for entry in explain_unmet(plan, inputs)
        ],
    }


def _model_validation(_: str | None) -> dict[str, Any]:
    import json

    from astra.settings import get_settings

    path = get_settings().derived_dir / "validation.json"
    if not path.exists():
        raise AskError(
            "the back-test artifact has not been built in this deployment, so "
            "ASTRA has no validation figures to quote"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    variants = {
        variant["id"]: variant for variant in payload["backtest"]["variants"]
    }
    headline = variants.get("spatial_cv", {})
    return {
        "auc": headline.get("auc"),
        "auc_ci_low": headline.get("auc_ci_low"),
        "auc_ci_high": headline.get("auc_ci_high"),
        "incidents": payload["backtest"]["incidents_in_study_area"],
        "runs": payload["sensitivity"]["runs"],
        "spearman_median": payload["sensitivity"]["spearman_median"],
        "top_k_unchanged_share": payload["sensitivity"]["top_k_unchanged_share"],
        "top_k": payload["sensitivity"]["top_k"],
    }


def _live_state(_: str | None) -> dict[str, Any]:
    from astra.engines.pipeline import get_registry

    registry = get_registry()
    run = registry.latest_completed
    if run is None or run.outputs is None:
        return {"live": False, "events_ingested": len(registry.log.snapshot())}
    return {
        "live": True,
        "run_id": run.id,
        "events_ingested": len(registry.log.snapshot()),
        "cells_rescored": run.outputs.rescore.cells_rescored,
        "cells_in_grid": run.outputs.rescore.cells_in_grid,
        "plan_requires_review": bool(run.review and run.review.required),
        "headline": run.review.headline if run.review else "",
    }


def _route_blocked(_: str | None) -> dict[str, Any]:
    from astra.engines.routes_service import baseline_routes

    corridor = baseline_routes()
    blocked = sorted(
        {
            habitation_id
            for (habitation_id, _site), pair in corridor.pairs.items()
            if not any(
                other.feasible
                for (other_h, _s), other in corridor.pairs.items()
                if other_h == habitation_id
            )
        }
    )
    return {
        "pairs_evaluated": len(corridor.pairs),
        "feasible_pairs": sum(1 for pair in corridor.pairs.values() if pair.feasible),
        "habitations_with_no_reliable_route": blocked,
    }


# ---------------------------------------------------------------------------
# Renderers - prose assembled from the structured answer, never from the model
# ---------------------------------------------------------------------------


def _render_top(data: dict[str, Any]) -> str:
    rows = data["habitations"]
    lead = ", ".join(
        f"{row['name']} ({row['habitation_id']}, priority {row['priority_score']:.0f}, "
        f"{row['population']:,} residents)"
        for row in rows[:3]
    )
    return (
        f"The highest-priority habitations are {lead}. "
        "Priority is a position in a queue, not a probability of harm."
    )


def _render_habitation(data: dict[str, Any]) -> str:
    from astra.engines.narration import template_habitation_advisory

    return template_habitation_advisory(data)


def _render_site(data: dict[str, Any]) -> str:
    from astra.engines.narration import template_site_advisory

    return template_site_advisory(data)


def _render_capacity(data: dict[str, Any]) -> str:
    return (
        f"{data['suitable_sites']} of {data['candidate_sites']} candidate sites pass "
        f"every hard gate, offering {data['total_effective_capacity']:,.0f} effective "
        "places in total. The binding constraints across them are "
        + (", ".join(b.lower() for b in data["bottlenecks"]) or "none")
        + "."
    )


def _render_plan(data: dict[str, Any]) -> str:
    return (
        f"The plan places {data['population_assigned']:,} of "
        f"{data['population_assessed']:,} assessed residents across "
        f"{data['sites_used']} site(s), leaving {data['population_unmet']:,} "
        f"unplaced. Solver status {data['solver_status']}; mean travel time "
        f"{data['mean_travel_time_min']} minutes at mean route reliability "
        f"{data['mean_route_reliability']}."
    )


def _render_movement(data: dict[str, Any]) -> str:
    parts = [
        f"{move['people']:,} residents to {move['site_id']} in the "
        f"{move['phase'].replace('_', '-').lower()} phase "
        f"({move['travel_time_min']} min, reliability {move['route_reliability']})"
        for move in data["movements"]
    ]
    return f"The plan moves, from {data['habitation_id']}: " + "; ".join(parts) + "."


def _render_unmet(data: dict[str, Any]) -> str:
    if not data["population_unmet"]:
        return "Every assessed resident has a destination in the current plan."
    reasons = "; ".join(
        f"{entry['habitation_id']} {entry['people']:,} "
        f"({entry['reason'].replace('_', ' ').lower()})"
        for entry in data["reasons"][:4]
    )
    return (
        f"{data['population_unmet']:,} residents have no destination in the current "
        f"plan: {reasons}."
    )


def _render_validation(data: dict[str, Any]) -> str:
    return (
        f"The hazard model's cross-validated ROC-AUC is {data['auc']:.2f} "
        f"(95% CI {data['auc_ci_low']:.2f}-{data['auc_ci_high']:.2f}) against "
        f"{data['incidents']} recorded incidents, each scored on a surface built "
        f"without it. Across {data['runs']:,} weight-perturbation runs the ranking "
        f"holds a median Spearman correlation of {data['spearman_median']:.3f} and "
        f"the top {data['top_k']} set is unchanged in "
        f"{data['top_k_unchanged_share'] * 100:.0f}% of them."
    )


def _render_live(data: dict[str, Any]) -> str:
    if not data["live"]:
        return (
            "No live evidence has been ingested, so ASTRA is showing the baseline "
            "assessment."
        )
    return (
        f"Run {data['run_id']} re-scored {data['cells_rescored']:,} of "
        f"{data['cells_in_grid']:,} grid cells from "
        f"{data['events_ingested']} ingested observation(s). {data['headline']}"
    )


def _render_blocked(data: dict[str, Any]) -> str:
    blocked = data["habitations_with_no_reliable_route"]
    if not blocked:
        return (
            f"Every habitation has at least one destination it can reach reliably; "
            f"{data['feasible_pairs']} of {data['pairs_evaluated']} pairs clear the "
            "threshold."
        )
    return (
        f"{len(blocked)} habitation(s) have no destination they can reach above the "
        f"reliability threshold: {', '.join(blocked)}. "
        f"{data['feasible_pairs']} of {data['pairs_evaluated']} pairs clear it."
    )


# ---------------------------------------------------------------------------
# The allowlist
# ---------------------------------------------------------------------------


INTENTS: tuple[Intent, ...] = (
    Intent(
        id="top_priority",
        question="Which habitations are the highest priority?",
        keywords=(
            "top", "highest", "priority", "worst", "most at risk", "first",
            "urgent", "which habitation",
        ),
        needs=None,
        handler=_top_priority,
        render=_render_top,
    ),
    Intent(
        id="habitation_detail",
        question="Why is H-01 ranked where it is?",
        keywords=("why", "explain", "habitation", "settlement", "village", "ranked"),
        needs="habitation",
        handler=_habitation_detail,
        render=_render_habitation,
    ),
    Intent(
        id="site_capacity",
        question="What is the capacity and bottleneck at S-05?",
        keywords=("site", "capacity of", "bottleneck", "constraint", "how many can"),
        needs="site",
        handler=_site_capacity,
        render=_render_site,
    ),
    Intent(
        id="capacity_overview",
        question="How much relocation capacity is there in total?",
        keywords=("total capacity", "how much capacity", "all sites", "overall capacity"),
        needs=None,
        handler=_capacity_overview,
        render=_render_capacity,
    ),
    Intent(
        id="plan_summary",
        question="What does the current relocation plan do?",
        keywords=("plan", "allocation", "assignment", "optimiser", "optimizer", "moved"),
        needs=None,
        handler=_plan_summary,
        render=_render_plan,
    ),
    Intent(
        id="where_does_it_go",
        question="Where does H-02 go in the plan?",
        keywords=("where does", "where do", "goes to", "moved to", "sent to"),
        needs="habitation",
        handler=_where_does_it_go,
        render=_render_movement,
    ),
    Intent(
        id="unmet_demand",
        question="Who is left without a destination?",
        keywords=(
            "unmet", "left out", "left without", "no destination", "not placed",
            "unplaced", "nobody", "who is left", "gap",
        ),
        needs=None,
        handler=_unmet_demand,
        render=_render_unmet,
    ),
    Intent(
        id="model_validation",
        question="How well does the hazard model actually perform?",
        keywords=(
            "accurate", "accuracy", "validated", "validation", "auc", "back-test",
            "backtest", "how good", "trust", "reliable is the model",
        ),
        needs=None,
        handler=_model_validation,
        render=_render_validation,
    ),
    Intent(
        id="live_state",
        question="What has the live feed changed?",
        keywords=("live", "latest run", "just happened", "ingested", "real time", "now"),
        needs=None,
        handler=_live_state,
        render=_render_live,
    ),
    Intent(
        id="route_blocked",
        question="Which habitations cannot reach a site reliably?",
        keywords=(
            "route", "road", "reach", "access", "cut off", "blocked",
            "unreachable", "cannot reach", "can't reach", "get to",
        ),
        needs=None,
        handler=_route_blocked,
        render=_render_blocked,
    ),
)

BY_ID = {intent.id: intent for intent in INTENTS}


def catalogue() -> list[dict[str, str]]:
    """Everything ASTRA can be asked. The interface shows this list up front."""
    return [
        {"id": intent.id, "question": intent.question, "needs": intent.needs or ""}
        for intent in INTENTS
    ]


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _entities(question: str) -> tuple[str | None, str | None]:
    habitation = HABITATION_ID.search(question)
    site = SITE_ID.search(question)
    return (
        habitation.group(0).upper() if habitation else None,
        site.group(0).upper() if site else None,
    )


def match(question: str) -> tuple[Intent, str | None, str]:
    """Choose one intent from the allowlist. Keyword scoring, no model needed."""
    lowered = (question or "").lower().strip()
    if not lowered:
        raise AskError("ask a question")

    habitation, site = _entities(question)
    scored: list[tuple[float, Intent, list[str]]] = []
    for intent in INTENTS:
        hits = [word for word in intent.keywords if word in lowered]
        if not hits:
            continue
        # Longer keyword matches are stronger evidence than incidental short ones.
        score = sum(len(word) for word in hits)
        if intent.needs == "habitation" and habitation:
            score += 25
        elif intent.needs == "site" and site:
            score += 25
        elif intent.needs and not (habitation or site):
            # An intent that needs an id, asked without one, is almost never what
            # was meant.
            score -= 15
        scored.append((score, intent, hits))

    if not scored:
        raise AskError(
            "ASTRA answers a fixed set of questions rather than interpreting free "
            "text, so that every answer is a computed value. It can answer: "
            + "; ".join(intent.question for intent in INTENTS)
        )
    scored.sort(key=lambda entry: entry[0], reverse=True)

    # An intent that needs an id, asked without one, is not what was meant if
    # anything else matched at all. "Who is left without a destination?" should
    # answer unmet demand, not demand a habitation id it was never given.
    for _score, intent, hits in scored:
        if intent.needs and not (habitation or site):
            continue
        entity = habitation if intent.needs == "habitation" else site
        return intent, entity, "matched on " + ", ".join(f"'{hit}'" for hit in hits[:3])

    score, intent, hits = scored[0]
    entity = habitation if intent.needs == "habitation" else site
    return intent, entity, "matched on " + ", ".join(f"'{hit}'" for hit in hits[:3])


def ask(question: str, *, intent_id: str | None = None) -> Answer:
    """Answer one question from the allowlist, or refuse and say what is available.

    ``intent_id`` lets the interface offer the catalogue as buttons: choosing one
    skips matching entirely and runs exactly the named handler.
    """
    if intent_id:
        intent = BY_ID.get(intent_id)
        if intent is None:
            raise AskError(
                f"'{intent_id}' is not one of ASTRA's answerable questions: "
                + ", ".join(BY_ID)
            )
        habitation, site = _entities(question)
        entity = habitation if intent.needs == "habitation" else site
        matched = "chosen directly from the catalogue"
    else:
        intent, entity, matched = match(question)

    if intent.needs and not entity:
        raise AskError(
            f"'{intent.question}' needs a {intent.needs} id in the question, "
            "for example H-01 or S-05"
        )

    data = intent.handler(entity)
    return Answer(
        intent=intent.id,
        question=question,
        matched_on=matched,
        entity=entity,
        data=data,
        text=intent.render(data),
        follow_up=[
            other.question for other in INTENTS if other.id != intent.id
        ][:3],
    )
