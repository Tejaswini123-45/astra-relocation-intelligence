"""Golden-file test: a refactor may not silently change a demo number.

Regenerate deliberately, and only when the change is intended:

    python scripts/freeze_golden.py
"""

from __future__ import annotations

import json
from pathlib import Path

from astra.domain.model_config import MODEL_CONFIG

GOLDEN = Path(__file__).parent / "golden" / "model_config.json"


def test_model_config_matches_the_committed_golden_snapshot() -> None:
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    actual = MODEL_CONFIG.model_dump(mode="json")
    assert actual == expected, (
        "the model configuration changed. If that was intended, run "
        "`python scripts/freeze_golden.py`, review the diff, and bump "
        "MODEL_CONFIG_VERSION so every audit record can be traced to the "
        "configuration that produced it."
    )


def test_golden_snapshot_records_the_config_version() -> None:
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert expected["version"] == MODEL_CONFIG.version
