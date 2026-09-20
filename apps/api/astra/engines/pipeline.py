"""The execution pipeline: one run, seven stages, real events on the wire.

A run is what happens when evidence arrives. It executes the same chain every
other part of ASTRA executes - hazard, exposure, capacity, routes, optimisation -
and, as it goes, emits a stage event carrying what that stage actually did:
rows processed, values computed, milliseconds elapsed.

**The events are the execution, not a description of it.** A stage event is
appended at the moment the stage finishes, by the code that ran it, from the
objects it produced. There is no timer driving a fake sequence and no fixed
script of stage durations; if a stage is slow the graph sits on it, and if a
stage fails the graph says which one and why. That is the whole point of §9: a
node lighting up has to mean something happened.

Runs execute on a worker thread and append to a buffer. The SSE endpoint replays
that buffer from the beginning and then follows it, so a client that connects
late - or reconnects - still sees every stage rather than joining halfway
through a story. Terminal runs stay readable by id.
"""

from __future__ import annotations

import threading
import time
import traceback
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from astra.data.scenarios import BASELINE
from astra.domain.enums import PhaseTier, RunStage, RunStageStatus, RunStatus
from astra.domain.model_config import MODEL_CONFIG
from astra.domain.models import HazardEvent
from astra.engines.audit import record_decision
from astra.engines.capacity import SiteCapacity
from astra.engines.capacity_service import compute_site_capacity
from astra.engines.live import EventLog, RescoreReport, baseline_ceilings, rescore
from astra.engines.optimizer import Plan
from astra.engines.optimizer_service import PlanInputs, build_inputs, solve_plan
from astra.engines.priority import PriorityEngine, PriorityResult
from astra.engines.routes_service import CorridorRoutes, evaluate_corridor
from astra.engines.service import RiskRun, baseline_risk

STAGE_ORDER: tuple[RunStage, ...] = (
    RunStage.INGEST,
    RunStage.HAZARD,
    RunStage.EXPOSURE_VULNERABILITY,
    RunStage.SITE_CAPACITY,
    RunStage.ROUTE_RELIABILITY,
    RunStage.OPTIMISATION,
    RunStage.DECISION_BRIEF,
)

STAGE_LABEL: dict[RunStage, str] = {
    RunStage.INGEST: "Ingest",
    RunStage.HAZARD: "Hazard re-score",
    RunStage.EXPOSURE_VULNERABILITY: "Exposure & vulnerability",
    RunStage.SITE_CAPACITY: "Site capacity",
    RunStage.ROUTE_RELIABILITY: "Route reliability",
    RunStage.OPTIMISATION: "Optimisation",
    RunStage.DECISION_BRIEF: "Decision brief",
}

MAX_RUNS = 25
"""Completed runs kept in memory, newest first. A demo replays a feed many
times; the history is a working record, not an archive."""


# ---------------------------------------------------------------------------
# Stage events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageEvent:
    """One thing that happened during a run, at the moment it happened."""

    sequence: int
    run_id: str
    stage: RunStage
    status: RunStageStatus
    at: datetime
    message: str
    elapsed_ms: float
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["stage"] = self.stage.value
        data["status"] = self.status.value
        data["at"] = self.at.isoformat()
        return data

    @property
    def event_name(self) -> str:
        """The SSE event name, matching the vocabulary in CLAUDE.md section 9."""
        return {
            RunStageStatus.STARTED: "stage_started",
            RunStageStatus.PROGRESS: "stage_progress",
            RunStageStatus.COMPLETED: "stage_completed",
            RunStageStatus.WARNING: "warning",
            RunStageStatus.FAILED: "stage_failed",
            RunStageStatus.PENDING: "stage_pending",
        }[self.status]


# ---------------------------------------------------------------------------
# What a run produced
# ---------------------------------------------------------------------------


@dataclass
class RunOutputs:
    """The complete assessment one run arrived at."""

    risk: RiskRun
    priority: PriorityResult
    capacity: list[SiteCapacity]
    routes: CorridorRoutes
    plan: Plan
    plan_inputs: PlanInputs
    rescore: RescoreReport
    closed_segments: frozenset[str]


@dataclass
class PlanReview:
    """Which standing decisions this run invalidated, and why.

    ASTRA never revokes a plan. It says, in the officer's terms, which specific
    movements the new evidence has undermined and leaves the decision where it
    belongs. An empty ``reasons`` means the standing plan still holds under the
    new evidence - which is itself worth saying out loud.
    """

    required: bool
    reasons: list[str] = field(default_factory=list)
    invalidated_movements: list[dict[str, Any]] = field(default_factory=list)
    people_affected: int = 0
    headline: str = ""


