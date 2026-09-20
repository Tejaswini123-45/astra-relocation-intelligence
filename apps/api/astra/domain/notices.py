"""The standing notices ASTRA is required to show, defined once and served.

CLAUDE.md sections 2.1, 2.2 and 4.1 mandate specific, unambiguous framing text.
The frontend must never retype these strings: it renders exactly what the API
returns, so the wording cannot drift between a screen, a printed brief and this
repository.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

HOW_THIS_WORKS = (
    "Every red-zone boundary, capacity figure and priority ranking in ASTRA is "
    "produced by a documented, auditable formula or a constraint solver. AI is used "
    "only to explain those outputs in plain language, never to produce them."
)

DECISION_AUTHORITY = (
    "Decision-support output. Final relocation decisions rest with the SDMA / "
    "District Authority."
)

CLASSIFICATION_LABEL = "ASTRA analytical classification"

SCENARIO_DISCLAIMER = (
    "Demonstration scenario. Habitation and site records are synthetic and "
    "terrain-calibrated. Not an official hazard designation of any real settlement."
)

SITE_TENURE_LIMITATION = (
    "ASTRA narrows the candidate search using terrain, land cover and service data. "
    "It does not verify land ownership, tenure or encumbrance - that requires an "
    "SDMA field survey."
)

PRIORITY_NOT_PROBABILITY = (
    "Priority is a ranking score, not a probability. Evidence confidence is computed "
    "separately and reported alongside it."
)

HISTORY_NOT_PREDICTION = (
    "Historical incident density is one weighted factor in the susceptibility model. "
    "It is not treated as proof of future hazard."
)


class Notices(BaseModel):
    """The full notice set, served with the model config and rendered verbatim."""

    model_config = ConfigDict(frozen=True)

    how_this_works: str = HOW_THIS_WORKS
    decision_authority: str = DECISION_AUTHORITY
    classification_label: str = CLASSIFICATION_LABEL
    scenario_disclaimer: str = SCENARIO_DISCLAIMER
    site_tenure_limitation: str = SITE_TENURE_LIMITATION
    priority_not_probability: str = PRIORITY_NOT_PROBABILITY
    history_not_prediction: str = HISTORY_NOT_PREDICTION


NOTICES = Notices()
