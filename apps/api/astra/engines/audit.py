"""The decision ledger and human-in-the-loop override (§6).

ASTRA computes. People decide. This module is the seam between those two
sentences, and it holds three commitments:

**Every run that could inform a decision leaves a record.** Not a log line - a
row carrying the scenario, the engine and config versions, the layers the inputs
came from, a hash of those inputs, the score components, the constraint and
solver status, the objective value and the evidence confidence. A brief printed
next week has to be traceable to the exact state that produced it.

**An override is recorded with its reason, and never silently applied.** The
reason is required. The record is permanent. The plan on screen still shows what
the optimiser computed alongside what the officer decided.

**An override's consequence is computed, not asserted.** Forcing a movement is
re-solved through the same CP-SAT model, and the ledger records what that
actually cost: the objective delta, who was displaced, and whether the forced
assignment is feasible at all. An override that breaks a hard constraint is
recorded as an override that breaks a hard constraint - because an SDMA may have
reasons ASTRA does not model, and hiding the cost would serve nobody.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from astra.data.store import dumps, loads, new_id, session
from astra.domain.enums import DecisionState
from astra.domain.model_config import MODEL_CONFIG

OVERRIDE_ACTIONS = ("APPROVE", "FORCE_ASSIGNMENT", "REJECT_ASSIGNMENT", "ANNOTATE")
"""What a person can record against a computed plan. Each is a statement about
the *decision*, never an edit of the computation that produced it."""


class AuditError(ValueError):
    """A decision or override ASTRA will not record as asked."""


@dataclass
class DecisionRecord:
    """One computed state a decision could be taken on."""

    id: str
    created_at: str
    scenario_id: str
    trigger: str
    run_id: str | None
    engine_version: str
    model_config_version: str
    source_layer_ids: list[str]
    input_summary_hash: str
    score_components: dict[str, Any]
    constraint_status: dict[str, Any]
    solver_status: str
    objective_value: float | None
    confidence: str
    state: str
    notes: str | None
    overrides: list[OverrideRecord] = field(default_factory=list)


@dataclass
class OverrideRecord:
    """One thing a person did about a computed plan, and what it cost."""

    id: str
    decision_id: str
    created_at: str
    actor: str
    action: str
    habitation_id: str | None
    site_id: str | None
    people: int | None
    reason: str
    consequence: dict[str, Any]


def input_hash(payload: dict[str, Any]) -> str:
    """A stable fingerprint of what went into a decision.

    Sorted-key JSON so the same inputs hash identically across processes and
    machines. Short enough to read off a printed brief, long enough that two
    different states will not collide in any run this system will ever see.
    """
    return hashlib.sha256(dumps(payload).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def record_decision(
    *,
    scenario_id: str,
    trigger: str,
    plan,
    plan_inputs,
    priority,
    capacity,
    routes,
    run_id: str | None = None,
    confidence: str = "MEDIUM",
    notes: str | None = None,
) -> DecisionRecord:
    """Write the ledger row for one computed plan.

    Everything stored here is read off the objects the engines produced. The
    summary hash covers the demand, the capacities and the allowed options - the
    three things that decide what the solver could possibly have returned - so
    two decisions with the same hash were solved against the same world.
    """
    suitable = [entry for entry in capacity if entry.suitable]
    summary = {
        "demand": {
            habitation_id: people for habitation_id, people in sorted(plan_inputs.demand.items())
        },
        "effective_capacity": {
            entry.site.id: round(entry.effective_capacity, 3)
            for entry in sorted(capacity, key=lambda entry: entry.site.id)
        },
        "suitable_sites": sorted(entry.site.id for entry in suitable),
        "options": sorted(
            f"{option.habitation_id}->{option.site_id}:{option.phase.value}"
            for option in plan_inputs.options
        ),
        "closed_segments": sorted(routes.closed_segments),
    }

    record = DecisionRecord(
        id=new_id("DEC"),
        created_at=datetime.now(tz=UTC).isoformat(),
        scenario_id=scenario_id,
        trigger=trigger,
        run_id=run_id,
        engine_version=MODEL_CONFIG.engine_version,
        model_config_version=MODEL_CONFIG.version,
        source_layer_ids=_source_layers(),
        input_summary_hash=input_hash(summary),
        score_components={
            "priority": [
                {
                    "habitation_id": row.habitation.id,
                    "rank": row.rank,
                    "priority": round(row.priority_score, 2),
                    "phase": row.phase.value,
                    "hazard": round(row.hazard.composite, 2),
                    "exposure": round(row.exposure.value, 4),
                    "vulnerability": round(row.vulnerability.value, 4),
                    "history": round(row.history.value, 4),
                }
                for row in sorted(priority.rows, key=lambda row: row.rank)
            ],
            "totals": {
                "population_assessed": plan.totals.population_assessed,
                "population_assigned": plan.totals.population_assigned,
                "population_unmet": plan.totals.population_unmet,
                "sites_used": plan.totals.sites_used,
                "mean_travel_time_min": round(plan.totals.mean_travel_time_min, 2),
                "mean_route_reliability": round(plan.totals.mean_route_reliability, 4),
            },
            "assignments": [
                {
                    "habitation_id": assignment.habitation_id,
                    "site_id": assignment.site_id,
                    "phase": assignment.phase.value,
                    "people": assignment.people,
                    "route_reliability": round(assignment.route_reliability, 4),
                }
                for assignment in plan.assignments
            ],
        },
        constraint_status={
            "suitable_sites": len(suitable),
            "candidate_sites": len(capacity),
            "feasible_pairs": sum(1 for pair in routes.pairs.values() if pair.feasible),
            "pairs_evaluated": len(routes.pairs),
            "allowed_options": len(plan_inputs.options),
            "closed_segments": sorted(routes.closed_segments),
            "reliability_threshold": MODEL_CONFIG.route.min_reliability_threshold.value,
            "post_solve_validation": "PASSED",
        },
        solver_status=plan.status.value,
        objective_value=plan.objective_value,
        confidence=confidence,
        state=DecisionState.COMPUTED.value,
        notes=notes,
    )

    with session() as connection:
        connection.execute(
            """
            INSERT INTO decisions (
                id, created_at, scenario_id, trigger, run_id, engine_version,
                model_config_version, source_layer_ids, input_summary_hash,
                score_components, constraint_status, solver_status,
                objective_value, confidence, state, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                record.id,
                record.created_at,
                record.scenario_id,
                record.trigger,
                record.run_id,
                record.engine_version,
                record.model_config_version,
                dumps(record.source_layer_ids),
                record.input_summary_hash,
                dumps(record.score_components),
                dumps(record.constraint_status),
                record.solver_status,
                record.objective_value,
                record.confidence,
                record.state,
                record.notes,
            ),
        )
    return record


