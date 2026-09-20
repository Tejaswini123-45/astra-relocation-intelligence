"""Export the OpenAPI schema that the TypeScript contracts are generated from.

    python scripts/export_openapi.py

Writes ``packages/contracts/openapi.json``. The generated types are committed, so
a schema change that the frontend has not been regenerated against shows up as a
diff in review rather than as a runtime surprise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from astra.main import app

OUTPUT = REPO_ROOT / "packages" / "contracts" / "openapi.json"


def main() -> int:
    schema = app.openapi()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"wrote {OUTPUT.relative_to(REPO_ROOT)}: "
        f"{len(schema['paths'])} paths, "
        f"{len(schema['components']['schemas'])} schemas"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