@dataclass
class Run:
    """One execution of the pipeline, with everything it emitted."""

    id: str
    trigger: str
    created_at: datetime
    events: list[HazardEvent]
    status: RunStatus = RunStatus.QUEUED
    stages: list[StageEvent] = field(default_factory=list)
    outputs: RunOutputs | None = None
    review: PlanReview | None = None
    error: str | None = None
    finished_at: datetime | None = None
    total_ms: float = 0.0
    decision_id: str | None = None

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # -- emitting ----------------------------------------------------------

    def emit(
        self,
        stage: RunStage,
        status: RunStageStatus,
        message: str,
        *,
        elapsed_ms: float = 0.0,
        **payload: Any,
    ) -> StageEvent:
        with self._lock:
            event = StageEvent(
                sequence=len(self.stages),
                run_id=self.id,
                stage=stage,
                status=status,
                at=datetime.now(tz=UTC),
                message=message,
                elapsed_ms=round(elapsed_ms, 1),
                payload=payload,
            )
            self.stages.append(event)
        return event

    def events_from(self, index: int) -> list[StageEvent]:
        with self._lock:
            return self.stages[index:]

    @property
    def terminal(self) -> bool:
        return self.status in (RunStatus.COMPLETED, RunStatus.FAILED)


# ---------------------------------------------------------------------------
# Executing a run
# ---------------------------------------------------------------------------


class _Stopwatch:
    def __init__(self) -> None:
        self.mark = time.perf_counter()

    def lap(self) -> float:
        now = time.perf_counter()
        elapsed = (now - self.mark) * 1000.0
        self.mark = now
        return elapsed


