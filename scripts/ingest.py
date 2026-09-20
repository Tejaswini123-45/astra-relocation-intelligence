"""Acquire the real open datasets for the study area, once.

    python scripts/ingest.py            # fetch anything not already vendored
    python scripts/ingest.py --force    # re-fetch everything
    python scripts/ingest.py --only copernicus-dem-30m

This is the only script in ASTRA that touches the network. It vendors each
source under ``data/raw`` and rewrites the provenance registry to describe
exactly what is on disk. Nothing at demo time calls out.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from astra.data.connectors import ConnectorError, build_connectors
from astra.data.study_area import get_study_area
from astra.settings import get_settings

REGISTRY_NOTE = (
    "Every dataset ASTRA has actually loaded is registered here, with the processing "
    "step that produced the local artifact. Real datasets are vendored under data/raw "
    "and are never re-fetched at demo time."
)


def _human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-fetch vendored sources")
    parser.add_argument("--only", help="fetch a single dataset id")
    args = parser.parse_args()

    settings = get_settings()
    area = get_study_area()
    connectors = build_connectors(settings.raw_dir, area.bbox)
    if args.only:
        connectors = [c for c in connectors if c.dataset_id == args.only]
        if not connectors:
            print(f"no connector with dataset id '{args.only}'")
            return 2

    acquired = datetime.now(UTC).date()
    records: list[dict] = []
    failures: list[str] = []

    for connector in connectors:
        label = connector.dataset_id
        try:
            result = connector.fetch(force=args.force)
        except ConnectorError as exc:
            failures.append(f"{label}: {exc}")
            print(f"  FAILED   {label}: {exc}")
            if connector.is_cached():
                print(f"           previously vendored artifact retained at {connector.artifact_path}")
                records.append(
                    json.loads(connector.dataset_record(acquired).model_dump_json())
                )
            continue
        state = "cached" if result.from_cache else "fetched"
        print(
            f"  {state:8} {label}: {_human(result.bytes_written)} "
            f"-> {result.path.relative_to(REPO_ROOT)}  ({result.detail})"
        )
        records.append(json.loads(connector.dataset_record(acquired).model_dump_json()))

    # Keep every record this run did not regenerate: the ASTRA-authored ones, and
    # any connector not selected by --only. Rewriting the registry from a partial
    # run would silently delete datasets that are still on disk.
    existing = json.loads(settings.provenance_path.read_text(encoding="utf-8"))
    regenerated_ids = {record["id"] for record in records}
    preserved = [
        record
        for record in existing.get("datasets", [])
        if record["id"] not in regenerated_ids
    ]

    payload = {
        "registry_version": existing.get("registry_version", "1.0.0"),
        "study_area": area.id,
        "note": REGISTRY_NOTE,
        "datasets": sorted(preserved + records, key=lambda r: r["id"]),
    }
    settings.provenance_path.write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    print(f"registry updated: {len(payload['datasets'])} datasets")

    if failures:
        print(f"{len(failures)} source(s) failed; vendored artifacts were left in place")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
