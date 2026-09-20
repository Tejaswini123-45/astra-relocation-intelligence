"""Field evidence: intake, structured extraction and the bridge into ingest.

A report arrives from someone standing on a hillside. It has a place, a time, a
person's name against it, usually a photograph, and a paragraph of prose. ASTRA
has to turn that into something the engines can act on without pretending the
prose said more than it did.

**Extraction is deterministic first.** A keyword-and-pattern classifier reads the
text for the hazard it describes, the severity words it uses and any measurement
it states. That runs with no key configured, it is inspectable, and its output is
what the engines consume. A language model, when configured, can *refine* the same
fields - and its result is accepted only where it agrees with the deterministic
reading on the hazard type, because a model that reclassifies a landslide report
as a flood has changed a fact, not a phrasing.

**Nothing is ingested silently.** Filing evidence stores it. Turning it into a
live observation that re-scores the corridor is a separate, explicit act, and the
record says which evidence produced which event.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from astra.data.store import dumps, loads, new_id, session
from astra.domain.enums import EventType, ProvenanceClass

#: What a field report can be about. Deliberately the same vocabulary the live
#: ingest speaks, so a report can become an observation without translation.
EVIDENCE_KINDS = (
    "LANDSLIDE",
    "FLOOD",
    "ROAD_BLOCKED",
    "STRUCTURAL_DAMAGE",
    "GROUND_INSTABILITY",
    "RAINFALL",
    "OTHER",
)

#: Keyword sets per kind. Ordered by specificity: the first kind whose keywords
#: appear wins, so "road blocked by a landslide" reads as a landslide that
#: blocked a road rather than as a generic closure.
KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "LANDSLIDE",
        (
            "landslide", "landslip", "slide", "slip", "debris flow", "rockfall",
            "boulder", "slope failure", "hillside collapsed", "mudslide",
        ),
    ),
    (
        "FLOOD",
        (
            "flood", "inundat", "water level", "river rose", "overflow",
            "submerged", "washed away", "waterlogg",
        ),
    ),
    (
        "GROUND_INSTABILITY",
        (
            "crack", "fissure", "subsid", "seepage", "settling", "tilting",
            "ground movement", "sinking",
        ),
    ),
    (
        "STRUCTURAL_DAMAGE",
        ("wall collapsed", "house collapsed", "damage", "roof", "foundation", "wall fell"),
    ),
    ("ROAD_BLOCKED", ("road blocked", "road closed", "impassable", "cut off", "bridge")),
    ("RAINFALL", ("rain", "rainfall", "downpour", "cloudburst", "mm of rain")),
)

#: Severity language, mapped to a 0-1 observed severity. These are ASTRA's own
#: reading of ordinary words, marked DEMO_CONFIG wherever they are shown.
SEVERITY_WORDS: tuple[tuple[float, tuple[str, ...]], ...] = (
    (0.95, ("catastrophic", "destroyed", "washed away", "fatalit", "buried", "killed")),
    (0.80, ("severe", "major", "collapsed", "impassable", "evacuat", "large")),
    (0.60, ("significant", "widening", "worsening", "blocked", "heavy", "extensive")),
    (0.40, ("moderate", "partial", "some damage", "visible")),
    (0.20, ("minor", "small", "hairline", "slight")),
)

MEASUREMENT = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mm|cm|m\b|metre|meter|km|inches|feet|ft)", re.IGNORECASE
)

#: How a classified report maps onto the live ingest vocabulary, when someone
#: chooses to promote it. Rainfall is the only kind that carries its own
#: measurement into the engine; the rest enter as evidence of instability or as
#: an incident that has already happened.
INGEST_KIND: dict[str, EventType] = {
    "RAINFALL": EventType.RAINFALL_OBSERVATION,
    "LANDSLIDE": EventType.INCIDENT_REPORT,
    "FLOOD": EventType.INCIDENT_REPORT,
    "GROUND_INSTABILITY": EventType.FIELD_EVIDENCE,
    "STRUCTURAL_DAMAGE": EventType.FIELD_EVIDENCE,
    "ROAD_BLOCKED": EventType.INFRASTRUCTURE_STATUS,
}


class EvidenceError(ValueError):
    """A report ASTRA cannot place, read or act on."""


@dataclass(frozen=True)
class Extraction:
    """What the classifier read out of a field report, and how sure it is."""

    kind: str
    severity: float
    confidence: str
    keywords: list[str]
    measurements: list[dict[str, Any]]
    suggested_event: str | None
    suggested_value: float | None
    rationale: str


@dataclass
class EvidenceRecord:
    """One filed field report, as ASTRA stored it."""

    id: str
    received_at: str
    observed_at: str
    kind: str
    lon: float | None
    lat: float | None
    habitation_id: str | None
    site_id: str | None
    segment_id: str | None
    reporter: str
    role: str | None
    text: str
    analyst_note: str | None
    photo_path: str | None
    photo_name: str | None
    severity: float | None
    confidence: str
    extraction_mode: str
    extracted: dict[str, Any]
    provenance: str
    ingested_event_id: str | None = None
    photo_url: str | None = field(default=None)


# ---------------------------------------------------------------------------
# Deterministic extraction
# ---------------------------------------------------------------------------


def extract(text: str) -> Extraction:
    """Read a field report with rules a person can check.

    Runs with no key configured, and its output is what the engines consume. A
    language model may refine this later, but it may never replace the hazard
    classification it produced - see ``refine``.
    """
    lowered = (text or "").lower()
    if not lowered.strip():
        raise EvidenceError("a field report has to say something")

    matched_kind = "OTHER"
    matched_words: list[str] = []
    for kind, words in KEYWORDS:
        hits = [word for word in words if word in lowered]
        if hits:
            matched_kind = kind
            matched_words = hits
            break

    severity = 0.0
    severity_words: list[str] = []
    for value, words in SEVERITY_WORDS:
        hits = [word for word in words if word in lowered]
        if hits:
            severity = value
            severity_words = hits
            break
    if severity == 0.0:
        # A report with no severity language is still a report. It is recorded at
        # the middle of the scale and its confidence says the scale was inferred.
        severity = 0.5

    measurements = [
        {"value": float(value), "unit": unit.lower()}
        for value, unit in MEASUREMENT.findall(text or "")
    ]

    if matched_kind == "OTHER":
        confidence = "LOW"
    elif matched_words and severity_words:
        confidence = "HIGH"
    else:
        confidence = "MEDIUM"

    suggested = INGEST_KIND.get(matched_kind)
    suggested_value: float | None = None
    if suggested is EventType.RAINFALL_OBSERVATION:
        millimetres = [m["value"] for m in measurements if m["unit"] == "mm"]
        suggested_value = max(millimetres) if millimetres else None
    elif suggested is EventType.INCIDENT_REPORT:
        # The incident weight scale the historical inventory uses runs roughly
        # 0.5 (small) to 2.0 (very large); an observed severity maps onto it.
        suggested_value = round(0.5 + severity * 1.5, 2)
    elif suggested is EventType.FIELD_EVIDENCE:
        suggested_value = severity
    elif suggested is EventType.INFRASTRUCTURE_STATUS:
        suggested_value = 1.0

    rationale_parts = []
    if matched_words:
        rationale_parts.append(
            f"classified {matched_kind.replace('_', ' ').lower()} from "
            + ", ".join(f"'{word}'" for word in matched_words[:3])
        )
    else:
        rationale_parts.append(
            "no hazard vocabulary matched, so the report is filed as unclassified"
        )
    if severity_words:
        rationale_parts.append(
            f"severity {severity:.2f} from " + ", ".join(f"'{word}'" for word in severity_words[:2])
        )
    else:
        rationale_parts.append(
            f"no severity language found, so severity is recorded at {severity:.2f} "
            "and confidence lowered"
        )
    if measurements:
        rationale_parts.append(
            "measurements read: "
            + ", ".join(f"{m['value']:g} {m['unit']}" for m in measurements[:3])
        )

    return Extraction(
        kind=matched_kind,
        severity=round(severity, 2),
        confidence=confidence,
        keywords=matched_words[:6],
        measurements=measurements,
        suggested_event=suggested.value if suggested else None,
        suggested_value=suggested_value,
        rationale="; ".join(rationale_parts) + ".",
    )


def refine(text: str, deterministic: Extraction) -> tuple[Extraction, str]:
    """Let a configured model refine the reading, without letting it change facts.

    The model may sharpen the severity and add keywords. It may not change the
    hazard classification: a model that reads a landslide report as a flood has
    changed a fact rather than a phrasing, and there is no way for a downstream
    engine to notice. Where they disagree, the deterministic reading stands and
    the disagreement is recorded.
    """
    from astra.engines.narration import NarrationError, _call_provider
    from astra.settings import get_settings

    if get_settings().llm_mode != "connected":
        return deterministic, "rules"

    system = (
        "You classify short field reports from disaster-response officers into a "
        "fixed schema. Reply with ONLY a JSON object and no other text:\n"
        '{"kind": one of ' + ", ".join(EVIDENCE_KINDS) + ", "
        '"severity": a number 0 to 1, "keywords": array of short strings}\n'
        "Base every field only on what the report states. Do not infer, do not "
        "speculate about causes, and never output a number the report does not "
        "support."
    )
    try:
        raw = _call_provider(f"Field report:\n{text}", system=system)
        import json

        parsed = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
    except (NarrationError, ValueError, json.JSONDecodeError):
        return deterministic, "rules"

    kind = str(parsed.get("kind", "")).upper()
    if kind not in EVIDENCE_KINDS or kind != deterministic.kind:
        return (
            Extraction(
                **{
                    **asdict(deterministic),
                    "rationale": (
                        deterministic.rationale
                        + f" A language model read this as '{kind or 'nothing'}'; "
                        "the rule-based classification stands, because a model that "
                        "reclassifies a report has changed a fact rather than a "
                        "phrasing."
                    ),
                }
            ),
            "rules",
        )

    try:
        severity = float(parsed.get("severity", deterministic.severity))
    except (TypeError, ValueError):
        severity = deterministic.severity
    severity = min(max(severity, 0.0), 1.0)
    keywords = [
        str(word)[:40] for word in (parsed.get("keywords") or [])[:6] if str(word).strip()
    ]
    return (
        Extraction(
            kind=deterministic.kind,
            severity=round(severity, 2),
            confidence=deterministic.confidence,
            keywords=keywords or deterministic.keywords,
            measurements=deterministic.measurements,
            suggested_event=deterministic.suggested_event,
            suggested_value=(
                severity
                if deterministic.suggested_event == EventType.FIELD_EVIDENCE.value
                else deterministic.suggested_value
            ),
            rationale=(
                deterministic.rationale
                + " A language model agreed with the classification and refined the "
                f"severity to {severity:.2f}."
            ),
        ),
        "model",
    )


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def file_evidence(
    *,
    text: str,
    reporter: str,
    observed_at: datetime | None = None,
    lon: float | None = None,
    lat: float | None = None,
    habitation_id: str | None = None,
    site_id: str | None = None,
    segment_id: str | None = None,
    role: str | None = None,
    analyst_note: str | None = None,
    photo_path: str | None = None,
    photo_name: str | None = None,
) -> EvidenceRecord:
    """Store one field report, with what the classifier made of it."""
    if not reporter or not reporter.strip():
        raise EvidenceError(
            "a field report has to carry who filed it. Evidence with no reporter "
            "is not evidence an SDMA can act on"
        )
    deterministic = extract(text)
    extraction, mode = refine(text, deterministic)

    now = datetime.now(tz=UTC)
    record = EvidenceRecord(
        id=new_id("EV"),
        received_at=now.isoformat(),
        observed_at=(observed_at or now).isoformat(),
        kind=extraction.kind,
        lon=lon,
        lat=lat,
        habitation_id=habitation_id,
        site_id=site_id,
        segment_id=segment_id,
        reporter=reporter.strip(),
        role=role,
        text=text.strip(),
        analyst_note=analyst_note,
        photo_path=photo_path,
        photo_name=photo_name,
        severity=extraction.severity,
        confidence=extraction.confidence,
        extraction_mode=mode,
        extracted=asdict(extraction),
        provenance=ProvenanceClass.SYNTHETIC_CALIBRATED.value,
    )

    with session() as connection:
        connection.execute(
            """
            INSERT INTO evidence (
                id, received_at, observed_at, kind, lon, lat, habitation_id,
                site_id, segment_id, reporter, role, text, analyst_note,
                photo_path, photo_name, severity, confidence, extraction_mode,
                extracted, provenance, ingested_event_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                record.id,
                record.received_at,
                record.observed_at,
                record.kind,
                record.lon,
                record.lat,
                record.habitation_id,
                record.site_id,
                record.segment_id,
                record.reporter,
                record.role,
                record.text,
                record.analyst_note,
                record.photo_path,
                record.photo_name,
                record.severity,
                record.confidence,
                record.extraction_mode,
                dumps(record.extracted),
                record.provenance,
                record.ingested_event_id,
            ),
        )
    return record