def execute(run: Run, log: EventLog) -> Run:
    """Run the chain over the current event log, emitting as it goes.

    Every payload below is read off the object the stage just produced. Nothing
    is estimated, and no stage reports a number it did not compute.
    """
    run.status = RunStatus.RUNNING
    started = time.perf_counter()
    watch = _Stopwatch()

    try:
        baseline = baseline_risk()

        # -- INGEST --------------------------------------------------------
        logged = log.snapshot()
        run.emit(
            RunStage.INGEST,
            RunStageStatus.STARTED,
            f"Accepting {len(run.events)} new observation(s)",
            events_in_batch=len(run.events),
            events_total=len(logged),
        )
        footprints = [
            {
                "event_id": event.id,
                "kind": event.kind.value,
                "description": event.describe(),
                "lon": event.lon,
                "lat": event.lat,
                "radius_m": event.radius_m,
                "observed_at": event.observed_at.isoformat(),
                "source": event.source,
                "provenance": event.provenance.value,
            }
            for event in run.events
        ]
        run.emit(
            RunStage.INGEST,
            RunStageStatus.COMPLETED,
            f"{len(run.events)} observation(s) accepted, "
            f"{len(logged)} in the log",
            elapsed_ms=watch.lap(),
            events_in_batch=len(run.events),
            events_total=len(logged),
            closed_segments=sorted(log.closed_segments),
            footprints=footprints,
        )

        # -- HAZARD --------------------------------------------------------
        run.emit(
            RunStage.HAZARD,
            RunStageStatus.STARTED,
            "Re-scoring the cells the new evidence touches",
            surface_events=len(log.surface_events),
        )
        risk, report = rescore(
            baseline, log.surface_events, ceilings=baseline_ceilings()
        )
        run.emit(
            RunStage.HAZARD,
            RunStageStatus.COMPLETED,
            report.note,
            elapsed_ms=watch.lap(),
            cells_rescored=report.cells_rescored,
            cells_in_grid=report.cells_in_grid,
            share_rescored=round(report.share, 5),
            window_rows=report.window_rows,
            window_cols=report.window_cols,
            zones_before=report.zones_before,
            zones_after=report.zones_after,
            scored_ms=report.scored_ms,
            zoned_ms=report.zoned_ms,
            cells_changed=report.cells_changed,
            composite_delta_max=report.composite_delta_max,
            composite_delta_mean=report.composite_delta_mean,
            reclassified_cells=report.reclassified_cells,
        )

        # -- EXPOSURE & VULNERABILITY --------------------------------------
        run.emit(
            RunStage.EXPOSURE_VULNERABILITY,
            RunStageStatus.STARTED,
            "Re-ranking habitations against the new hazard state",
            habitations=len(risk.context.habitations),
        )
        priority = PriorityEngine().compute(risk)
        immediate = priority.by_phase(PhaseTier.IMMEDIATE)
        # Which settlements the new evidence actually moved, against the standing
        # baseline. A small, true movement reported precisely is worth more than
        # a headline number that leaves a viewer guessing whether anything
        # happened at all.
        moved = _habitations_moved(priority)
        tier_changes = [entry for entry in moved if entry["phase_changed"]]
        if tier_changes:
            run.emit(
                RunStage.EXPOSURE_VULNERABILITY,
                RunStageStatus.WARNING,
                "; ".join(
                    f"{entry['name']} moves {entry['phase_before']} -> "
                    f"{entry['phase_after']}"
                    for entry in tier_changes
                ),
                tier_changes=tier_changes,
            )
        run.emit(
            RunStage.EXPOSURE_VULNERABILITY,
            RunStageStatus.COMPLETED,
            f"{len(priority.rows)} habitations ranked; "
            f"{len(immediate)} in the immediate tier; "
            + (
                f"{len(moved)} habitation(s) moved, most of all "
                f"{moved[0]['name']} {moved[0]['hazard_before']:.1f} -> "
                f"{moved[0]['hazard_after']:.1f}"
                if moved
                else "no habitation's hazard score moved"
            ),
            elapsed_ms=watch.lap(),
            habitations=len(priority.rows),
            immediate=len(immediate),
            immediate_population=sum(row.habitation.population for row in immediate),
            top_habitation=priority.rows[0].habitation.id if priority.rows else None,
            top_priority=round(priority.rows[0].priority_score, 1)
            if priority.rows
            else None,
            habitations_moved=moved,
            tier_changes=tier_changes,
        )

        # -- ROUTES --------------------------------------------------------
        # Routes come before capacity: access throughput is a capacity
        # constraint, so the road has to be evaluated first or the site's
        # effective capacity would be computed against an access figure that
        # predates the closure that just came in.
        closed = log.closed_segments
        run.emit(
            RunStage.ROUTE_RELIABILITY,
            RunStageStatus.STARTED,
            "Re-routing the corridor"
            + (f" with {len(closed)} segment(s) closed" if closed else ""),
            closed_segments=sorted(closed),
        )
        routes = evaluate_corridor(
            closed_segments=closed,
            habitations=list(risk.context.habitations),
            sites=list(risk.context.sites),
        )
        feasible = sum(1 for pair in routes.pairs.values() if pair.feasible)
        if closed and feasible == 0:
            run.emit(
                RunStage.ROUTE_RELIABILITY,
                RunStageStatus.WARNING,
                "No habitation-site pair clears the reliability threshold on the "
                "open network. Every movement in the plan below is unreachable.",
                closed_segments=sorted(closed),
            )
        run.emit(
            RunStage.ROUTE_RELIABILITY,
            RunStageStatus.COMPLETED,
            f"{feasible} of {len(routes.pairs)} habitation-site pairs clear the "
            f"{MODEL_CONFIG.route.min_reliability_threshold.value * 100:.0f}% "
            "reliability threshold",
            elapsed_ms=watch.lap(),
            pairs=len(routes.pairs),
            feasible_pairs=feasible,
            closed_segments=sorted(closed),
            segments=len(routes.network.segments),
        )

        # -- CAPACITY ------------------------------------------------------
        run.emit(
            RunStage.SITE_CAPACITY,
            RunStageStatus.STARTED,
            "Re-checking suitability gates and effective capacity",
            sites=len(risk.context.sites),
        )
        access = {
            site_id: entry.access_capacity_persons
            for site_id, entry in routes.access.items()
        }
        capacity = compute_site_capacity(risk, access=access)
        suitable = [entry for entry in capacity if entry.suitable]
        lost = [
            entry.site.id
            for entry in capacity
            if not entry.suitable
            and entry.site.id
            in {e.site.id for e in _baseline_capacity_snapshot() if e.suitable}
        ]
        if lost:
            run.emit(
                RunStage.SITE_CAPACITY,
                RunStageStatus.WARNING,
                f"{len(lost)} candidate site(s) no longer clear every hard gate: "
                + ", ".join(lost),
                sites_lost=lost,
            )
        run.emit(
            RunStage.SITE_CAPACITY,
            RunStageStatus.COMPLETED,
            f"{len(suitable)} of {len(capacity)} candidate sites pass every gate; "
            f"{sum(e.effective_capacity for e in suitable):,.0f} effective places",
            elapsed_ms=watch.lap(),
            sites=len(capacity),
            suitable_sites=len(suitable),
            effective_capacity=round(
                sum(entry.effective_capacity for entry in suitable), 1
            ),
            sites_lost=lost,
        )

        # -- OPTIMISATION --------------------------------------------------
        run.emit(
            RunStage.OPTIMISATION,
            RunStageStatus.STARTED,
            "Re-solving the relocation assignment",
            options=None,
        )
        inputs = build_inputs(routes, priority=priority, capacity=capacity)
        plan, inputs = solve_plan(inputs)
        if plan.status.value == "FALLBACK":
            run.emit(
                RunStage.OPTIMISATION,
                RunStageStatus.WARNING,
                "The solver hit its time limit; this plan is the deterministic "
                "greedy fallback and is labelled FALLBACK wherever it appears.",
                solver_status=plan.status.value,
            )
        run.emit(
            RunStage.OPTIMISATION,
            RunStageStatus.COMPLETED,
            f"{plan.status.value}: {plan.totals.population_assigned:,} of "
            f"{plan.totals.population_assessed:,} residents placed across "
            f"{plan.totals.sites_used} site(s)",
            elapsed_ms=watch.lap(),
            solver_status=plan.status.value,
            solve_ms=round(plan.solve_ms, 1),
            options=len(inputs.options),
            assignments=len(plan.assignments),
            population_assigned=plan.totals.population_assigned,
            population_unmet=plan.totals.population_unmet,
            objective_value=plan.objective_value,
        )

        # -- DECISION BRIEF ------------------------------------------------
        run.emit(
            RunStage.DECISION_BRIEF,
            RunStageStatus.STARTED,
            "Checking the standing plan against the new evidence",
        )
        review = review_plan(plan, priority, capacity, routes)
        if review.required:
            run.emit(
                RunStage.DECISION_BRIEF,
                RunStageStatus.WARNING,
                review.headline,
                people_affected=review.people_affected,
                reasons=review.reasons,
            )
        run.emit(
            RunStage.DECISION_BRIEF,
            RunStageStatus.COMPLETED,
            review.headline,
            elapsed_ms=watch.lap(),
            plan_requires_review=review.required,
            reasons=review.reasons,
            invalidated_movements=len(review.invalidated_movements),
            people_affected=review.people_affected,
        )

        run.outputs = RunOutputs(
            risk=risk,
            priority=priority,
            capacity=capacity,
            routes=routes,
            plan=plan,
            plan_inputs=inputs,
            rescore=report,
            closed_segments=closed,
        )
        run.review = review

        # Every run that could inform a decision leaves a ledger row (section 6).
        # A failure to write one must not lose the run, but it is reported as a
        # warning rather than swallowed: a decision nobody can trace back is a
        # gap in the audit trail, and the person looking at the screen should
        # know the gap exists.
        try:
            record = record_decision(
                scenario_id=BASELINE.id,
                trigger=run.trigger,
                plan=plan,
                plan_inputs=inputs,
                priority=priority,
                capacity=capacity,
                routes=routes,
                run_id=run.id,
                notes=review.headline,
            )
            run.decision_id = record.id
            run.emit(
                RunStage.DECISION_BRIEF,
                RunStageStatus.PROGRESS,
                f"Recorded in the decision ledger as {record.id}",
                decision_id=record.id,
                input_summary_hash=record.input_summary_hash,
            )
        except Exception as error:  # noqa: BLE001 - the run survives; the gap is said
            run.emit(
                RunStage.DECISION_BRIEF,
                RunStageStatus.WARNING,
                f"This run could not be written to the decision ledger ({error}). "
                "The assessment stands, but it is not traceable from the audit "
                "trail.",
            )

        run.status = RunStatus.COMPLETED
    except Exception as error:  # noqa: BLE001 - the run records its own failure
        run.status = RunStatus.FAILED
        run.error = f"{type(error).__name__}: {error}"
        run.emit(
            _current_stage(run),
            RunStageStatus.FAILED,
            run.error,
            elapsed_ms=watch.lap(),
            traceback=traceback.format_exc(limit=4),
        )
    finally:
        run.finished_at = datetime.now(tz=UTC)
        run.total_ms = round((time.perf_counter() - started) * 1000.0, 1)
    return run


