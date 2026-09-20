# Validation

Neither a map nor a solver earns trust on its own. This is the evidence that
ASTRA's hazard model and priority ranking were tested, reported whatever they
showed. Every figure below is produced by `scripts/backtest.py` from the vendored
data and the committed model configuration with a fixed seed, written to
`data/derived/validation.json`, served at `GET /validation`, and rendered on the
Model & Provenance screen. CI re-runs it with `--check` and fails on drift.

Figures are for model config `1.10.0`, engine `0.6.0`.

## Hazard back-test against recorded incidents

18 incidents from the NASA Global Landslide Catalog fall inside the corridor.
Each is compared with 12,000 background points sampled away from every incident.

| Variant | Independent of the incidents? | ROC-AUC | 95% CI |
|---|---|---|---|
| As deployed | **No** - incident density is one of the model's factors | 0.88 | 0.82-0.93 |
| **Cross-validated (6-fold)** | Yes - the incident factor is rebuilt without the held-out fold | **0.79** | 0.68-0.88 |
| Terrain and rainfall only | Yes - incident factor held at zero weight | 0.66 | 0.52-0.79 |

The headline is the cross-validated figure:

> Cross-validated ROC-AUC 0.79 (95% CI 0.68-0.88) against 18 recorded incidents,
> each scored on a surface built without it. 56% of them fall in the most
> susceptible fifth of the corridor.

Per sub-model, as deployed: landslide 0.88, flood 0.79, cloudburst 0.68.

The success-rate curve is built from each incident's area rank on the surface it
was actually scored on, so the cross-validated curve is genuinely
cross-validated.

**Limitation, stated on screen.** The ceiling of this validation is set by the
inventory, not the model. The catalogue is compiled from media and official
reports, so it is biased towards events near roads and settlements and towards
those that caused harm; unreported failures on empty slopes are missing and count
here as ground where nothing happened. Coordinates carry a stated accuracy of up
to a kilometre, and the sample is small. A back-test against a record like this
can show that a model is not working; it cannot prove that one is.

## Weight sensitivity and rank stability

Every factor weight in every hazard sub-model and all four top-level priority
weights are multiplied by an independent uniform draw in [0.8, 1.2] and
renormalised, 1,000 times, and the whole ranking is recomputed from the cached
normalised factor surfaces.

> Across 1,000 runs perturbing all 18 weights by +/-20%, the habitation ranking
> holds a median Spearman correlation of 0.993 with the baseline, and the top 5
> set is unchanged in 85% of them.

Habitations whose rank moves materially are flagged weight-sensitive on both the
Model screen and the Priority screen.

## Confidence surface

Confidence is computed from data completeness, provenance mix, evidence recency
and spatial resolution, and is **never multiplied into susceptibility or
priority**. Across this corridor it runs from 0.62 to 0.84 - a narrow range,
because the DEM, resolution and provenance mix barely vary here; what varies is
the incident record. It is drawn as a hatch over the hazard layer, never as a
fade, so thin evidence never reads as lower hazard.

## Engine and product tests

- `apps/api/tests`: engine unit tests (normalisation, composite, zones,
  vulnerability, capacity bottleneck including ties, marginal interventions,
  route reliability with closures, tiering and override rules, optimiser
  constraint satisfaction and infeasibility, scenario diff), golden-file tests,
  fixture and referential-integrity tests, API contract tests, and
  `test_brief.py`, which asserts the Decision Brief matches the Plan, Priority,
  Sites, Zones and Routes endpoints figure for figure.
- `e2e/`: Playwright against the real API - `judge-journey.spec.ts` (the full
  judge journey), `simulate.spec.ts`, `live.spec.ts`, `validation.spec.ts`.

Reproduce:

```bash
python scripts/backtest.py          # recompute and write the artifact
python scripts/backtest.py --check  # recompute and fail if a headline moved
cd apps/api && pytest
```
