"""The demonstration event feed.

A controllable feed is what makes "real time" demonstrable without waiting for a
real storm. The sequence is a vendored fixture, classified ``DEMO_CONFIG``, and
it is served as *data to be posted*: replaying it drives ``POST /events`` exactly
as an external feed would, and every stage a viewer then sees is a real
execution over the real engines. Nothing in the file is a result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from astra.domain.enums import EventType, ProvenanceClass
from astra.settings import get_settings


class FeedError(RuntimeError):
    """The feed fixture is missing or malformed."""


@dataclass(frozen=True)
class FeedStep:
    """One observation the feed will post, with how long to wait first."""

    delay_ms: int
    kind: EventType
    value: float
    source: str
    note: str
    lon: float | None = None
    lat: float | None = None
    radius_m: float = 1500.0
    target: str | None = None


@dataclass(frozen=True)
class EventFeed:
    id: str
    name: str
    description: str
    note: str
    provenance: ProvenanceClass
    steps: list[FeedStep]


@lru_cache(maxsize=1)
def load_feed(path: Path | None = None) -> EventFeed:
    source = path or get_settings().fixtures_dir / "event_feed.json"
    if not source.exists():
        raise FeedError(f"event feed fixture missing at {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
        steps = [
            FeedStep(
                delay_ms=int(step["delay_ms"]),
                kind=EventType(step["kind"]),
                value=float(step["value"]),
                source=str(step["source"]),
                note=str(step["note"]),
                lon=step.get("lon"),
                lat=step.get("lat"),
                radius_m=float(step.get("radius_m", 1500.0)),
                target=step.get("target"),
            )
            for step in payload["steps"]
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise FeedError(f"event feed fixture at {source} is malformed: {error}") from error
    if not steps:
        raise FeedError(f"event feed fixture at {source} declares no steps")
    return EventFeed(
        id=str(payload["id"]),
        name=str(payload["name"]),
        description=str(payload["description"]),
        note=str(payload.get("note", "")),
        provenance=ProvenanceClass(payload.get("provenance", "DEMO_CONFIG")),
        steps=steps,
    )
