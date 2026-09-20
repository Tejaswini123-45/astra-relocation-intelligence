"""Section 7 - back-testing and weight sensitivity: the credibility layer.

Neither a map nor a solver earns trust on its own. Evidence that the model was
tested against something it did not choose does. Two questions are answered here,
and both are answered with the number the run actually produces:

**Is the hazard model any good?** Its composite surface is scored as a predictor
of where landslides have actually happened, against sampled background points,
by ROC-AUC and by the success-rate curve used in the published landslide
susceptibility literature.

**Are the weights arbitrary?** Every weight is perturbed by a fixed fraction over
many Monte Carlo runs and the resulting habitation rankings are compared with the
baseline by Spearman correlation and top-k set stability.

Three disciplines this module holds to, because the first two are where this kind
of validation is usually quietly wrong:

1. **The incident record is an input to the model.** Scoring the model against
   the same points that built one of its factors is circular and inflates the
   AUC. So three variants are computed and all three are reported: the model as
   it runs (labelled *not independent*), spatial k-fold cross-validation where
   the evaluation points never contributed to the surface they are scored on,
   and a terrain-only model with the incident factor removed entirely. The
   cross-validated figure is the headline.
2. **The sample is small and is said to be small.** The inventory holds a couple
   of dozen points inside this corridor. Every AUC is reported with a bootstrap
   confidence interval and the count of positives it rests on, because an AUC
   from n=18 without an interval is a number pretending to be a measurement.
3. **Nothing here is tuned to look good.** The script computes, the artifact
   records, the interface renders. If the AUC is 0.71, the screen says 0.71.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import numpy as np

from astra.domain.enums import HazardType
from astra.domain.model_config import MODEL_CONFIG, AstraModelConfig
from astra.engines.context import EngineContext, IncidentRecord
from astra.engines.grid import AnalysisGrid
from astra.engines.hazard import HazardEngine, HazardResult, kernel_density
from astra.engines.priority import PriorityEngine
from astra.engines.service import RiskRun

SEED = 20260910
"""Fixed so the whole validation is reproducible: the same clone on the same
data produces the same numbers, and a reviewer can check them."""

EXCLUSION_RADIUS_M = 750.0
"""Background points are kept this far from any recorded incident. Sampling a
'non-incident' cell fifty metres from a landslide would be scoring the model
against noise in the inventory's own coordinates rather than against its
predictions."""

CV_FOLDS = 6
"""Spatial cross-validation folds. With a couple of dozen incidents this is the
most folds that still leaves a usable hold-out in each."""


# ---------------------------------------------------------------------------
# Statistics, computed here rather than imported
# ---------------------------------------------------------------------------


def roc_auc(positive: np.ndarray, background: np.ndarray) -> float:
    """AUC as the Mann-Whitney U statistic: P(score at an incident > elsewhere).

    Computed from ranks rather than by integrating a curve, which handles ties
    exactly - and ties matter here, because a clipped susceptibility surface has
    plenty of them.
    """
    positive = positive[np.isfinite(positive)]
    background = background[np.isfinite(background)]
    if positive.size == 0 or background.size == 0:
        return float("nan")
    combined = np.concatenate([positive, background])
    order = combined.argsort()
    ranks = np.empty_like(order, dtype="float64")
    ranks[order] = np.arange(1, combined.size + 1)
    # Average the ranks within each group of ties.
    unique, inverse, counts = np.unique(combined, return_inverse=True, return_counts=True)
    sums = np.zeros(unique.size)
    np.add.at(sums, inverse, ranks)
    ranks = (sums / counts)[inverse]

    n_pos, n_neg = positive.size, background.size
    rank_sum = ranks[:n_pos].sum()
    return float((rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def bootstrap_auc_interval(
    positive: np.ndarray,
    background: np.ndarray,
    *,
    resamples: int = 2000,
    rng: np.random.Generator | None = None,
) -> tuple[float, float]:
    """A 95% percentile bootstrap interval on the AUC.

    Resampling the positives is what the interval is really about: with a couple
    of dozen incidents, the AUC's uncertainty is dominated by which incidents
    happen to be in the inventory.
    """
    positive = positive[np.isfinite(positive)]
    background = background[np.isfinite(background)]
    if positive.size < 2 or background.size < 2:
        return (float("nan"), float("nan"))
    generator = rng or np.random.default_rng(SEED)
    draws = np.empty(resamples)
    for index in range(resamples):
        draws[index] = roc_auc(
            generator.choice(positive, positive.size, replace=True),
            generator.choice(background, background.size, replace=True),
        )
    finite = draws[np.isfinite(draws)]
    if finite.size == 0:
        return (float("nan"), float("nan"))
    return (float(np.percentile(finite, 2.5)), float(np.percentile(finite, 97.5)))


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman rank correlation. Both inputs are already ranks or scores."""
    if a.size < 2:
        return float("nan")

    def rank(values: np.ndarray) -> np.ndarray:
        order = values.argsort()
        ranks = np.empty_like(order, dtype="float64")
        ranks[order] = np.arange(1, values.size + 1)
        return ranks

    ra, rb = rank(a), rank(b)
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    denominator = np.sqrt((ra**2).sum() * (rb**2).sum())
    return float((ra * rb).sum() / denominator) if denominator else float("nan")


