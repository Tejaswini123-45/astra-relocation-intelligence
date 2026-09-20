"""Live ingest, pipeline runs and the SSE stream (§5.8, §9).

``POST /events`` accepts observations, registers them, and starts a real pipeline
run on a worker thread. ``GET /runs/{id}/stream`` publishes that run's stage
events as Server-Sent Events, replayed from the first so a client that connects
after the run started - which is every client - sees the whole execution.

Nothing in here is a progress bar. Each SSE frame is a stage event appended by
the code that ran the stage, carrying what that stage computed. If the execution
graph in the interface shows a node lighting up, a stage started; if it shows a
warning, a stage emitted one.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from astra.api.plan_router import serialise_plan
from astra.api.risk_router import serialise_zones
from astra.api.schemas import (
    EventBatch,
    EventFeedResponse,
    EventResponse,
    FeedStepResponse,
    LiveStateResponse,
    PlanReviewResponse,
    RunListResponse,
    RunResponse,
    RunSummary,
    StageEventResponse,
    ZonesResponse,
)
from astra.data.feed import FeedError, load_feed
from astra.domain.enums import EventType, PhaseTier
from astra.domain.model_config import MODEL_CONFIG
from astra.domain.models import HazardEvent
from astra.domain.notices import CLASSIFICATION_LABEL, DECISION_AUTHORITY
from astra.engines.capacity_service import baseline_capacity
from astra.engines.live import LiveIngestError, make_event
from astra.engines.optimizer_service import baseline_plan
from astra.engines.pipeline import (
    STAGE_LABEL,
    STAGE_ORDER,
    PlanReview,
    Run,
    get_registry,
)
from astra.engines.routes_service import baseline_routes, corridor_network
from astra.engines.service import baseline_risk

router = APIRouter(tags=["live"])

STREAM_POLL_S = 0.05
"""How often the stream checks the run's buffer for new stage events. The run
itself is not paced by this; it is as fast as the engines are."""


# ---------------------------------------------------------------------------
# Serialisers
# ---------------------------------------------------------------------------


def _event(event: HazardEvent) -> EventResponse:
    return EventResponse(
        id=event.id,
        kind=event.kind,
        observed_at=event.observed_at,
        received_at=event.received_at,
        lon=event.lon,
        lat=event.lat,
        radius_m=event.radius_m,
        value=event.value,
        target=event.target,
        source=event.source,
        provenance=event.provenance,
        note=event.note,
        description=event.describe(),
    )


def _stage(event) -> StageEventResponse:
    return StageEventResponse(
        sequence=event.sequence,
        run_id=event.run_id,
        stage=event.stage,
        status=event.status,
        event=event.event_name,
        at=event.at,
        message=event.message,
        elapsed_ms=event.elapsed_ms,
        payload=event.payload,
    )


def _review(review: PlanReview | None) -> PlanReviewResponse | None:
    if review is None:
        return None
    return PlanReviewResponse(
        required=review.required,
        headline=review.headline,
        reasons=review.reasons,
        invalidated_movements=review.invalidated_movements,
        people_affected=review.people_affected,
        decision_authority=DECISION_AUTHORITY,
    )


def _run(run: Run) -> RunResponse:
    report = run.outputs.rescore if run.outputs else None
    return RunResponse(
        id=run.id,
        trigger=run.trigger,
        status=run.status,
        created_at=run.created_at,
        finished_at=run.finished_at,
        total_ms=run.total_ms,
        events=[_event(event) for event in run.events],
        stages=[_stage(event) for event in run.stages],
        stage_order=list(STAGE_ORDER),
        stage_labels={stage.value: label for stage, label in STAGE_LABEL.items()},
        error=run.error,
        review=_review(run.review),
        cells_rescored=report.cells_rescored if report else None,
        cells_in_grid=report.cells_in_grid if report else None,
        decision_id=run.decision_id,
        engine_version=MODEL_CONFIG.engine_version,
        model_config_version=MODEL_CONFIG.version,
    )


def _critical_area(zones) -> float:
    return round(
        sum(zone.area_km2 for zone in zones if zone.zone_class.value == "CRITICAL"), 3
    )


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


@router.post("/events", response_model=RunResponse, status_code=202)
def ingest(batch: EventBatch) -> RunResponse:
    """Accept observations and start a pipeline run over them.

    Returns as soon as the run is registered, with the run's id and its first
    stage events. The run itself continues on a worker thread; follow it on
    ``GET /runs/{id}/stream``.
    """
    registry = get_registry()
    segments = set(corridor_network().by_id)

    events: list[HazardEvent] = []
    for submission in batch.events:
        if submission.kind is EventType.INFRASTRUCTURE_STATUS:
            if not submission.target:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "an infrastructure-status event has to name the road segment "
                        "it reports on"
                    ),
                )
            if submission.target not in segments:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"unknown road segment '{submission.target}'; an "
                        "infrastructure report has to name a segment in the routed "
                        "network"
                    ),
                )
        try:
            events.append(
                make_event(
                    kind=submission.kind,
                    value=submission.value,
                    event_id=registry.log.next_id(),
                    lon=submission.lon,
                    lat=submission.lat,
                    radius_m=submission.radius_m,
                    target=submission.target,
                    source=submission.source,
                    observed_at=submission.observed_at,
                    note=submission.note,
                )
            )
        except LiveIngestError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    run = registry.start(events, trigger=batch.trigger)
    return _run(run)


@router.post("/live/reset", response_model=LiveStateResponse)
def reset() -> LiveStateResponse:
    """Discard every ingested event and return to the baseline.

    Live state is derived from the event log rather than edited in place, so
    clearing the log genuinely restores the baseline - there is no accumulated
    residue to leak into the next demonstration.
    """
    get_registry().reset()
    return live_state()


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@router.get("/runs", response_model=RunListResponse)
def runs(limit: int = 10) -> RunListResponse:
    """The recent pipeline runs, newest first."""
    registry = get_registry()
    return RunListResponse(
        runs=[
            RunSummary(
                id=run.id,
                trigger=run.trigger,
                status=run.status,
                created_at=run.created_at,
                total_ms=run.total_ms,
                events=len(run.events),
                plan_requires_review=bool(run.review and run.review.required),
            )
            for run in registry.recent(min(max(limit, 1), 25))
        ],
        events_ingested=len(registry.log.snapshot()),
        closed_segments=sorted(registry.log.closed_segments),
    )


@router.get("/runs/{run_id}", response_model=RunResponse)
def run_detail(run_id: str) -> RunResponse:
    """One run, with every stage event it has emitted so far."""
    run = get_registry().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
    return _run(run)


@router.get("/runs/{run_id}/stream")
async def run_stream(run_id: str, request: Request) -> StreamingResponse:
    """This run's stage events, as Server-Sent Events.

    Frames use the event names from CLAUDE.md section 9 - ``stage_started``,
    ``stage_progress``, ``stage_completed``, ``warning``, ``stage_failed`` - and
    the stream closes with a ``run_completed`` frame once the run is terminal, so
    a client knows the difference between "still working" and "finished".
    """
    registry = get_registry()
    run = registry.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run '{run_id}'")

    async def publish() -> AsyncIterator[str]:
        index = 0
        while True:
            if await request.is_disconnected():
                return
            pending = run.events_from(index)
            index += len(pending)
            for event in pending:
                yield _frame(event.event_name, _stage(event).model_dump(mode="json"))
            if run.terminal and index >= len(run.stages):
                yield _frame(
                    "run_completed",
                    {
                        "run_id": run.id,
                        "status": run.status.value,
                        "total_ms": run.total_ms,
                        "error": run.error,
                        "plan_requires_review": bool(
                            run.review and run.review.required
                        ),
                        "headline": run.review.headline if run.review else None,
                    },
                )
                return
            await asyncio.sleep(STREAM_POLL_S)

    return StreamingResponse(
        publish(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Proxies that buffer a response would turn a live stream into a
            # single delivery at the end, which is the one thing this endpoint
            # must not do.
            "X-Accel-Buffering": "no",
        },
    )


def _frame(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"


# ---------------------------------------------------------------------------
# Live state
# ---------------------------------------------------------------------------


@router.get("/live", response_model=LiveStateResponse)
def live_state() -> LiveStateResponse:
    """The standing live picture, against the baseline it moved from."""
    registry = get_registry()
    baseline = baseline_risk()
    run = registry.latest_completed

    base_immediate = sum(
        row.habitation.population
        for row in _baseline_priority().rows
        if row.phase is PhaseTier.IMMEDIATE
    )
    base_capacity = baseline_capacity()
    base_routes = baseline_routes()
    base_plan, _ = baseline_plan()

    if run is None or run.outputs is None:
        return LiveStateResponse(
            live=False,
            run_id=None,
            events_ingested=len(registry.log.snapshot()),
            events=[_event(event) for event in registry.log.snapshot()],
            closed_segments=sorted(registry.log.closed_segments),
            zones_baseline=len(baseline.zones),
            zones_now=len(baseline.zones),
            critical_area_km2_baseline=_critical_area(baseline.zones),
            critical_area_km2_now=_critical_area(baseline.zones),
            immediate_population_baseline=base_immediate,
            immediate_population_now=base_immediate,
            suitable_sites_baseline=sum(1 for e in base_capacity if e.suitable),
            suitable_sites_now=sum(1 for e in base_capacity if e.suitable),
            feasible_routes_baseline=sum(
                1 for pair in base_routes.pairs.values() if pair.feasible
            ),
            feasible_routes_now=sum(
                1 for pair in base_routes.pairs.values() if pair.feasible
            ),
            placed_baseline=base_plan.totals.population_assigned,
            placed_now=base_plan.totals.population_assigned,
            cells_rescored=0,
            cells_in_grid=baseline.grid.size,
            share_rescored=0.0,
            rescore_note=(
                "Nothing has been ingested. This is the baseline assessment, "
                "unchanged."
            ),
            review=None,
            headline=(
                "No live evidence ingested. ASTRA is showing the baseline "
                "assessment and says so rather than implying a live state."
            ),
            classification_label=CLASSIFICATION_LABEL,
            decision_authority=DECISION_AUTHORITY,
            engine_version=MODEL_CONFIG.engine_version,
            model_config_version=MODEL_CONFIG.version,
        )

    outputs = run.outputs
    now_immediate = sum(
        row.habitation.population
        for row in outputs.priority.rows
        if row.phase is PhaseTier.IMMEDIATE
    )
    return LiveStateResponse(
        live=True,
        run_id=run.id,
        events_ingested=len(registry.log.snapshot()),
        events=[_event(event) for event in registry.log.snapshot()],
        closed_segments=sorted(registry.log.closed_segments),
        zones_baseline=len(baseline.zones),
        zones_now=len(outputs.risk.zones),
        critical_area_km2_baseline=_critical_area(baseline.zones),
        critical_area_km2_now=_critical_area(outputs.risk.zones),
        immediate_population_baseline=base_immediate,
        immediate_population_now=now_immediate,
        suitable_sites_baseline=sum(1 for e in base_capacity if e.suitable),
        suitable_sites_now=sum(1 for e in outputs.capacity if e.suitable),
        feasible_routes_baseline=sum(
            1 for pair in base_routes.pairs.values() if pair.feasible
        ),
        feasible_routes_now=sum(
            1 for pair in outputs.routes.pairs.values() if pair.feasible
        ),
        placed_baseline=base_plan.totals.population_assigned,
        placed_now=outputs.plan.totals.population_assigned,
        cells_rescored=outputs.rescore.cells_rescored,
        cells_in_grid=outputs.rescore.cells_in_grid,
        share_rescored=round(outputs.rescore.share, 5),
        rescore_note=outputs.rescore.note,
        review=_review(run.review),
        headline=run.review.headline if run.review else "",
        classification_label=CLASSIFICATION_LABEL,
        decision_authority=DECISION_AUTHORITY,
        engine_version=MODEL_CONFIG.engine_version,
        model_config_version=MODEL_CONFIG.version,
    )


def _baseline_priority():
    from astra.api.priority_router import baseline_priority

    return baseline_priority()


@router.get("/live/zones", response_model=ZonesResponse)
def live_zones() -> ZonesResponse:
    """The analytical red zones as they stand now, live or baseline."""
    run = get_registry().latest_completed
    if run is None or run.outputs is None:
        return serialise_zones(baseline_risk())
    return serialise_zones(run.outputs.risk)


@router.get("/live/plan")
def live_plan():
    """The plan as it stands now, through the same serialiser the Plan screen uses."""
    run = get_registry().latest_completed
    if run is None or run.outputs is None:
        plan, inputs = baseline_plan()
        return serialise_plan(plan, inputs, baseline_routes())
    return serialise_plan(
        run.outputs.plan, run.outputs.plan_inputs, run.outputs.routes
    )


# ---------------------------------------------------------------------------
# The demonstration feed
# ---------------------------------------------------------------------------


@router.get("/events/feed", response_model=EventFeedResponse)
def event_feed() -> EventFeedResponse:
    """The scripted observation sequence a client replays against POST /events.

    Returned as *data to be posted*, not as results. Replaying it drives the same
    ingest endpoint an external feed would drive, and every stage a viewer sees
    afterwards is a real execution over the real engines.
    """
    try:
        feed = load_feed()
    except FeedError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return EventFeedResponse(
        id=feed.id,
        name=feed.name,
        description=feed.description,
        note=feed.note,
        provenance=feed.provenance,
        steps=[
            FeedStepResponse(
                index=index,
                delay_ms=step.delay_ms,
                kind=step.kind,
                lon=step.lon,
                lat=step.lat,
                radius_m=step.radius_m,
                value=step.value,
                target=_resolve_target(step.target),
                source=step.source,
                note=step.note,
            )
            for index, step in enumerate(feed.steps)
        ],
    )


def _resolve_target(target: str | None) -> str | None:
    """Resolve the feed's placeholder to the segment the plan actually leans on.

    The feed ships a placeholder rather than a hardcoded segment id so that the
    road it closes is the one the *current* solved plan depends on most, not one
    that happened to matter when the fixture was written.
    """
    if target != "$MOST_DEPENDED_ON_SEGMENT":
        return target
    from astra.api.routes_router import critical_segments

    listing = critical_segments()
    bridges = [row for row in listing.segments if row.is_bridge]
    chosen = bridges[0] if bridges else (listing.segments[0] if listing.segments else None)
    return chosen.segment_id if chosen else None