def _habitations_moved(priority: PriorityResult) -> list[dict[str, Any]]:
    """Habitations whose hazard or phase has moved from the standing baseline.

    Sorted by how far the hazard score moved, so the stage payload leads with the
    settlement the evidence says the most about.
    """
    from astra.api.priority_router import baseline_priority

    before = {row.habitation.id: row for row in baseline_priority().rows}
    moved: list[dict[str, Any]] = []
    for row in priority.rows:
        base = before.get(row.habitation.id)
        if base is None:
            continue
        hazard_delta = row.hazard.composite - base.hazard.composite
        phase_changed = base.phase is not row.phase
        if abs(hazard_delta) < 0.05 and not phase_changed:
            continue
        moved.append(
            {
                "habitation_id": row.habitation.id,
                "name": row.habitation.name,
                "population": row.habitation.population,
                "hazard_before": round(base.hazard.composite, 1),
                "hazard_after": round(row.hazard.composite, 1),
                "hazard_delta": round(hazard_delta, 1),
                "priority_before": round(base.priority_score, 1),
                "priority_after": round(row.priority_score, 1),
                "phase_before": base.phase.value,
                "phase_after": row.phase.value,
                "phase_changed": phase_changed,
            }
        )
    moved.sort(key=lambda entry: abs(entry["hazard_delta"]), reverse=True)
    return moved