# ---------------------------------------------------------------------------
# Back-test
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SuccessRatePoint:
    """One point on the success-rate curve."""

    area_share: float
    incident_share: float


@dataclass(frozen=True)
class BacktestVariant:
    """One way of scoring the model against the inventory."""

    id: str
    name: str
    independent: bool
    auc: float
    auc_ci_low: float
    auc_ci_high: float
    positives: int
    background: int
    success_curve: list[SuccessRatePoint]
    area_under_success_curve: float
    top_10pct_capture: float
    top_20pct_capture: float
    note: str


@dataclass(frozen=True)
class HazardVariantAuc:
    hazard: str
    auc: float
    positives: int


@dataclass(frozen=True)
class BacktestResult:
    variants: list[BacktestVariant]
    per_hazard: list[HazardVariantAuc]
    headline: str
    limitation: str
    incidents_in_study_area: int
    incidents_total: int
    exclusion_radius_m: float
    folds: int
    seed: int
    elapsed_ms: float


def incidents_in_grid(
    grid: AnalysisGrid, incidents: list[IncidentRecord]
) -> list[tuple[IncidentRecord, int, int]]:
    """The incidents ASTRA has ground for, with their grid cells."""
    located: list[tuple[IncidentRecord, int, int]] = []
    for record in incidents:
        index = grid.index_of(record.lon, record.lat)
        if index is not None:
            located.append((record, index[0], index[1]))
    return located


