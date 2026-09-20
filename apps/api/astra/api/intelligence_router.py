"""Evidence, the decision ledger, human override, narration and the ask layer.

The screens behind these endpoints are where ASTRA stops computing and a person
takes over. Every one of them is written so that the human act is the record and
the computation is the evidence beside it - never the other way round.
"""

from __future__ import annotations

import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from astra.api.schemas import (
    AskRequest,
    AskResponse,
    DecisionListResponse,
    DecisionResponse,
    EvidenceListResponse,
    EvidenceRecordResponse,
    IntentResponse,
    NarrationResponse,
    OverrideRequest,
    OverrideResponse,
    PromoteEvidenceRequest,
    RecordDecisionRequest,
)
from astra.data.scenarios import BASELINE
from astra.domain.model_config import MODEL_CONFIG
from astra.domain.notices import DECISION_AUTHORITY
from astra.engines import ask as ask_engine
from astra.engines import evidence as evidence_engine
from astra.engines.audit import (
    AuditError,
    DecisionRecord,
    OverrideRecord,
    get_decision,
    list_decisions,
    record_decision,
    record_override,
)
from astra.engines.narration import Narration, narrate
from astra.settings import get_settings

router = APIRouter(tags=["intelligence"])

MAX_PHOTO_BYTES = 8 * 1024 * 1024
ALLOWED_PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp"}


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def _photo_dir() -> Path:
    path = get_settings().data_dir / "evidence"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _serialise_evidence(record) -> EvidenceRecordResponse:
    return EvidenceRecordResponse(**vars(record))


@router.post("/evidence", response_model=EvidenceRecordResponse, status_code=201)
async def file_evidence(
    text: str = Form(..., min_length=3, max_length=4000),
    reporter: str = Form(..., min_length=1, max_length=120),
    role: str | None = Form(default=None, max_length=120),
    observed_at: str | None = Form(default=None),
    lon: float | None = Form(default=None),
    lat: float | None = Form(default=None),
    habitation_id: str | None = Form(default=None),
    site_id: str | None = Form(default=None),
    segment_id: str | None = Form(default=None),
    analyst_note: str | None = Form(default=None, max_length=1000),
    photo: UploadFile | None = File(default=None),
) -> EvidenceRecordResponse:
    """File one field report, with an optional photograph.

    The report is classified by rules that run with no key configured, and the
    classification is stored beside the text with the reasoning that produced it.
    Filing does not change any assessment: promoting a report to a live
    observation is a separate, explicit act.
    """
    stored_path: str | None = None
    stored_name: str | None = None
    if photo is not None and photo.filename:
        content_type = photo.content_type or mimetypes.guess_type(photo.filename)[0]
        if content_type not in ALLOWED_PHOTO_TYPES:
            raise HTTPException(
                status_code=415,
                detail=(
                    f"'{content_type}' is not an accepted photograph format; "
                    "ASTRA stores JPEG, PNG or WebP"
                ),
            )
        payload = await photo.read()
        if len(payload) > MAX_PHOTO_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"the photograph is {len(payload) / 1e6:.1f} MB; the limit is "
                    f"{MAX_PHOTO_BYTES / 1e6:.0f} MB"
                ),
            )
        suffix = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }[content_type]
        # The stored name is ASTRA's own, never the uploader's: a filename from
        # outside the system has no business deciding a path inside it.
        from astra.data.store import new_id

        stored_name = Path(photo.filename).name[:120]
        target = _photo_dir() / f"{new_id('PH')}{suffix}"
        target.write_bytes(payload)
        stored_path = str(target)

    observed = None
    if observed_at:
        try:
            observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail=f"'{observed_at}' is not an ISO 8601 timestamp",
            ) from error

    try:
        record = evidence_engine.file_evidence(
            text=text,
            reporter=reporter,
            role=role,
            observed_at=observed,
            lon=lon,
            lat=lat,
            habitation_id=habitation_id,
            site_id=site_id,
            segment_id=segment_id,
            analyst_note=analyst_note,
            photo_path=stored_path,
            photo_name=stored_name,
        )
    except evidence_engine.EvidenceError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _serialise_evidence(record)


@router.get("/evidence", response_model=EvidenceListResponse)
def evidence_list(limit: int = 50) -> EvidenceListResponse:
    """Filed field reports, newest first."""
    records = evidence_engine.list_evidence(limit)
    return EvidenceListResponse(
        evidence=[_serialise_evidence(record) for record in records],
        total=len(records),
        kinds=list(evidence_engine.EVIDENCE_KINDS),
        extraction_note=(
            "Classification is by documented keyword and pattern rules that run "
            "with no language model configured. Where a model is configured it may "
            "refine the severity, but it may not change the hazard classification: "
            "a model that reclassifies a landslide report as a flood has changed a "
            "fact, not a phrasing."
        ),
        decision_authority=DECISION_AUTHORITY,
    )


