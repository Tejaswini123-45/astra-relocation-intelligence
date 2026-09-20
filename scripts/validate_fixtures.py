"""Fixture integrity gate, runnable standalone and in CI.

Exit code 0 means the dataset is safe to serve. Any other code means it is not,
and the API will refuse to start on the same data.

    python scripts/validate_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from astra.data.validate import validate_all


def main() -> int:
    report = validate_all()
    for check in report.checked:
        print(f"  checked  {check}")
    for warning in report.warnings:
        print(f"  WARNING  {warning}")
    for error in report.errors:
        print(f"  ERROR    {error}")
    print(report.summary())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
