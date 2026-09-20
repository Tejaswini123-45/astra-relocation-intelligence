"""The Decision Brief: preview, generate, retrieve.

``GET /brief/preview`` is the brief as it would print now and writes nothing -
the Command Centre reads it on every load. ``POST /brief`` is the deliberate act:
it writes a decision-ledger row and freezes the payload under an id the printed
page carries.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from astra.api.schemas import (
    BriefListResponse,
    BriefResponse,
    BriefSummary,
    GenerateBriefRequest,
)
from astra.domain.notices import DECISION_AUTHORITY
from astra.engines import brief as brief_engine

router = APIRouter(tags=["brief"])


@router.get("/brief/preview", response_model=BriefResponse)
def brief_preview() -> BriefResponse:
    """The brief for the standing state, without writing a ledger row."""
    return BriefResponse.model_validate(brief_engine.build(brief_engine.standing_state()))


@router.post("/brief", response_model=BriefResponse, status_code=201)
def generate_brief(request: GenerateBriefRequest) -> BriefResponse:
    """Generate a Decision Brief, record it in the ledger, and freeze it."""
    return BriefResponse.model_validate(brief_engine.generate(notes=request.notes))


@router.get("/briefs", response_model=BriefListResponse)
def briefs(limit: int = 10) -> BriefListResponse:
    """Generated briefs, newest first."""
    rows = brief_engine.recent(limit)
    return BriefListResponse(
        briefs=[BriefSummary(**row) for row in rows],
        total=len(rows),
        decision_authority=DECISION_AUTHORITY,
    )


@router.get("/brief/{brief_id}", response_model=BriefResponse)
def brief_detail(brief_id: str) -> BriefResponse:
    """One generated brief, exactly as it was when it was generated."""
    payload = brief_engine.get(brief_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"no brief '{brief_id}'")
    return BriefResponse.model_validate(payload)
