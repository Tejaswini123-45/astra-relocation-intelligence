"""The provenance registry: what every dataset is, and how honest it is.

CLAUDE.md section 4.2 requires each dataset to declare a provenance class and a
full record. The registry is loaded from ``data/provenance.json`` so that adding
data in a later slice is a data change, not a code change, and so the file can be
diffed and reviewed on its own.

Registration is deliberately strict. A record that omits a field, or claims
``REAL_OPEN`` without a source URL, fails to load - the API then refuses to
start rather than serve a layer whose origin it cannot state.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError

from astra.domain.enums import ProvenanceClass
from astra.domain.models import DatasetRecord
from astra.settings import get_settings


class ProvenanceError(RuntimeError):
    """Raised when the registry cannot be loaded or is internally inconsistent."""


class ProvenanceRegistry:
    """An immutable, validated view over the dataset records on disk."""

    def __init__(self, records: list[DatasetRecord]) -> None:
        duplicates = _duplicates([record.id for record in records])
        if duplicates:
            raise ProvenanceError(f"duplicate dataset ids in registry: {duplicates}")
        self._records = list(records)
        self._by_id = {record.id: record for record in records}

    @property
    def records(self) -> list[DatasetRecord]:
        return list(self._records)

    def get(self, dataset_id: str) -> DatasetRecord:
        try:
            return self._by_id[dataset_id]
        except KeyError as exc:
            raise ProvenanceError(
                f"dataset '{dataset_id}' is referenced but not registered"
            ) from exc

    def has(self, dataset_id: str) -> bool:
        return dataset_id in self._by_id

    def by_class(self, provenance: ProvenanceClass) -> list[DatasetRecord]:
        return [r for r in self._records if r.provenance is provenance]

    def counts(self) -> dict[str, int]:
        """Per-class totals, rendered in the provenance panel without blending."""
        return {
            provenance.value: len(self.by_class(provenance))
            for provenance in ProvenanceClass
        }


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for value in values:
        if value in seen:
            dupes.add(value)
        seen.add(value)
    return sorted(dupes)


def load_registry(path: Path | None = None) -> ProvenanceRegistry:
    """Load and validate the registry from disk."""
    registry_path = path or get_settings().provenance_path
    if not registry_path.exists():
        raise ProvenanceError(
            f"provenance registry missing at {registry_path}; ASTRA will not serve "
            "layers whose origin it cannot state"
        )
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProvenanceError(f"provenance registry is not valid JSON: {exc}") from exc

    raw_records = payload.get("datasets")
    if not isinstance(raw_records, list):
        raise ProvenanceError("provenance registry must contain a 'datasets' array")

    records: list[DatasetRecord] = []
    for index, raw in enumerate(raw_records):
        try:
            records.append(DatasetRecord.model_validate(raw))
        except ValidationError as exc:
            identifier = raw.get("id", f"#{index}") if isinstance(raw, dict) else f"#{index}"
            raise ProvenanceError(
                f"dataset '{identifier}' failed provenance validation: {exc}"
            ) from exc
    return ProvenanceRegistry(records)


@lru_cache(maxsize=1)
def get_registry() -> ProvenanceRegistry:
    """Process-wide registry. Cached because it is read-only at runtime."""
    return load_registry()
