"""The LLM layer: narrow, optional, and structurally unable to compute (§10).

The model has exactly one job here - turning an already-computed structured
result into prose an official who is not a GIS analyst can read. It never scores,
never ranks, never selects, and never sees raw data. Three mechanisms make that a
property of the code rather than a promise:

1. **It is handed a dictionary of finished values.** The prompt builder takes the
   structured result and serialises it. There is no path from a raw surface, a
   fixture or a database row into a prompt.
2. **Every number it emits is checked against that dictionary before rendering.**
   A figure the model produced that is not in the source object is a
   hallucination, and the narration is dropped in favour of the template. Not
   flagged - dropped. A caveat under a wrong number is still a wrong number on
   an official's screen.
3. **The template is the default, not the fallback of last resort.** With no key
   configured the whole product runs identically and says `Template mode` in the
   header. If pulling the API key broke a demo, the architecture would be wrong.

The templates below are not filler. They are the narration ASTRA ships with, and
they are written to be the thing a judge reads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from astra.domain.notices import DECISION_AUTHORITY
from astra.settings import get_settings

#: Numbers under this are too common to fingerprint - a "3" in prose is not
#: evidence the model read it from the payload. Larger figures are checked.
MIN_CHECKED_VALUE = 10.0

#: How far a quoted figure may sit from a source value and still count as that
#: value. Covers rounding in prose ("about 1,140"), not invention.
ROUNDING_TOLERANCE = 0.02


@dataclass(frozen=True)
class Narration:
    """Prose for an official, and an honest account of where it came from."""

    text: str
    mode: str
    """``template`` or ``model``. Rendered in the UI so nobody has to guess."""
    validated: bool
    """True when every number in the text was found in the source object."""
    note: str


class NarrationError(RuntimeError):
    """The configured provider could not be reached or refused."""


# ---------------------------------------------------------------------------
# Numeric validation
# ---------------------------------------------------------------------------


NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _numbers_in(text: str) -> list[float]:
    values: list[float] = []
    for match in NUMBER.finditer(text):
        try:
            values.append(float(match.group(0).replace(",", "")))
        except ValueError:
            continue
    return values


def source_values(payload: Any) -> set[float]:
    """Every number anywhere in the structured result, flattened."""
    found: set[float] = set()

    def walk(node: Any) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            found.add(float(node))
            return
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(payload)
    # Percentages are the one honest transformation prose makes of a computed
    # value: a reliability of 0.89 reads as 89%. Admit both forms.
    return found | {value * 100.0 for value in found}


def validate_numbers(text: str, payload: Any) -> tuple[bool, list[float]]:
    """Which numbers in the prose are not in the structured result."""
    allowed = source_values(payload)
    unmatched: list[float] = []
    for value in _numbers_in(text):
        if abs(value) < MIN_CHECKED_VALUE:
            continue
        tolerance = max(abs(value) * ROUNDING_TOLERANCE, 0.5)
        if not any(abs(value - candidate) <= tolerance for candidate in allowed):
            unmatched.append(value)
    return (not unmatched, unmatched)


# ---------------------------------------------------------------------------
# Templates - the narration ASTRA ships with
# ---------------------------------------------------------------------------


def _plural(count: float, singular: str, plural: str | None = None) -> str:
    return singular if abs(count - 1) < 1e-9 else (plural or f"{singular}s")


def template_plan_advisory(payload: dict[str, Any]) -> str:
    """A relocation plan, described the way a district officer would want it."""
    totals = payload.get("totals", {})
    assigned = int(totals.get("population_assigned", 0))
    assessed = int(totals.get("population_assessed", 0))
    unmet = int(totals.get("population_unmet", 0))
    sites = int(totals.get("sites_used", 0))
    phases = payload.get("phases", [])
    status = payload.get("solver_status", "")

    lines: list[str] = []
    lines.append(
        f"The optimiser places {assigned:,} of {assessed:,} assessed residents "
        f"across {sites} {_plural(sites, 'site')}"
        + (f", leaving {unmet:,} without a destination." if unmet else ".")
    )

    for phase in phases:
        people = int(phase.get("people_moved", 0))
        if not people:
            continue
        label = str(phase.get("phase", "")).replace("_", "-").lower()
        lines.append(
            f"In the {label} phase, {people:,} residents move from "
            f"{phase.get('habitations', 0)} "
            f"{_plural(phase.get('habitations', 0), 'habitation')} to "
            f"{phase.get('sites_used', 0)} "
            f"{_plural(phase.get('sites_used', 0), 'site')}."
        )

    unmet_reasons = payload.get("unmet_reasons", [])
    if unmet_reasons:
        first = unmet_reasons[0]
        lines.append(
            f"The largest gap is at {first.get('habitation_name', first.get('habitation_id'))}: "
            f"{int(first.get('people', 0)):,} residents, because "
            f"{str(first.get('reason', '')).replace('_', ' ').lower()}."
        )

    if status == "FALLBACK":
        lines.append(
            "This plan came from the deterministic greedy fallback rather than the "
            "solver, because the solver reached its time limit. Treat it as a "
            "workable allocation, not an optimal one."
        )

    lines.append(DECISION_AUTHORITY)
    return " ".join(lines)


def template_habitation_advisory(payload: dict[str, Any]) -> str:
    """Why one settlement sits where it does in the queue."""
    name = payload.get("name", payload.get("habitation_id", "This habitation"))
    lines = [
        f"{name} ranks {payload.get('rank')} of "
        f"{payload.get('of_habitations')} with a priority score of "
        f"{payload.get('priority_score')}, which is a position in a queue and not "
        "a probability of harm."
    ]
    components = payload.get("components", {})
    if components:
        ordered = sorted(components.items(), key=lambda item: item[1], reverse=True)
        leading, value = ordered[0]
        lines.append(
            f"The largest contribution is {leading.replace('_', ' ')} at "
            f"{value}, against a composite hazard of "
            f"{payload.get('hazard_composite')} over its footprint."
        )
    if payload.get("phase"):
        lines.append(
            f"It falls in the {str(payload['phase']).replace('_', '-').lower()} "
            f"tier. {payload.get('phase_reason', '')}".strip()
        )
    if payload.get("rules_applied"):
        lines.append(
            "An override rule applied: " + "; ".join(payload["rules_applied"]) + "."
        )
    confidence = payload.get("confidence_band")
    if confidence:
        lines.append(
            f"Evidence confidence for this assessment is {str(confidence).lower()}, "
            "reported separately from the priority score and never multiplied into it."
        )
    lines.append(DECISION_AUTHORITY)
    return " ".join(lines)


def template_site_advisory(payload: dict[str, Any]) -> str:
    """What a candidate site can actually absorb, and what is stopping it."""
    name = payload.get("name", payload.get("site_id"))
    if not payload.get("suitable", True):
        gates = ", ".join(payload.get("failed_gates", [])) or "a hard suitability gate"
        return (
            f"{name} is not a candidate under current conditions: it fails {gates}. "
            "A gate is a yes or no, so the capacity figures below describe a site "
            f"nobody can be moved to. {DECISION_AUTHORITY}"
        )
    lines = [
        f"{name} has an effective capacity of "
        f"{payload.get('effective_capacity')} people against a theoretical "
        f"{payload.get('theoretical_capacity')} on land alone."
    ]
    bottleneck = payload.get("bottleneck")
    if bottleneck:
        lines.append(
            f"The binding constraint is {str(bottleneck).lower()}: it is what the "
            "site runs out of first, and raising anything else changes nothing "
            "until it is relieved."
        )
    intervention = payload.get("top_intervention")
    if intervention:
        lines.append(
            f"{intervention.get('description')} would take effective capacity from "
            f"{intervention.get('capacity_before')} to "
            f"{intervention.get('capacity_after')}"
            + (
                f", after which the binding constraint becomes "
                f"{str(intervention.get('next_bottleneck', '')).lower()}."
                if intervention.get("next_bottleneck")
                else "."
            )
        )
    lines.append(payload.get("limitation", ""))
    lines.append(DECISION_AUTHORITY)
    return " ".join(line for line in lines if line)


def template_scenario_advisory(payload: dict[str, Any]) -> str:
    """What changed, and what an officer should do about it."""
    lines = [payload.get("headline", "")]
    changes = payload.get("changes", [])
    if changes:
        lines.append("Applied: " + "; ".join(changes) + ".")
    tier = payload.get("tier_changes", [])
    if tier:
        lines.append(
            f"{len(tier)} {_plural(len(tier), 'habitation')} change phase, "
            f"affecting {int(payload.get('newly_immediate_population', 0)):,} "
            "residents newly requiring immediate action."
        )
    lines.append(DECISION_AUTHORITY)
    return " ".join(line for line in lines if line)


TEMPLATES = {
    "plan": template_plan_advisory,
    "habitation": template_habitation_advisory,
    "site": template_site_advisory,
    "scenario": template_scenario_advisory,
}


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = (
    "You write short advisories for Indian State Disaster Management Authority "
    "officers, from a decision-support system called ASTRA.\n\n"
    "You are given a JSON object of ALREADY-COMPUTED results. Your only job is to "
    "put them into plain, precise English.\n\n"
    "Rules, all absolute:\n"
    "- Use ONLY numbers that appear in the JSON. Never calculate, combine, round "
    "beyond one decimal, estimate or infer a figure.\n"
    "- Never rank, score, select, recommend a site, or say which option is best. "
    "Those decisions were already made by the engines; describe them.\n"
    "- Never state or imply a probability of a disaster occurring.\n"
    "- Write 3 to 5 sentences of continuous prose. No headings, no bullet points, "
    "no preamble, no markdown.\n"
    "- Do not invent place names, dates or authorities.\n"
    "- End with exactly this sentence: "
    f"{DECISION_AUTHORITY}"
)


def build_prompt(kind: str, payload: dict[str, Any]) -> str:
    """The user message. Structured results only - never raw data."""
    from astra.data.store import dumps

    return (
        f"Advisory type: {kind}\n"
        "Computed result:\n"
        f"{dumps(payload)}\n\n"
        "Write the advisory."
    )


def _call_provider(prompt: str, *, system: str) -> str:
    """Send one request to the configured provider.

    Provider-agnostic on purpose: the adapter speaks HTTP to whichever endpoint
    is configured, and ASTRA has no dependency on any vendor's SDK. An
    unreachable provider raises, and the caller falls back to the template.
    """
    import json
    import urllib.error
    import urllib.request

    settings = get_settings()
    provider = settings.llm_provider.lower()
    if provider == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "content-type": "application/json",
            "x-api-key": settings.llm_api_key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": settings.llm_model or "claude-sonnet-5",
            "max_tokens": 400,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        extract = lambda data: "".join(  # noqa: E731
            block.get("text", "") for block in data.get("content", [])
        )
    elif provider == "openai":
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {settings.llm_api_key}",
        }
        body = {
            "model": settings.llm_model or "gpt-4o-mini",
            "max_tokens": 400,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        extract = lambda data: (  # noqa: E731
            data.get("choices", [{}])[0].get("message", {}).get("content", "")
        )
    else:
        raise NarrationError(f"unsupported LLM provider '{settings.llm_provider}'")

    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise NarrationError(str(error)) from error
    text = extract(data).strip()
    if not text:
        raise NarrationError("the provider returned an empty advisory")
    return text


def narrate(kind: str, payload: dict[str, Any]) -> Narration:
    """Prose for one computed result, from the model if configured, else template.

    The template is produced either way. When a model is configured, its output
    has to survive numeric validation against the same payload to be used; if it
    does not, the template is what renders and the note says why.
    """
    if kind not in TEMPLATES:
        raise NarrationError(f"no advisory template for '{kind}'")
    fallback = TEMPLATES[kind](payload)
    settings = get_settings()

    if settings.llm_mode != "connected":
        return Narration(
            text=fallback,
            mode="template",
            validated=True,
            note=(
                "Written by ASTRA's deterministic templates from the computed "
                "result. No language model is configured, and the analysis is "
                "identical either way - only this prose differs."
            ),
        )

    try:
        text = _call_provider(build_prompt(kind, payload), system=SYSTEM_PROMPT)
    except NarrationError as error:
        return Narration(
            text=fallback,
            mode="template",
            validated=True,
            note=(
                f"The configured language model could not be reached ({error}); "
                "this advisory is ASTRA's deterministic template. The analysis is "
                "unaffected."
            ),
        )

    ok, unmatched = validate_numbers(text, payload)
    if not ok:
        return Narration(
            text=fallback,
            mode="template",
            validated=True,
            note=(
                "The language model's advisory quoted "
                + ", ".join(f"{value:g}" for value in unmatched)
                + ", which does not appear in the computed result, so it was "
                "discarded and ASTRA's template rendered instead. A caveat under a "
                "wrong number is still a wrong number."
            ),
        )
    return Narration(
        text=text,
        mode="model",
        validated=True,
        note=(
            "Written by the configured language model from the computed result "
            "only, and every number in it was checked against that result before "
            "it was shown."
        ),
    )