@router.get("/evidence/{evidence_id}", response_model=EvidenceRecordResponse)
def evidence_detail(evidence_id: str) -> EvidenceRecordResponse:
    record = evidence_engine.get_evidence(evidence_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no evidence '{evidence_id}'")
    return _serialise_evidence(record)


@router.get("/evidence/{evidence_id}/photo", response_class=FileResponse)
def evidence_photo(evidence_id: str) -> FileResponse:
    """The photograph filed with one report."""
    record = evidence_engine.get_evidence(evidence_id)
    if record is None or not record.photo_path:
        raise HTTPException(
            status_code=404, detail=f"no photograph filed with '{evidence_id}'"
        )
    path = Path(record.photo_path)
    # The stored path was minted by ASTRA, but it is checked against the store's
    # own directory before it is served: a path that escaped that directory would
    # be a way to read the filesystem through an API that has no such business.
    try:
        path.resolve().relative_to(_photo_dir().resolve())
    except ValueError as error:
        raise HTTPException(status_code=404, detail="photograph not found") from error
    if not path.exists():
        raise HTTPException(status_code=404, detail="photograph not found")
    return FileResponse(path)


@router.post("/evidence/{evidence_id}/promote", status_code=202)
def promote_evidence(evidence_id: str, request: PromoteEvidenceRequest) -> dict[str, Any]:
    """Turn a filed report into a live observation and run the pipeline on it.

    Deliberately separate from filing. A report is a record of what someone saw;
    acting on it is a decision, and the ledger records which report produced which
    event.
    """
    from astra.domain.enums import EventType
    from astra.engines.live import LiveIngestError, make_event
    from astra.engines.pipeline import get_registry

    record = evidence_engine.get_evidence(evidence_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no evidence '{evidence_id}'")
    if record.ingested_event_id:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{evidence_id}' was already ingested as "
                f"{record.ingested_event_id}; filing the same observation twice "
                "would count one event as two"
            ),
        )

    suggested = record.extracted.get("suggested_event")
    if not suggested:
        raise HTTPException(
            status_code=422,
            detail=(
                "this report was not classified into a hazard ASTRA models, so "
                "there is no observation to promote it to. It stays on the record."
            ),
        )
    value = (
        request.value
        if request.value is not None
        else record.extracted.get("suggested_value")
    )
    if value is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "this report states no measurement ASTRA can act on. Supply the "
                "value explicitly if you want it ingested."
            ),
        )

    registry = get_registry()
    try:
        event = make_event(
            kind=EventType(suggested),
            value=float(value),
            event_id=registry.log.next_id(),
            lon=record.lon,
            lat=record.lat,
            radius_m=request.radius_m,
            target=record.segment_id,
            source=f"{record.reporter} (field report {record.id})",
            observed_at=datetime.fromisoformat(record.observed_at),
            note=record.text[:400],
        )
    except (LiveIngestError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    run = registry.start([event], trigger=f"evidence {record.id}")
    evidence_engine.mark_ingested(record.id, event.id)
    return {
        "evidence_id": record.id,
        "event_id": event.id,
        "run_id": run.id,
        "kind": suggested,
        "value": float(value),
        "note": (
            "The report is now a live observation. The pipeline is running on it; "
            "follow it on the run stream."
        ),
    }


# ---------------------------------------------------------------------------
# The decision ledger
# ---------------------------------------------------------------------------


def _serialise_override(record: OverrideRecord) -> OverrideResponse:
    return OverrideResponse(**vars(record))


def _serialise_decision(record: DecisionRecord) -> DecisionResponse:
    payload = vars(record) | {
        "overrides": [_serialise_override(entry) for entry in record.overrides],
        "decision_authority": DECISION_AUTHORITY,
    }
    return DecisionResponse(**payload)


@router.post("/decisions", response_model=DecisionResponse, status_code=201)
def create_decision(request: RecordDecisionRequest) -> DecisionResponse:
    """Write the current computed plan into the ledger as a decision point."""
    from astra.api.priority_router import baseline_priority
    from astra.engines.capacity_service import baseline_capacity
    from astra.engines.optimizer_service import baseline_plan
    from astra.engines.routes_service import baseline_routes

    plan, inputs = baseline_plan()
    record = record_decision(
        scenario_id=BASELINE.id,
        trigger=request.trigger,
        plan=plan,
        plan_inputs=inputs,
        priority=baseline_priority(),
        capacity=baseline_capacity(),
        routes=baseline_routes(),
        notes=request.notes,
    )
    return _serialise_decision(record)


@router.get("/decisions", response_model=DecisionListResponse)
def decisions(limit: int = 25) -> DecisionListResponse:
    """The audit ledger, newest first."""
    records = list_decisions(limit)
    return DecisionListResponse(
        decisions=[_serialise_decision(record) for record in records],
        total=len(records),
        engine_version=MODEL_CONFIG.engine_version,
        model_config_version=MODEL_CONFIG.version,
        decision_authority=DECISION_AUTHORITY,
    )


@router.get("/decisions/{decision_id}", response_model=DecisionResponse)
def decision_detail(decision_id: str) -> DecisionResponse:
    record = get_decision(decision_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no decision '{decision_id}'")
    return _serialise_decision(record)


@router.get("/audit/{decision_id}", response_model=DecisionResponse)
def audit(decision_id: str) -> DecisionResponse:
    """The audit record for one decision. Same payload, the name a brief cites."""
    return decision_detail(decision_id)


@router.post(
    "/decisions/{decision_id}/override", response_model=DecisionResponse
)
def override(decision_id: str, request: OverrideRequest) -> DecisionResponse:
    """Record what a person decided, with the computed consequence beside it.

    For an action that changes an assignment, the consequence is not asserted -
    the plan is re-solved with that assignment forced and the ledger records what
    it actually cost, including whether it is feasible at all.
    """
    if get_decision(decision_id) is None:
        raise HTTPException(status_code=404, detail=f"no decision '{decision_id}'")

    consequence: dict[str, Any] = {}
    if request.action in ("FORCE_ASSIGNMENT", "REJECT_ASSIGNMENT"):
        consequence = _consequence_of(request)

    try:
        record_override(
            decision_id=decision_id,
            actor=request.actor,
            action=request.action,
            reason=request.reason,
            habitation_id=request.habitation_id,
            site_id=request.site_id,
            people=request.people,
            consequence=consequence,
        )
    except AuditError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    record = get_decision(decision_id)
    assert record is not None
    return _serialise_decision(record)


def _consequence_of(request: OverrideRequest) -> dict[str, Any]:
    """Re-solve with the override applied and report what it actually costs."""
    from astra.engines.optimizer_service import baseline_plan, counterfactual

    plan, inputs = baseline_plan()
    if request.habitation_id not in inputs.demand:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{request.habitation_id}' has no relocation demand in this plan, "
                "so there is nothing to redirect"
            ),
        )
    result = counterfactual(
        request.habitation_id,
        request.site_id,
        people=request.people,
        inputs=inputs,
        baseline=plan,
    )
    return {
        "habitation_id": result.habitation_id,
        "site_id": result.site_id,
        "people": result.people,
        "feasible": result.feasible,
        "reason": result.reason,
        "objective_baseline": result.objective_baseline,
        "objective_forced": result.objective_forced,
        "objective_delta": result.objective_delta,
        "assigned_elsewhere_before": result.assigned_elsewhere_before,
        "assigned_elsewhere_after": result.assigned_elsewhere_after,
        "displaced": [[other, count] for other, count in result.displaced],
        "headline": result.headline,
        "computed_by": "counterfactual re-solve through the same CP-SAT model",
    }