def mark_ingested(evidence_id: str, event_id: str) -> None:
    """Record which live observation this report became."""
    with session() as connection:
        connection.execute(
            "UPDATE evidence SET ingested_event_id = ? WHERE id = ?",
            (event_id, evidence_id),
        )


def _record_from(row) -> EvidenceRecord:
    return EvidenceRecord(
        id=row["id"],
        received_at=row["received_at"],
        observed_at=row["observed_at"],
        kind=row["kind"],
        lon=row["lon"],
        lat=row["lat"],
        habitation_id=row["habitation_id"],
        site_id=row["site_id"],
        segment_id=row["segment_id"],
        reporter=row["reporter"],
        role=row["role"],
        text=row["text"],
        analyst_note=row["analyst_note"],
        photo_path=row["photo_path"],
        photo_name=row["photo_name"],
        severity=row["severity"],
        confidence=row["confidence"],
        extraction_mode=row["extraction_mode"],
        extracted=loads(row["extracted"]) or {},
        provenance=row["provenance"],
        ingested_event_id=row["ingested_event_id"],
        photo_url=(
            f"/evidence/{row['id']}/photo" if row["photo_path"] else None
        ),
    )


def get_evidence(evidence_id: str) -> EvidenceRecord | None:
    with session() as connection:
        row = connection.execute(
            "SELECT * FROM evidence WHERE id = ?", (evidence_id,)
        ).fetchone()
    return _record_from(row) if row else None


def list_evidence(limit: int = 50) -> list[EvidenceRecord]:
    with session() as connection:
        rows = connection.execute(
            "SELECT * FROM evidence ORDER BY received_at DESC, id DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        ).fetchall()
    return [_record_from(row) for row in rows]
