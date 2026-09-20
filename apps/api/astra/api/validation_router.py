"""The credibility layer: back-test, weight sensitivity and confidence (§7).

`GET /validation` serves the artifact `scripts/backtest.py` produced. It is not
recomputed per request - a thousand Monte Carlo runs is half a minute of work,
and a figure that shifted between two page loads would be worse than useless as
evidence. What the endpoint does instead is say **when** it was computed and
**against which model configuration**, and refuse to present a stale artifact as
current: if the config has moved since the run, the response says so in the
response itself rather than leaving a judge to compare version strings.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from astra.api.schemas import ValidationResponse
from astra.domain.model_config import MODEL_CONFIG
from astra.domain.notices import DECISION_AUTHORITY
from astra.settings import get_settings

router = APIRouter(tags=["validation"])

ARTIFACT = "validation.json"


@lru_cache(maxsize=1)
def _load(path_key: str) -> dict:
    return json.loads(Path(path_key).read_text(encoding="utf-8"))


@router.get("/validation", response_model=ValidationResponse)
def validation() -> ValidationResponse:
    """Back-test, sensitivity and confidence, as the last run produced them."""
    path = get_settings().derived_dir / ARTIFACT
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail=(
                "the validation artifact has not been built; run "
                "scripts/backtest.py. ASTRA serves no validation figures rather "
                "than plausible ones"
            ),
        )
    payload = _load(str(path))

    stale = (
        payload.get("model_config_version") != MODEL_CONFIG.version
        or payload.get("engine_version") != MODEL_CONFIG.engine_version
    )
    return ValidationResponse(
        **payload,
        current_model_config_version=MODEL_CONFIG.version,
        current_engine_version=MODEL_CONFIG.engine_version,
        stale=stale,
        staleness_note=(
            (
                f"These figures were computed against model config "
                f"{payload.get('model_config_version')} / engine "
                f"{payload.get('engine_version')}, and the running system is on "
                f"{MODEL_CONFIG.version} / {MODEL_CONFIG.engine_version}. Re-run "
                "scripts/backtest.py before quoting them."
            )
            if stale
            else (
                "Computed against the model configuration and engine version this "
                "system is running."
            )
        ),
        decision_authority=DECISION_AUTHORITY,
    )


@router.get("/risk/overlay/confidence.png", response_class=FileResponse)
def confidence_overlay() -> FileResponse:
    """The evidence-confidence surface as a hatch, drawn over the hazard layer."""
    path = get_settings().derived_dir / "confidence.png"
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail="confidence overlay is not built; run scripts/build_hazard.py",
        )
    return FileResponse(path, media_type="image/png")