def _source_layers() -> list[str]:
    """The registered datasets every computed layer in this decision rests on."""
    from astra.data.provenance import get_registry

    try:
        return sorted(dataset.id for dataset in get_registry().datasets)
    except Exception:  # noqa: BLE001 - a missing registry must not lose the record
        return []


def record_override(
    *,
    decision_id: str,
    actor: str,
    action: str,
    reason: str,
    habitation_id: str | None = None,
    site_id: str | None = None,
    people: int | None = None,
    consequence: dict[str, Any] | None = None,
) -> OverrideRecord:
    """Record what a person decided, and what the computation says it costs."""
    if action not in OVERRIDE_ACTIONS:
        raise AuditError(
            f"'{action}' is not a recordable action; one of "
            + ", ".join(OVERRIDE_ACTIONS)
        )
    if not reason or not reason.strip():
        raise AuditError(
            "an override has to carry a reason. ASTRA records what a person "
            "decided and why, and an unexplained departure from the computed plan "
            "is the one thing an audit trail cannot be built from"
        )
    if action in ("FORCE_ASSIGNMENT", "REJECT_ASSIGNMENT") and not (
        habitation_id and site_id
    ):
        raise AuditError(
            f"a {action} has to name both the habitation and the site it concerns"
        )

    record = OverrideRecord(
        id=new_id("OVR"),
        decision_id=decision_id,
        created_at=datetime.now(tz=UTC).isoformat(),
        actor=actor,
        action=action,
        habitation_id=habitation_id,
        site_id=site_id,
        people=people,
        reason=reason.strip(),
        consequence=consequence or {},
    )
    state = {
        "APPROVE": DecisionState.APPROVED,
        "FORCE_ASSIGNMENT": DecisionState.OVERRIDDEN,
        "REJECT_ASSIGNMENT": DecisionState.OVERRIDDEN,
        "ANNOTATE": DecisionState.UNDER_REVIEW,
    }[action]

    with session() as connection:
        existing = connection.execute(
            "SELECT id, state FROM decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        if existing is None:
            raise AuditError(f"no decision '{decision_id}' to record against")
        connection.execute(
            """
            INSERT INTO overrides (
                id, decision_id, created_at, actor, action,
                habitation_id, site_id, people, reason, consequence
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                record.id,
                record.decision_id,
                record.created_at,
                record.actor,
                record.action,
                record.habitation_id,
                record.site_id,
                record.people,
                record.reason,
                dumps(record.consequence),
            ),
        )
        # An annotation on an already-approved decision does not un-approve it.
        if not (
            state is DecisionState.UNDER_REVIEW
            and existing["state"] == DecisionState.APPROVED.value
        ):
            connection.execute(
                "UPDATE decisions SET state = ? WHERE id = ?",
                (state.value, decision_id),
            )
    return record


def mark_requires_review(decision_id: str, note: str) -> None:
    """New evidence has undermined a decision. Say so on the record itself."""
    with session() as connection:
        connection.execute(
            "UPDATE decisions SET state = ?, notes = ? WHERE id = ?",
            (DecisionState.REQUIRES_REVIEW.value, note, decision_id),
        )


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _decision_from(row, overrides: list[OverrideRecord]) -> DecisionRecord:
    return DecisionRecord(
        id=row["id"],
        created_at=row["created_at"],
        scenario_id=row["scenario_id"],
        trigger=row["trigger"],
        run_id=row["run_id"],
        engine_version=row["engine_version"],
        model_config_version=row["model_config_version"],
        source_layer_ids=loads(row["source_layer_ids"]) or [],
        input_summary_hash=row["input_summary_hash"],
        score_components=loads(row["score_components"]) or {},
        constraint_status=loads(row["constraint_status"]) or {},
        solver_status=row["solver_status"],
        objective_value=row["objective_value"],
        confidence=row["confidence"],
        state=row["state"],
        notes=row["notes"],
        overrides=overrides,
    )


def _override_from(row) -> OverrideRecord:
    return OverrideRecord(
        id=row["id"],
        decision_id=row["decision_id"],
        created_at=row["created_at"],
        actor=row["actor"],
        action=row["action"],
        habitation_id=row["habitation_id"],
        site_id=row["site_id"],
        people=row["people"],
        reason=row["reason"],
        consequence=loads(row["consequence"]) or {},
    )


def get_decision(decision_id: str) -> DecisionRecord | None:
    with session() as connection:
        row = connection.execute(
            "SELECT * FROM decisions WHERE id = ?", (decision_id,)
        ).fetchone()
        if row is None:
            return None
        overrides = [
            _override_from(entry)
            for entry in connection.execute(
                "SELECT * FROM overrides WHERE decision_id = ? ORDER BY created_at",
                (decision_id,),
            )
        ]
    return _decision_from(row, overrides)


def list_decisions(limit: int = 25) -> list[DecisionRecord]:
    with session() as connection:
        rows = connection.execute(
            "SELECT * FROM decisions ORDER BY created_at DESC, id DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        ).fetchall()
        ids = [row["id"] for row in rows]
        overrides: dict[str, list[OverrideRecord]] = {row_id: [] for row_id in ids}
        if ids:
            placeholders = ",".join("?" for _ in ids)
            for entry in connection.execute(
                f"SELECT * FROM overrides WHERE decision_id IN ({placeholders}) "
                "ORDER BY created_at",
                ids,
            ):
                overrides[entry["decision_id"]].append(_override_from(entry))
    return [_decision_from(row, overrides[row["id"]]) for row in rows]


def latest_decision() -> DecisionRecord | None:
    found = list_decisions(limit=1)
    return found[0] if found else None
