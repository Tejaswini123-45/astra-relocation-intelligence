"""Back-test the hazard model and measure how much its weights matter.

    python scripts/backtest.py [--sensitivity-runs N] [--check]

Writes ``data/derived/validation.json``, which ``GET /validation`` serves and the
Model & Provenance screen renders. Running it is the only way that file changes:
nothing is entered by hand, nothing is rounded up, and the seed is fixed so the
same clone on the same data produces the same numbers.

``--check`` recomputes and compares against the committed artifact instead of
overwriting it, and exits non-zero if the headline figures have moved. That is
what CI runs: a refactor that quietly changes the AUC on the screen has to show
up as a failed build rather than as a better-looking number.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from astra.engines.service import compute_risk
from astra.engines.validation import run_validation
from astra.settings import get_settings

OUTPUT_NAME = "validation.json"

#: Figures whose movement means the story on the screen has changed. Checked in
#: CI at a tolerance that admits floating-point drift and nothing else.
HEADLINE_TOLERANCE = 5e-3


def _headlines(payload: dict) -> dict[str, float]:
    figures: dict[str, float] = {}
    for variant in payload["backtest"]["variants"]:
        figures[f"auc.{variant['id']}"] = variant["auc"]
        figures[f"top20.{variant['id']}"] = variant["top_20pct_capture"]
    figures["spearman.median"] = payload["sensitivity"]["spearman_median"]
    figures["top_k.unchanged"] = payload["sensitivity"]["top_k_unchanged_share"]
    return figures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sensitivity-runs",
        type=int,
        default=None,
        help="Monte Carlo runs. Defaults to validation.sensitivity_runs in the config.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare against the committed artifact instead of overwriting it.",
    )
    args = parser.parse_args()

    settings = get_settings()
    output = settings.derived_dir / OUTPUT_NAME

    print("computing the baseline hazard state...")
    run = compute_risk()
    report = run_validation(run, sensitivity_runs=args.sensitivity_runs)
    payload = report.as_dict()

    tested = report.backtest
    print(f"\nback-test  ({tested.elapsed_ms:.0f} ms)")
    print(f"  {tested.incidents_in_study_area} of {tested.incidents_total} recorded "
          f"incidents fall inside the study area")
    for variant in tested.variants:
        flag = "independent" if variant.independent else "NOT independent"
        print(
            f"  {variant.id:<14} {flag:<15} AUC {variant.auc:.3f} "
            f"[{variant.auc_ci_low:.3f}, {variant.auc_ci_high:.3f}]  "
            f"top-20% capture {variant.top_20pct_capture:.2f}"
        )
    for entry in tested.per_hazard:
        print(f"    per-hazard {entry.hazard:<12} AUC {entry.auc:.3f}")

    stability = report.sensitivity
    print(f"\nsensitivity  ({stability.elapsed_ms:.0f} ms)")
    print(
        f"  {stability.runs} runs, all {stability.weights_perturbed} weights "
        f"+/-{stability.perturbation * 100:.0f}%"
    )
    print(
        f"  Spearman median {stability.spearman_median:.3f}, "
        f"5th percentile {stability.spearman_p05:.3f}, "
        f"worst {stability.spearman_min:.3f}"
    )
    print(
        f"  top-{stability.top_k} set unchanged in "
        f"{stability.top_k_unchanged_share * 100:.0f}% of runs"
    )
    unstable = [entry for entry in stability.habitations if not entry.stable]
    if unstable:
        print(f"  weight-sensitive: {', '.join(entry.habitation_id for entry in unstable)}")

    confidence = report.confidence
    print("\nconfidence surface")
    for band in confidence.bands:
        print(
            f"  {band.band:<7} {band.area_km2:>8.1f} km2  "
            f"{band.share * 100:>5.1f}%  {band.zones:>4} zones  "
            f"{band.habitations} habitations"
        )

    if args.check:
        if not output.exists():
            print(f"\nFAIL: {output} does not exist; run without --check first")
            return 1
        committed = json.loads(output.read_text(encoding="utf-8"))
        drifted = []
        fresh, old = _headlines(payload), _headlines(committed)
        for key, value in fresh.items():
            previous = old.get(key)
            if previous is None:
                drifted.append(f"{key}: not in the committed artifact")
            elif not (
                math.isnan(value) and math.isnan(previous)
            ) and abs(value - previous) > HEADLINE_TOLERANCE:
                drifted.append(f"{key}: {previous:.4f} -> {value:.4f}")
        if drifted:
            print("\nFAIL: validation headlines have moved since the committed run:")
            for line in drifted:
                print(f"  {line}")
            print("Re-run scripts/backtest.py and commit the artifact deliberately.")
            return 1
        print("\nOK: recomputed figures match the committed artifact")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nwrote {output.relative_to(REPO_ROOT)}")
    print(f"  {report.backtest.headline}")
    print(f"  {report.sensitivity.headline}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
