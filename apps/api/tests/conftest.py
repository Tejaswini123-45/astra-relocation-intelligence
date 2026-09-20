"""Keep the test suite out of the demonstration ledger.

Tests file evidence, record decisions and generate briefs. Pointed at the default
store they would land in the same SQLite file the running demo reads, and the
Evidence & Audit screen would show test rows as if an officer had filed them.
The store is redirected to a throwaway file before any application module is
imported; an explicitly set ``ASTRA_DB_PATH`` still wins.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault(
    "ASTRA_DB_PATH", str(Path(tempfile.mkdtemp(prefix="astra-tests-")) / "astra.sqlite")
)
