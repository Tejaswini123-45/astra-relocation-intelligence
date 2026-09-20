"""Re-freeze the golden model-configuration snapshot.

Run this only when a configuration change is intended, review the resulting diff,
and bump ``MODEL_CONFIG_VERSION`` so every audit record still traces to the
configuration that produced it.

    python scripts/freeze_golden.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from astra.domain.model_config import MODEL_CONFIG

GOLDEN = REPO_ROOT / "apps" / "api" / "tests" / "golden" / "model_config.json"


def main() -> int:
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    payload = MODEL_CONFIG.model_dump(mode="json")
    GOLDEN.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"froze {GOLDEN.relative_to(REPO_ROOT)} at config version {MODEL_CONFIG.version} "
        f"({len(MODEL_CONFIG.constants())} constants)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