def _current_stage(run: Run) -> RunStage:
    """The stage a failure belongs to: the last one that started."""
    for event in reversed(run.stages):
        if event.status is RunStageStatus.STARTED:
            return event.stage
    return RunStage.INGEST


def _baseline_capacity_snapshot() -> list[SiteCapacity]:
    from astra.engines.capacity_service import baseline_capacity

    return baseline_capacity()


# ---------------------------------------------------------------------------
# Plan invalidation
# ---------------------------------------------------------------------------


def review_plan(
    plan: Plan,
    priority: PriorityResult,
    capacity: list[SiteCapacity],
    routes: CorridorRoutes,
) -> PlanReview:
    """Which movements in the *standing* plan the new evidence has undermined.

    The standing plan is the baseline one - the plan an officer is looking at and
    may already have approved. Every movement in it is re-checked against the
    state this run just computed: is its destination still a candidate, is its
    road still reliable enough, has its origin moved into the immediate tier.
    """
    from astra.engines.optimizer_service import baseline_plan

    standing, _ = baseline_plan()
    threshold = MODEL_CONFIG.route.min_reliability_threshold.value
    suitable = {entry.site.id for entry in capacity if entry.suitable}
    phases = {row.habitation.id: row.phase for row in priority.rows}
    baseline_phases = _baseline_phases()

    reasons: list[str] = []
    invalidated: list[dict[str, Any]] = []
    people = 0

    for movement in standing.assignments:
        causes: list[str] = []
        if movement.site_id not in suitable:
            causes.append("the destination no longer clears every suitability gate")
        pair = routes.pair(movement.habitation_id, movement.site_id)
        if pair is None or not pair.feasible:
            causes.append(
                "the route to it has dropped below the "
                f"{threshold * 100:.0f}% reliability threshold"
            )
        elif pair.best.reliability < movement.route_reliability - 0.05:
            causes.append(
                f"route reliability has fallen from {movement.route_reliability:.0%} "
                f"to {pair.best.reliability:.0%}"
            )
        moved_up = (
            phases.get(movement.habitation_id) is PhaseTier.IMMEDIATE
            and baseline_phases.get(movement.habitation_id) is not PhaseTier.IMMEDIATE
        )
        if moved_up:
            causes.append("its residents have moved into the immediate tier")
        if not causes:
            continue
        people += movement.people
        invalidated.append(
            {
                "habitation_id": movement.habitation_id,
                "site_id": movement.site_id,
                "phase": movement.phase.value,
                "people": movement.people,
                "reasons": causes,
            }
        )

    if invalidated:
        reasons = sorted({cause for entry in invalidated for cause in entry["reasons"]})

    newly_immediate = [
        row
        for row in priority.rows
        if row.phase is PhaseTier.IMMEDIATE
        and baseline_phases.get(row.habitation.id) is not PhaseTier.IMMEDIATE
    ]
    newly_immediate_population = sum(row.habitation.population for row in newly_immediate)

    required = bool(invalidated) or bool(newly_immediate)
    if required:
        parts = []
        if invalidated:
            parts.append(
                f"{len(invalidated)} movement(s) covering {people:,} residents are "
                "no longer supported by the evidence"
            )
        if newly_immediate:
            parts.append(
                f"{len(newly_immediate)} habitation(s) totalling "
                f"{newly_immediate_population:,} residents have entered the "
                "immediate tier"
            )
        headline = "Plan requires review: " + "; ".join(parts) + "."
    else:
        headline = (
            "The standing plan still holds under this evidence: every movement's "
            "destination remains suitable and every route remains above the "
            "reliability threshold."
        )

    return PlanReview(
        required=required,
        reasons=reasons
        + (
            [
                f"{len(newly_immediate)} habitation(s) have entered the immediate tier"
            ]
            if newly_immediate
            else []
        ),
        invalidated_movements=invalidated,
        people_affected=people + newly_immediate_population,
        headline=headline,
    )