# ---------------------------------------------------------------------------
# Narration and the ask layer
# ---------------------------------------------------------------------------


def _narration(result: Narration) -> NarrationResponse:
    from astra.settings import get_settings as settings_of

    return NarrationResponse(
        text=result.text,
        mode=result.mode,
        validated=result.validated,
        note=result.note,
        llm_configured=settings_of().llm_mode == "connected",
        decision_authority=DECISION_AUTHORITY,
    )


@router.get("/narrate/plan", response_model=NarrationResponse)
def narrate_plan() -> NarrationResponse:
    """The current plan, in prose, for an official who is not a GIS analyst."""
    from astra.api.plan_router import serialise_plan
    from astra.engines.optimizer_service import baseline_plan
    from astra.engines.routes_service import baseline_routes

    plan, inputs = baseline_plan()
    payload = serialise_plan(plan, inputs, baseline_routes()).model_dump(mode="json")
    slim = {
        "totals": payload["totals"],
        "phases": payload["phases"],
        "unmet_reasons": payload["unmet"][:3],
        "solver_status": payload["status"],
    }
    return _narration(narrate("plan", slim))


@router.get("/narrate/habitation/{habitation_id}", response_model=NarrationResponse)
def narrate_habitation(habitation_id: str) -> NarrationResponse:
    try:
        payload = ask_engine._habitation_detail(habitation_id)
    except ask_engine.AskError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _narration(narrate("habitation", payload))


@router.get("/narrate/site/{site_id}", response_model=NarrationResponse)
def narrate_site(site_id: str) -> NarrationResponse:
    try:
        payload = ask_engine._site_capacity(site_id)
    except ask_engine.AskError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _narration(narrate("site", payload))


@router.get("/ask/intents", response_model=list[IntentResponse])
def intents() -> list[IntentResponse]:
    """Everything ASTRA can be asked. The interface shows this list up front."""
    return [IntentResponse(**entry) for entry in ask_engine.catalogue()]


@router.post("/ask", response_model=AskResponse)
def ask(request: AskRequest) -> AskResponse:
    """Answer one question from the allowlist, or refuse and say what is available.

    There is no query language here and no path to the filesystem, a shell or a
    database. A question selects one of a fixed set of functions, each of which
    reads already-computed state.
    """
    try:
        answer = ask_engine.ask(request.question, intent_id=request.intent_id)
    except ask_engine.AskError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return AskResponse(
        intent=answer.intent,
        question=answer.question,
        matched_on=answer.matched_on,
        entity=answer.entity,
        data=answer.data,
        text=answer.text,
        follow_up=answer.follow_up,
        decision_authority=answer.decision_authority,
        note=(
            "ASTRA answers a fixed set of questions by running the same engine "
            "accessors its screens use. Every figure above is on a screen too; "
            "nothing here is generated."
        ),
    )