def sample_background(
    grid: AnalysisGrid,
    surface: np.ndarray,
    positives: list[tuple[IncidentRecord, int, int]],
    *,
    count: int,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Sample scored cells away from every recorded incident, without replacement."""
    valid = np.isfinite(surface)
    exclusion_rows = max(1, int(round(EXCLUSION_RADIUS_M / grid.cell.y_m)))
    exclusion_cols = max(1, int(round(EXCLUSION_RADIUS_M / grid.cell.x_m)))
    for _, row, col in positives:
        r0, r1 = max(0, row - exclusion_rows), min(grid.rows, row + exclusion_rows + 1)
        c0, c1 = max(0, col - exclusion_cols), min(grid.cols, col + exclusion_cols + 1)
        valid[r0:r1, c0:c1] = False

    rows, cols = np.nonzero(valid)
    if rows.size == 0:
        return np.array([], dtype=int), np.array([], dtype=int)
    take = min(count, rows.size)
    chosen = rng.choice(rows.size, size=take, replace=False)
    return rows[chosen], cols[chosen]


def area_rank(surface: np.ndarray, score: float) -> float:
    """The share of the corridor scoring at or above ``score``.

    Working in area shares rather than raw scores is what lets a
    cross-validated success curve be assembled from folds: each incident's
    position is measured against the surface it was actually scored on, and the
    positions are then comparable across folds.
    """
    finite = surface[np.isfinite(surface)]
    if finite.size == 0 or not np.isfinite(score):
        return float("nan")
    return float((finite >= score).mean())


def success_rate_curve(
    area_ranks: list[float],
) -> tuple[list[SuccessRatePoint], float]:
    """What share of recorded incidents falls in the most susceptible X% of ground.

    The standard validation in the landslide-susceptibility literature, and the
    one an official can read without knowing what an AUC is: *this model puts
    two thirds of every landslide we know about in the worst fifth of the
    corridor.*

    Takes each incident's position as an area share so that a cross-validated
    curve is assembled from the fold surfaces the incidents were scored on,
    rather than being quietly recomputed against the full-data surface - which
    would make the cross-validated row identical to the uncross-validated one and
    the whole exercise pointless.
    """
    ranks = np.array([value for value in area_ranks if np.isfinite(value)])
    if ranks.size == 0:
        return [], float("nan")

    points: list[SuccessRatePoint] = []
    for share in np.round(np.arange(0.0, 1.0001, 0.05), 4):
        if share == 0.0:
            points.append(SuccessRatePoint(area_share=0.0, incident_share=0.0))
            continue
        points.append(
            SuccessRatePoint(
                area_share=float(share),
                incident_share=float((ranks <= share).mean()),
            )
        )
    area = float(
        np.trapezoid(
            [point.incident_share for point in points],
            [point.area_share for point in points],
        )
    )
    return points, area


def _capture_at(points: list[SuccessRatePoint], share: float) -> float:
    for point in points:
        if abs(point.area_share - share) < 1e-6:
            return point.incident_share
    return float("nan")


def _score_variant(
    variant_id: str,
    name: str,
    *,
    independent: bool,
    positive_scores: np.ndarray,
    background_scores: np.ndarray,
    area_ranks: list[float],
    rng: np.random.Generator,
    note: str,
) -> BacktestVariant:
    auc = roc_auc(positive_scores, background_scores)
    low, high = bootstrap_auc_interval(positive_scores, background_scores, rng=rng)
    curve, area = success_rate_curve(area_ranks)
    return BacktestVariant(
        id=variant_id,
        name=name,
        independent=independent,
        auc=round(auc, 4),
        auc_ci_low=round(low, 4),
        auc_ci_high=round(high, 4),
        positives=int(np.isfinite(positive_scores).sum()),
        background=int(np.isfinite(background_scores).sum()),
        success_curve=curve,
        area_under_success_curve=round(area, 4),
        top_10pct_capture=round(_capture_at(curve, 0.10), 4),
        top_20pct_capture=round(_capture_at(curve, 0.20), 4),
        note=note,
    )


def _without_incident_factor(config: AstraModelConfig) -> AstraModelConfig:
    """The landslide model with the incident-density weight removed.

    The removed weight is redistributed across the remaining factors in
    proportion to what they already carry, so the weight set still sums to one
    and the relative emphasis among the terrain and rainfall factors is
    unchanged. Anything else would be testing a different model.
    """
    weight_set = config.hazard.landslide_weights
    weights = dict(weight_set.weights)
    if "incident_density" not in weights:
        return config
    removed = weights["incident_density"]
    remaining = sum(
        constant.value for name, constant in weights.items() if name != "incident_density"
    )
    if remaining <= 0:
        return config
    scale = 1.0 + removed.value / remaining
    # The factor keeps its place in the weight set at zero rather than being
    # deleted. The engine refuses to score a factor it has no weight for, and
    # rightly - a scored factor with no declared weight is exactly the silent
    # discrepancy that guard exists to catch. Zero is the honest way to say "this
    # contributes nothing".
    rescaled = {
        name: constant.model_copy(
            update={"value": 0.0 if name == "incident_density" else constant.value * scale}
        )
        for name, constant in weights.items()
    }
    return config.model_copy(
        update={
            "hazard": config.hazard.model_copy(
                update={
                    "landslide_weights": weight_set.model_copy(
                        update={"weights": rescaled}
                    )
                }
            )
        }
    )


def _surface_without(
    context: EngineContext,
    ceilings: dict[str, float],
    keep: list[IncidentRecord],
) -> np.ndarray:
    """Re-score the composite with the incident surface rebuilt from `keep` only."""
    from dataclasses import replace

    density = kernel_density(
        context.grid,
        np.array([record.lon for record in keep]),
        np.array([record.lat for record in keep]),
        np.array([record.weight for record in keep]),
        MODEL_CONFIG.hazard.history_kernel_radius_m.value,
    )
    surfaces = replace(context.surfaces, incident_density=density)
    return HazardEngine(ceilings=ceilings).compute(surfaces).composite


def backtest(run: RiskRun) -> BacktestResult:
    """Score the composite surface against the recorded landslide inventory."""
    started = time.perf_counter()
    context = run.context
    grid = context.grid
    config = MODEL_CONFIG.validation
    rng = np.random.default_rng(SEED)

    positives = incidents_in_grid(grid, context.incidents)
    baseline_surface = run.result.composite
    background_rows, background_cols = sample_background(
        grid,
        baseline_surface,
        positives,
        count=int(config.backtest_background_points.value),
        rng=rng,
    )

    variants: list[BacktestVariant] = []
    per_hazard: list[HazardVariantAuc] = []

    if positives and background_rows.size:
        ceilings = HazardEngine().measure_ceilings(context.surfaces)

        # -- the model exactly as it runs -----------------------------------
        variants.append(
            _score_variant(
                "as_deployed",
                "As deployed",
                independent=False,
                positive_scores=np.array(
                    [baseline_surface[row, col] for _, row, col in positives]
                ),
                background_scores=baseline_surface[background_rows, background_cols],
                area_ranks=[
                    area_rank(baseline_surface, float(baseline_surface[row, col]))
                    for _, row, col in positives
                ],
                rng=rng,
                note=(
                    "The model exactly as it runs. Recorded incidents are one of the "
                    "landslide sub-model's weighted factors, so this figure is NOT "
                    "independent of the points it is scored against and will read "
                    "high. It is reported for comparison, not as evidence."
                ),
            )
        )

        # -- spatial cross-validation, the honest headline -------------------
        # Positives *and* background are scored on the same fold surface. Scoring
        # the background on the full-data surface instead would compare numbers
        # produced by two different models, and the resulting AUC would measure
        # that difference as much as the model's skill.
        folds = min(CV_FOLDS, len(positives))
        order = rng.permutation(len(positives))
        cv_positive: list[float] = []
        cv_background: list[float] = []
        cv_area_ranks: list[float] = []
        for fold in range(folds):
            held = {int(index) for index in order[fold::folds]}
            keep = [
                record
                for index, (record, _, _) in enumerate(positives)
                if index not in held
            ]
            surface = _surface_without(context, ceilings, keep)
            cv_background.extend(
                float(value) for value in surface[background_rows, background_cols]
            )
            for index in held:
                _, row, col = positives[index]
                score = float(surface[row, col])
                cv_positive.append(score)
                cv_area_ranks.append(area_rank(surface, score))
        variants.append(
            _score_variant(
                "spatial_cv",
                f"Cross-validated ({folds}-fold)",
                independent=True,
                positive_scores=np.array(cv_positive),
                background_scores=np.array(cv_background),
                area_ranks=cv_area_ranks,
                rng=rng,
                note=(
                    f"Each incident is scored on a surface built without it: the "
                    f"inventory is split into {folds} folds and, for each, the "
                    "incident-density factor is rebuilt from the other folds only "
                    "before re-scoring. Background points are scored on the same "
                    "fold surface, so both sides of every comparison come from one "
                    "model. The evaluation points never contributed to the surface "
                    "they are measured on. This is the figure to judge the model by."
                ),
            )
        )

        # -- terrain and rainfall only, the strictest test -------------------
        terrain_config = _without_incident_factor(MODEL_CONFIG)
        terrain_surface = (
            HazardEngine(config=terrain_config, ceilings=ceilings)
            .compute(context.surfaces)
            .composite
        )
        variants.append(
            _score_variant(
                "terrain_only",
                "Terrain and rainfall only",
                independent=True,
                positive_scores=np.array(
                    [terrain_surface[row, col] for _, row, col in positives]
                ),
                background_scores=terrain_surface[background_rows, background_cols],
                area_ranks=[
                    area_rank(terrain_surface, float(terrain_surface[row, col]))
                    for _, row, col in positives
                ],
                rng=rng,
                note=(
                    "The incident-density factor is held at zero weight and its share "
                    "redistributed across the remaining terrain and rainfall factors. "
                    "Nothing about the recorded inventory reaches this surface, so it "
                    "answers the strictest form of the question: does the physical "
                    "model on its own put past failures on dangerous ground?"
                ),
            )
        )

        for hazard in run.result.hazards_modelled:
            surface = run.result.per_hazard[hazard].score
            per_hazard.append(
                HazardVariantAuc(
                    hazard=hazard.value,
                    auc=round(
                        roc_auc(
                            np.array([surface[row, col] for _, row, col in positives]),
                            surface[background_rows, background_cols],
                        ),
                        4,
                    ),
                    positives=len(positives),
                )
            )

    headline = _backtest_headline(variants, len(positives))
    return BacktestResult(
        variants=variants,
        per_hazard=per_hazard,
        headline=headline,
        limitation=(
            "The ceiling of this validation is set by the inventory, not by the "
            "model. The NASA Global Landslide Catalogue is compiled from media and "
            "official reports, so it is biased towards events near roads and "
            "settlements and towards those that caused harm; unreported failures on "
            "empty slopes are missing, and every one of them counts here as ground "
            "where nothing happened. Coordinates carry a stated accuracy of up to a "
            "kilometre. And the sample inside this corridor is small. A back-test "
            "against a record like this can show that a model is not working; it "
            "cannot prove that one is."
        ),
        incidents_in_study_area=len(positives),
        incidents_total=len(context.incidents),
        exclusion_radius_m=EXCLUSION_RADIUS_M,
        folds=min(CV_FOLDS, len(positives)) if positives else 0,
        seed=SEED,
        elapsed_ms=round((time.perf_counter() - started) * 1000.0, 1),
    )


def _backtest_headline(variants: list[BacktestVariant], positives: int) -> str:
    independent = next((v for v in variants if v.independent and v.id == "spatial_cv"), None)
    if independent is None or not np.isfinite(independent.auc):
        return (
            "The recorded inventory holds too few incidents inside this corridor to "
            "back-test against. ASTRA reports that rather than a number."
        )
    return (
        f"Cross-validated ROC-AUC {independent.auc:.2f} "
        f"(95% CI {independent.auc_ci_low:.2f}-{independent.auc_ci_high:.2f}) against "
        f"{positives} recorded incidents, each scored on a surface built without it. "
        f"{independent.top_20pct_capture * 100:.0f}% of them fall in the most "
        "susceptible fifth of the corridor."
    )


# ---------------------------------------------------------------------------
# Weight sensitivity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HabitationStability:
    habitation_id: str
    name: str
    baseline_rank: int
    baseline_priority: float
    median_rank: float
    best_rank: int
    worst_rank: int
    rank_spread: int
    priority_p05: float
    priority_p95: float
    stable: bool
    note: str


@dataclass(frozen=True)
class SensitivityResult:
    runs: int
    perturbation: float
    top_k: int
    spearman_mean: float
    spearman_median: float
    spearman_p05: float
    spearman_min: float
    top_k_unchanged_share: float
    top_k_baseline: list[str]
    habitations: list[HabitationStability]
    weights_perturbed: int
    headline: str
    method: str
    seed: int
    elapsed_ms: float


def _perturb_weight_set(weight_set, rng, amount: float):
    """Perturb every factor weight and renormalise so the set still sums to one."""
    names = list(weight_set.weights)
    values = np.array([weight_set.weights[name].value for name in names])
    factors = 1.0 + rng.uniform(-amount, amount, size=values.size)
    perturbed = np.clip(values * factors, 1e-6, None)
    perturbed = perturbed / perturbed.sum()
    return weight_set.model_copy(
        update={
            "weights": {
                name: weight_set.weights[name].model_copy(update={"value": float(value)})
                for name, value in zip(names, perturbed, strict=True)
            }
        }
    )


PRIORITY_WEIGHT_KEYS = ("w_hazard", "w_exposure", "w_vulnerability", "w_history")


def _perturb_priority(priority_config, rng, amount: float):
    """Perturb the four top-level priority weights and renormalise."""
    values = np.array(
        [getattr(priority_config, key).value for key in PRIORITY_WEIGHT_KEYS]
    )
    factors = 1.0 + rng.uniform(-amount, amount, size=values.size)
    perturbed = np.clip(values * factors, 1e-6, None)
    perturbed = perturbed / perturbed.sum()
    return priority_config.model_copy(
        update={
            key: getattr(priority_config, key).model_copy(
                update={"value": float(value)}
            )
            for key, value in zip(PRIORITY_WEIGHT_KEYS, perturbed, strict=True)
        }
    )


def _footprint_windows(run: RiskRun) -> dict[str, tuple[slice, slice]]:
    """The grid window each habitation's footprint covers, computed once.

    Sampling this per habitation per Monte Carlo run is what makes a thousand-run
    sensitivity analysis take minutes instead of hours.
    """
    grid = run.grid
    radius = MODEL_CONFIG.priority.footprint_radius_m.value
    half_rows = max(0, int(round(radius / grid.cell.y_m)))
    half_cols = max(0, int(round(radius / grid.cell.x_m)))
    windows: dict[str, tuple[slice, slice]] = {}
    for habitation in run.context.habitations:
        index = grid.index_of(habitation.centroid.lon, habitation.centroid.lat)
        if index is None:
            continue
        row, col = index
        windows[habitation.id] = (
            slice(max(0, row - half_rows), min(grid.rows, row + half_rows + 1)),
            slice(max(0, col - half_cols), min(grid.cols, col + half_cols + 1)),
        )
    return windows


def _composite_from(
    result: HazardResult, config: AstraModelConfig
) -> np.ndarray:
    """Re-derive the composite from cached factor surfaces under new weights.

    The normalised factor arrays do not depend on the weights, so a perturbed run
    is a weighted sum over arrays that are already in memory rather than a full
    re-scoring of the grid. This is exact, not an approximation: it is the same
    arithmetic ``score_hazard`` does.
    """
    scores: dict[HazardType, np.ndarray] = {}
    for hazard, surface in result.per_hazard.items():
        weight_set = config.hazard.weights_for(hazard)
        total = np.zeros(surface.score.shape, dtype="float64")
        for factor in surface.factors:
            total += weight_set.w(factor.name) * np.nan_to_num(factor.values, nan=0.0)
        scores[hazard] = np.clip(total * 100.0, 0.0, 100.0)

    stack = np.stack([scores[hazard] for hazard in result.hazards_modelled], axis=0)
    order = np.argsort(stack, axis=0)[::-1]
    highest = np.take_along_axis(stack, order[:1], axis=0)[0]
    second = (
        np.take_along_axis(stack, order[1:2], axis=0)[0]
        if stack.shape[0] > 1
        else np.zeros_like(highest)
    )
    lam = config.hazard.composite_lambda.value
    return np.clip(highest + lam * second, 0.0, 100.0)


def sensitivity(run: RiskRun, *, runs: int | None = None) -> SensitivityResult:
    """Perturb every weight and report how much the ranking actually moves."""
    started = time.perf_counter()
    config = MODEL_CONFIG.validation
    amount = config.sensitivity_perturbation.value
    total_runs = int(runs if runs is not None else config.sensitivity_runs.value)
    top_k = int(config.rank_stability_top_k.value)
    rng = np.random.default_rng(SEED)

    baseline = PriorityEngine().compute(run)
    baseline_rows = sorted(baseline.rows, key=lambda row: row.rank)
    ids = [row.habitation.id for row in baseline_rows]
    names = {row.habitation.id: row.habitation.name for row in baseline_rows}
    baseline_scores = np.array([row.priority_score for row in baseline_rows])
    baseline_top = set(ids[:top_k])

    windows = _footprint_windows(run)
    habitations = {h.id: h for h in run.context.habitations}
    incidents = run.context.incidents

    weights_perturbed = sum(
        len(MODEL_CONFIG.hazard.weights_for(hazard).weights)
        for hazard in run.result.hazards_modelled
    ) + len(PRIORITY_WEIGHT_KEYS)

    correlations = np.empty(total_runs)
    top_unchanged = 0
    ranks = {habitation_id: np.empty(total_runs) for habitation_id in ids}
    scores_seen = {habitation_id: np.empty(total_runs) for habitation_id in ids}

    for index in range(total_runs):
        hazard_config = MODEL_CONFIG.hazard
        for hazard in run.result.hazards_modelled:
            attribute = {
                HazardType.LANDSLIDE: "landslide_weights",
                HazardType.FLOOD: "flood_weights",
                HazardType.CLOUDBURST: "cloudburst_weights",
                HazardType.COASTAL_EROSION: "coastal_weights",
            }[hazard]
            hazard_config = hazard_config.model_copy(
                update={
                    attribute: _perturb_weight_set(
                        getattr(hazard_config, attribute), rng, amount
                    )
                }
            )
        perturbed = MODEL_CONFIG.model_copy(
            update={
                "hazard": hazard_config,
                "priority": _perturb_priority(MODEL_CONFIG.priority, rng, amount),
            }
        )

        composite = _composite_from(run.result, perturbed)
        engine = PriorityEngine(perturbed)
        run_scores: list[tuple[str, float]] = []
        for habitation_id in ids:
            window = windows.get(habitation_id)
            if window is None:
                run_scores.append((habitation_id, 0.0))
                continue
            patch = composite[window]
            hazard_component = engine.hazard_component(
                float(np.nanmean(patch)), float(np.nanmax(patch))
            )
            habitation = habitations[habitation_id]
            score, _ = engine.priority(
                hazard_component,
                engine.exposure(habitation),
                engine.vulnerability(habitation),
                engine.history(habitation, incidents),
            )
            run_scores.append((habitation_id, score))

        ordered = sorted(run_scores, key=lambda entry: entry[1], reverse=True)
        rank_of = {habitation_id: rank for rank, (habitation_id, _) in enumerate(ordered, 1)}
        score_of = dict(run_scores)
        for habitation_id in ids:
            ranks[habitation_id][index] = rank_of[habitation_id]
            scores_seen[habitation_id][index] = score_of[habitation_id]
        correlations[index] = spearman(
            baseline_scores, np.array([score_of[i] for i in ids])
        )
        if {habitation_id for habitation_id, _ in ordered[:top_k]} == baseline_top:
            top_unchanged += 1

    stability: list[HabitationStability] = []
    for row in baseline_rows:
        habitation_id = row.habitation.id
        seen = ranks[habitation_id]
        spread = int(seen.max() - seen.min())
        stable = spread <= 1
        stability.append(
            HabitationStability(
                habitation_id=habitation_id,
                name=names[habitation_id],
                baseline_rank=row.rank,
                baseline_priority=round(row.priority_score, 1),
                median_rank=float(np.median(seen)),
                best_rank=int(seen.min()),
                worst_rank=int(seen.max()),
                rank_spread=spread,
                priority_p05=round(float(np.percentile(scores_seen[habitation_id], 5)), 1),
                priority_p95=round(float(np.percentile(scores_seen[habitation_id], 95)), 1),
                stable=stable,
                note=(
                    "Holds its position under every perturbation tested."
                    if stable
                    else f"Moves between rank {int(seen.min())} and {int(seen.max())} "
                    f"when the weights are perturbed by "
                    f"{amount * 100:.0f}%; treat its exact position as indicative."
                ),
            )
        )

    finite = correlations[np.isfinite(correlations)]
    share = top_unchanged / total_runs if total_runs else float("nan")
    return SensitivityResult(
        runs=total_runs,
        perturbation=amount,
        top_k=top_k,
        spearman_mean=round(float(finite.mean()), 4) if finite.size else float("nan"),
        spearman_median=round(float(np.median(finite)), 4) if finite.size else float("nan"),
        spearman_p05=round(float(np.percentile(finite, 5)), 4) if finite.size else float("nan"),
        spearman_min=round(float(finite.min()), 4) if finite.size else float("nan"),
        top_k_unchanged_share=round(share, 4),
        top_k_baseline=ids[:top_k],
        habitations=stability,
        weights_perturbed=weights_perturbed,
        headline=(
            f"Across {total_runs:,} runs perturbing all {weights_perturbed} weights by "
            f"+/-{amount * 100:.0f}%, the habitation ranking holds a median Spearman "
            f"correlation of {np.median(finite):.3f} with the baseline, and the top "
            f"{top_k} set is unchanged in {share * 100:.0f}% of them."
            if finite.size
            else "The sensitivity analysis produced no comparable ranking."
        ),
        method=(
            "Every factor weight in every hazard sub-model, and all four top-level "
            "priority weights, are multiplied by an independent uniform draw in "
            f"[1-{amount:g}, 1+{amount:g}] and renormalised so each set still sums to "
            "one. The composite is then re-derived from the cached normalised factor "
            "surfaces - the same arithmetic the engine does, not an approximation - "
            "and the whole ranking is recomputed. The seed is fixed, so this is "
            "reproducible."
        ),
        seed=SEED,
        elapsed_ms=round((time.perf_counter() - started) * 1000.0, 1),
    )


# ---------------------------------------------------------------------------
# The confidence surface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfidenceBandSummary:
    band: str
    cells: int
    area_km2: float
    share: float
    zones: int
    habitations: int


@dataclass(frozen=True)
class ConfidenceSurfaceResult:
    mean: float
    median: float
    p10: float
    minimum: float
    maximum: float
    bands: list[ConfidenceBandSummary]
    low_confidence_habitations: list[str]
    note: str


def confidence_surface(run: RiskRun) -> ConfidenceSurfaceResult:
    """Where the evidence is thin, reported separately from where the risk is high.

    Confidence is never multiplied into susceptibility. A cell can be highly
    susceptible on thin evidence, and an official has to be able to see that -
    which is why this is a separate surface with its own bands, and why the map
    draws low-confidence ground differently rather than quietly discounting it.
    """
    from astra.engines.service import confidence_band

    values = run.result.confidence
    finite = values[np.isfinite(values)]
    cell_area = run.grid.cell_area_km2

    bands: list[ConfidenceBandSummary] = []
    # Band membership uses the same thresholds the badges use, applied to the
    # whole surface at once.
    config = MODEL_CONFIG.confidence
    high = values >= config.band_high_min.value
    medium = (values >= config.band_medium_min.value) & ~high
    low = np.isfinite(values) & ~high & ~medium

    zone_bands: dict[str, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for zone in run.zones:
        zone_bands[confidence_band(zone.mean_confidence).value] += 1

    habitation_bands: dict[str, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    thin: list[str] = []
    for habitation in run.context.habitations:
        index = run.grid.index_of(habitation.centroid.lon, habitation.centroid.lat)
        if index is None:
            continue
        value = float(values[index[0], index[1]])
        band = confidence_band(value).value
        habitation_bands[band] += 1
        if band == "LOW":
            thin.append(habitation.id)

    for name, mask in (("HIGH", high), ("MEDIUM", medium), ("LOW", low)):
        cells = int(mask.sum())
        bands.append(
            ConfidenceBandSummary(
                band=name,
                cells=cells,
                area_km2=round(cells * cell_area, 2),
                share=round(cells / finite.size, 4) if finite.size else 0.0,
                zones=zone_bands[name],
                habitations=habitation_bands[name],
            )
        )

    low_value = float(finite.min()) if finite.size else float("nan")
    high_value = float(finite.max()) if finite.size else float("nan")
    spread_note = (
        f" Across this corridor the surface runs from {low_value:.2f} to "
        f"{high_value:.2f}: a narrow range, because three of its four inputs barely "
        "vary here. Terrain comes from a DEM that covers the whole area, the "
        "resolution is the same everywhere, and the provenance mix is near-uniform. "
        "What actually varies is the incident record, so the confidence surface in "
        "this study area is largely a map of where evidence has been reported. That "
        "is a limitation of the corridor, not a defect of the formula, and it is "
        "reported rather than dressed up as a richer signal than it is."
        if finite.size
        else ""
    )
    return ConfidenceSurfaceResult(
        mean=round(float(finite.mean()), 4) if finite.size else float("nan"),
        median=round(float(np.median(finite)), 4) if finite.size else float("nan"),
        p10=round(float(np.percentile(finite, 10)), 4) if finite.size else float("nan"),
        minimum=round(low_value, 4) if finite.size else float("nan"),
        maximum=round(high_value, 4) if finite.size else float("nan"),
        bands=bands,
        low_confidence_habitations=sorted(thin),
        note=(
            "Confidence is computed from data completeness, provenance mix, evidence "
            "recency and spatial resolution, and is never multiplied into the "
            "susceptibility score. A cell can be highly susceptible on thin evidence, "
            "and the interface has to be able to say so." + spread_note
        ),
    )


# ---------------------------------------------------------------------------
# The whole report
# ---------------------------------------------------------------------------


@dataclass
class ValidationReport:
    """Everything section 7 asks for, computed once and served."""

    generated_at: str
    model_config_version: str
    engine_version: str
    backtest: BacktestResult
    sensitivity: SensitivityResult
    confidence: ConfidenceSurfaceResult
    elapsed_ms: float = 0.0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def run_validation(run: RiskRun, *, sensitivity_runs: int | None = None) -> ValidationReport:
    started = time.perf_counter()
    tested = backtest(run)
    stability = sensitivity(run, runs=sensitivity_runs)
    confidence = confidence_surface(run)
    return ValidationReport(
        generated_at=datetime.now(tz=UTC).isoformat(),
        model_config_version=MODEL_CONFIG.version,
        engine_version=MODEL_CONFIG.engine_version,
        backtest=tested,
        sensitivity=stability,
        confidence=confidence,
        elapsed_ms=round((time.perf_counter() - started) * 1000.0, 1),
        notes=[
            "Every figure here is produced by scripts/backtest.py from the vendored "
            "data and the committed model configuration, with a fixed seed. Nothing "
            "is entered by hand and nothing is tuned to look favourable.",
        ],
    )
