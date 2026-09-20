"""The mutable record: evidence, decisions, overrides and the audit ledger.

Everything ASTRA *computes* is deterministic and lives in memory or on disk as a
derived artifact. Everything a *person* does - filing a field report, approving a
plan, overriding an assignment and saying why - is history, and history has to
survive a restart. That is what this file is: a small SQLite database holding the
things that cannot be recomputed.

Two decisions worth stating.

**SQLite, not PostGIS.** At twelve habitations, six sites and one study bounding
box, a spatial database buys nothing and adds a deployment failure mode on demo
day. The geospatial layers are precomputed on disk; only mutable state lives
here. `docs/SCALING.md` records the migration path.

**A connection per operation.** The API serves requests on a thread pool and the
pipeline runs on worker threads; a shared connection would need a lock around
every statement and would eventually be held across one. Opening a connection per
call costs microseconds against a local file and removes the whole class of
problem. WAL mode lets readers and the writer proceed at once.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from astra.settings import get_settings

SCHEMA_VERSION = 2

#: Applied in order, once, on startup. Never edited after they ship - a new
#: change is a new statement appended, so an existing database migrates forward
#: rather than needing to be rebuilt.
MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS evidence (
        id                TEXT PRIMARY KEY,
        received_at       TEXT NOT NULL,
        observed_at       TEXT NOT NULL,
        kind              TEXT NOT NULL,
        lon               REAL,
        lat               REAL,
        habitation_id     TEXT,
        site_id           TEXT,
        segment_id        TEXT,
        reporter          TEXT NOT NULL,
        role              TEXT,
        text              TEXT NOT NULL,
        analyst_note      TEXT,
        photo_path        TEXT,
        photo_name        TEXT,
        severity          REAL,
        confidence        TEXT NOT NULL,
        extraction_mode   TEXT NOT NULL,
        extracted         TEXT NOT NULL,
        provenance        TEXT NOT NULL,
        ingested_event_id TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decisions (
        id                    TEXT PRIMARY KEY,
        created_at            TEXT NOT NULL,
        scenario_id           TEXT NOT NULL,
        trigger               TEXT NOT NULL,
        run_id                TEXT,
        engine_version        TEXT NOT NULL,
        model_config_version  TEXT NOT NULL,
        source_layer_ids      TEXT NOT NULL,
        input_summary_hash    TEXT NOT NULL,
        score_components      TEXT NOT NULL,
        constraint_status     TEXT NOT NULL,
        solver_status         TEXT NOT NULL,
        objective_value       REAL,
        confidence            TEXT NOT NULL,
        state                 TEXT NOT NULL,
        notes                 TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS overrides (
        id             TEXT PRIMARY KEY,
        decision_id    TEXT NOT NULL REFERENCES decisions(id),
        created_at     TEXT NOT NULL,
        actor          TEXT NOT NULL,
        action         TEXT NOT NULL,
        habitation_id  TEXT,
        site_id        TEXT,
        people         INTEGER,
        reason         TEXT NOT NULL,
        consequence    TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_overrides_decision ON overrides(decision_id)",
    "CREATE INDEX IF NOT EXISTS idx_decisions_created ON decisions(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_received ON evidence(received_at)",
    # v2: generated Decision Briefs, frozen as the payload that was rendered.
    """
    CREATE TABLE IF NOT EXISTS briefs (
        id           TEXT PRIMARY KEY,
        decision_id  TEXT NOT NULL REFERENCES decisions(id),
        created_at   TEXT NOT NULL,
        basis        TEXT NOT NULL,
        headline     TEXT NOT NULL,
        payload      TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_briefs_created ON briefs(created_at)",
)

_INIT_LOCK = threading.Lock()
_INITIALISED: set[str] = set()


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=15.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialise(path: Path | None = None) -> Path:
    """Create the database and apply every migration. Safe to call repeatedly."""
    target = Path(path or get_settings().db_path)
    key = str(target.resolve())
    with _INIT_LOCK:
        if key in _INITIALISED:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        with _connect(target) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
            )
            for statement in MIGRATIONS:
                connection.execute(statement)
            connection.execute("DELETE FROM schema_version")
            connection.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
            connection.commit()
        _INITIALISED.add(key)
    return target


@contextmanager
def session(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """One connection, committed on success and rolled back on failure."""
    target = initialise(path)
    connection = _connect(target)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def reset(path: Path | None = None) -> None:
    """Empty every table. Used by tests and by the demonstration reset."""
    with session(path) as connection:
        for table in ("briefs", "overrides", "decisions", "evidence"):
            connection.execute(f"DELETE FROM {table}")


def new_id(prefix: str) -> str:
    """A readable, sortable, collision-free identifier.

    The timestamp prefix makes an id something a person can place in time when
    they read it on a printed brief; the random suffix makes two ids minted in
    the same millisecond distinct.
    """
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:6]}"


def dumps(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None
