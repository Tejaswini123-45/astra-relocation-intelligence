"""Fixture loading.

Fixtures live as JSON under ``data/fixtures`` and are parsed into the domain
models, so a malformed record is a load-time failure rather than a wrong number
on screen. Files that a later slice will produce are simply absent for now; the
loader reports what exists and never invents a record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from astra.domain.models import CandidateSite, Habitation, RoadSegment
from astra.settings import get_settings

T = TypeVar("T", bound=BaseModel)

HABITATIONS_FILE = "habitations.json"
SITES_FILE = "sites.json"
ROAD_SEGMENTS_FILE = "road_segments.json"


class FixtureError(RuntimeError):
    """Raised when a fixture file exists but cannot be parsed or validated."""


@dataclass
class FixtureBundle:
    """Everything currently on disk, plus which files were actually present."""

    habitations: list[Habitation] = field(default_factory=list)
    sites: list[CandidateSite] = field(default_factory=list)
    road_segments: list[RoadSegment] = field(default_factory=list)
    files_present: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.habitations) + len(self.sites) + len(self.road_segments)


def _load_collection(path: Path, model: type[T]) -> list[T]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FixtureError(f"{path.name} is not valid JSON: {exc}") from exc

    if isinstance(payload, dict):
        payload = payload.get("features", payload.get("items"))
    if not isinstance(payload, list):
        raise FixtureError(
            f"{path.name} must contain a JSON array, or an object with an 'items' array"
        )

    records: list[T] = []
    for index, raw in enumerate(payload):
        try:
            records.append(model.model_validate(raw))
        except ValidationError as exc:
            identifier = raw.get("id", f"#{index}") if isinstance(raw, dict) else f"#{index}"
            raise FixtureError(
                f"{path.name}: record '{identifier}' failed validation: {exc}"
            ) from exc
    return records


def load_fixtures(fixtures_dir: Path | None = None) -> FixtureBundle:
    """Load every fixture file present. Absent files are absent, not empty stubs."""
    directory = fixtures_dir or get_settings().fixtures_dir
    bundle = FixtureBundle()
    if not directory.exists():
        return bundle

    for filename, model, target in (
        (HABITATIONS_FILE, Habitation, "habitations"),
        (SITES_FILE, CandidateSite, "sites"),
        (ROAD_SEGMENTS_FILE, RoadSegment, "road_segments"),
    ):
        path = directory / filename
        if not path.exists():
            continue
        setattr(bundle, target, _load_collection(path, model))
        bundle.files_present.append(filename)

    return bundle
