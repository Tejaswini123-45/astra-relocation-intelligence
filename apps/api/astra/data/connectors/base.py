"""Connector interface.

CLAUDE.md section 4.3: real data is fetched once, vendored into ``data/raw`` and
never fetched again at demo time. Every source therefore has exactly two paths:

* :meth:`Connector.fetch` - touches the network, runs from ``scripts/ingest.py``,
  writes a local artifact and returns what it did.
* :meth:`Connector.load` - never touches the network, reads only the vendored
  artifact, and is the only path any engine or API route uses.

If a source cannot be reached, ``fetch`` fails loudly and the cached artifact
continues to serve. No external call is on the critical path of the demo.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from astra.domain.models import BBox, DatasetRecord


class ConnectorError(RuntimeError):
    """Raised when a fetch fails or a required artifact is missing."""


@dataclass(frozen=True)
class FetchResult:
    """What a fetch actually did, reported by the ingest script."""

    dataset_id: str
    path: Path
    bytes_written: int
    from_cache: bool
    detail: str


class Connector(ABC):
    """One real open dataset, vendored for offline use."""

    #: Dataset id, matching the record in the provenance registry.
    dataset_id: str

    def __init__(self, raw_dir: Path, bbox: BBox) -> None:
        self.raw_dir = raw_dir
        self.bbox = bbox

    @property
    @abstractmethod
    def artifact_path(self) -> Path:
        """Where the vendored artifact lives. Read by :meth:`load`."""

    @abstractmethod
    def fetch(self, *, force: bool = False) -> FetchResult:
        """Acquire the data from its source and vendor it locally."""

    @abstractmethod
    def dataset_record(self, acquired: date) -> DatasetRecord:
        """The provenance record written into ``data/provenance.json``."""

    def is_cached(self) -> bool:
        return self.artifact_path.exists() and self.artifact_path.stat().st_size > 0

    def require_cached(self) -> Path:
        """Resolve the artifact for offline use, or fail with a usable message."""
        if not self.is_cached():
            raise ConnectorError(
                f"dataset '{self.dataset_id}' is not vendored at {self.artifact_path}. "
                "Run `python scripts/ingest.py` once, with a network connection, to "
                "acquire it. ASTRA never fetches at demo time."
            )
        return self.artifact_path

    def _prepare(self, force: bool) -> bool:
        """Return True when a fetch should proceed."""
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        return force or not self.is_cached()

    def _cached_result(self, detail: str = "already vendored") -> FetchResult:
        return FetchResult(
            dataset_id=self.dataset_id,
            path=self.artifact_path,
            bytes_written=self.artifact_path.stat().st_size,
            from_cache=True,
            detail=detail,
        )