def _baseline_phases() -> dict[str, PhaseTier]:
    from astra.api.priority_router import baseline_priority

    return {row.habitation.id: row.phase for row in baseline_priority().rows}


# ---------------------------------------------------------------------------
# The run registry
# ---------------------------------------------------------------------------


class RunRegistry:
    """Runs in flight and recently completed, with the live event log.

    One instance per process. The log is the source of truth for live state: the
    standing picture is always ``baseline + every event in the log``, so it can
    be rebuilt from the log alone and a reset is genuinely a reset.
    """

    def __init__(self) -> None:
        self.log = EventLog()
        self._runs: dict[str, Run] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._counter = 0
        self.latest_completed: Run | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self, events: list[HazardEvent], *, trigger: str) -> Run:
        """Register the events, create a run, and execute it on a worker thread."""
        with self._lock:
            self._counter += 1
            run_id = f"run-{self._counter:04d}"
        for event in events:
            self.log.add(event)
        run = Run(
            id=run_id,
            trigger=trigger,
            created_at=datetime.now(tz=UTC),
            events=list(events),
        )
        with self._lock:
            self._runs[run_id] = run
            self._order.append(run_id)
            self._evict()
        thread = threading.Thread(
            target=self._execute, args=(run,), name=f"astra-{run_id}", daemon=True
        )
        thread.start()
        return run

    def _execute(self, run: Run) -> None:
        execute(run, self.log)
        if run.status is RunStatus.COMPLETED:
            self.latest_completed = run

    def _evict(self) -> None:
        while len(self._order) > MAX_RUNS:
            stale = self._order.pop(0)
            run = self._runs.pop(stale, None)
            if run is not None and run is self.latest_completed:
                # Never evict the run whose outputs the live screens are showing.
                # The history then sits one over the bound until a newer run
                # completes, which is the right trade: a bound is worth less than
                # a screen that can still explain the number it is displaying.
                self._runs[stale] = run
                self._order.insert(0, stale)
                break

    # -- reading -----------------------------------------------------------

    def get(self, run_id: str) -> Run | None:
        with self._lock:
            return self._runs.get(run_id)

    def recent(self, limit: int = 10) -> list[Run]:
        with self._lock:
            ids = list(reversed(self._order))[:limit]
        return [self._runs[run_id] for run_id in ids if run_id in self._runs]

    def reset(self) -> None:
        """Back to the baseline. The log is cleared and the history discarded."""
        self.log.clear()
        with self._lock:
            self._runs.clear()
            self._order.clear()
            self._counter = 0
        self.latest_completed = None

    # -- streaming ---------------------------------------------------------

    def follow(
        self, run: Run, *, poll_s: float = 0.05, timeout_s: float = 180.0
    ) -> Iterator[StageEvent]:
        """Every stage event of a run, from the first, then as they arrive.

        Replaying from the beginning is deliberate: a client that connects after
        the run started - which is every client, since the run starts on the
        POST that created it - must still see the whole execution.
        """
        index = 0
        deadline = time.monotonic() + timeout_s
        while True:
            pending = run.events_from(index)
            yield from pending
            index += len(pending)
            if run.terminal and index >= len(run.stages):
                return
            if time.monotonic() > deadline:
                return
            time.sleep(poll_s)


_REGISTRY: RunRegistry | None = None


def get_registry() -> RunRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = RunRegistry()
    return _REGISTRY


def current_state() -> tuple[RiskRun, Run | None]:
    """The hazard state the live screens should show, and the run that made it.

    With no events ingested this is the baseline, and the caller is told so by
    getting ``None`` for the run. ASTRA does not manufacture a live state before
    anything has happened.
    """
    registry = get_registry()
    run = registry.latest_completed
    if run is None or run.outputs is None:
        return baseline_risk(), None
    return run.outputs.risk, run


def baseline_scenario_id() -> str:
    return BASELINE.id
