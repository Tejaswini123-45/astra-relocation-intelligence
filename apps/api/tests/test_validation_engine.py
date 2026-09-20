"""Section 7 - the credibility layer.

These tests exist because a validation module is the easiest place in a system to
be quietly wrong in a flattering direction. So they check the statistics against
cases whose answers are known, and then check the *discipline*: that the
cross-validated figure is genuinely blind to the points it scores, that the
uncross-validated one is labelled, and that a small sample is reported as a small
sample rather than as a confident number.
"""

from __future__ import annotations

import numpy as np
import pytest

from astra.domain.model_config import MODEL_CONFIG
from astra.engines.hazard import HazardEngine
from astra.engines.service import baseline_risk
from astra.engines.validation import (
    EXCLUSION_RADIUS_M,
    SEED,
    _without_incident_factor,
    area_rank,
    backtest,
    bootstrap_auc_interval,
    confidence_surface,
    incidents_in_grid,
    roc_auc,
    sample_background,
    sensitivity,
    spearman,
    success_rate_curve,
)


@pytest.fixture(scope="module")
def run():
    return baseline_risk()


@pytest.fixture(scope="module")
def tested(run):
    return backtest(run)


# ---------------------------------------------------------------------------
# The statistics, against answers that are known
# ---------------------------------------------------------------------------


def test_auc_is_one_when_every_positive_outranks_every_background() -> None:
    assert roc_auc(np.array([5.0, 6.0, 7.0]), np.array([1.0, 2.0, 3.0])) == 1.0


def test_auc_is_zero_when_the_ordering_is_exactly_inverted() -> None:
    assert roc_auc(np.array([1.0, 2.0]), np.array([5.0, 6.0])) == 0.0


def test_auc_is_a_half_when_every_value_is_tied() -> None:
    """A clipped susceptibility surface has ties, and they must not inflate it."""
    assert roc_auc(np.full(10, 3.0), np.full(10, 3.0)) == pytest.approx(0.5)


def test_auc_of_a_coin_flip_lands_near_a_half() -> None:
    rng = np.random.default_rng(1)
    value = roc_auc(rng.normal(size=4000), rng.normal(size=4000))
    assert 0.46 < value < 0.54


def test_the_bootstrap_interval_brackets_the_point_estimate() -> None:
    rng = np.random.default_rng(2)
    positive = rng.normal(loc=1.0, size=40)
    background = rng.normal(size=400)
    point = roc_auc(positive, background)
    low, high = bootstrap_auc_interval(positive, background, resamples=400, rng=rng)
    assert low < point < high
    assert 0.0 <= low < high <= 1.0


def test_a_smaller_sample_gives_a_wider_interval() -> None:
    """The whole reason the interval is reported: n is doing the work."""
    rng = np.random.default_rng(3)
    background = rng.normal(size=800)
    _, wide_high = bootstrap_auc_interval(
        rng.normal(loc=1.0, size=8), background, resamples=400, rng=rng
    )
    wide_low, _ = bootstrap_auc_interval(
        rng.normal(loc=1.0, size=8), background, resamples=400, rng=rng
    )
    narrow_low, narrow_high = bootstrap_auc_interval(
        rng.normal(loc=1.0, size=200), background, resamples=400, rng=rng
    )
    assert (wide_high - wide_low) > (narrow_high - narrow_low)


def test_spearman_is_one_for_an_identical_ordering() -> None:
    values = np.array([3.0, 1.0, 2.0, 5.0])
    assert spearman(values, values * 2.0) == pytest.approx(1.0)


def test_spearman_is_minus_one_for_a_reversed_ordering() -> None:
    values = np.array([1.0, 2.0, 3.0, 4.0])
    assert spearman(values, -values) == pytest.approx(-1.0)


def test_the_success_curve_starts_at_zero_and_ends_at_one() -> None:
    curve, area = success_rate_curve([0.05, 0.4, 0.9])
    assert curve[0].area_share == 0.0
    assert curve[0].incident_share == 0.0
    assert curve[-1].area_share == pytest.approx(1.0)
    assert curve[-1].incident_share == pytest.approx(1.0)
    assert 0.0 < area < 1.0


def test_a_perfect_model_captures_everything_in_a_sliver_of_area() -> None:
    curve, area = success_rate_curve([0.001] * 20)
    assert curve[1].incident_share == pytest.approx(1.0)
    assert area > 0.95


def test_a_worthless_model_traces_the_diagonal() -> None:
    ranks = list(np.linspace(0.0, 1.0, 400))
    _, area = success_rate_curve(ranks)
    assert 0.45 < area < 0.55


def test_area_rank_is_the_share_of_ground_at_or_above_a_score() -> None:
    surface = np.arange(100, dtype="float64")
    assert area_rank(surface, 90.0) == pytest.approx(0.10)
    assert area_rank(surface, 0.0) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Background sampling
# ---------------------------------------------------------------------------


def test_background_points_are_kept_away_from_every_recorded_incident(run) -> None:
    positives = incidents_in_grid(run.grid, run.context.incidents)
    rows, cols = sample_background(
        run.grid,
        run.result.composite,
        positives,
        count=500,
        rng=np.random.default_rng(SEED),
    )
    assert rows.size == 500
    grid = run.grid
    for row, col in zip(rows, cols, strict=True):
        for _, incident_row, incident_col in positives:
            metres = np.hypot(
                (row - incident_row) * grid.cell.y_m,
                (col - incident_col) * grid.cell.x_m,
            )
            assert metres > EXCLUSION_RADIUS_M * 0.5, (
                "a background point next to a landslide is scoring the model "
                "against noise in the inventory's own coordinates"
            )


def test_background_sampling_is_reproducible(run) -> None:
    positives = incidents_in_grid(run.grid, run.context.incidents)
    first = sample_background(
        run.grid, run.result.composite, positives, count=200,
        rng=np.random.default_rng(SEED),
    )
    second = sample_background(
        run.grid, run.result.composite, positives, count=200,
        rng=np.random.default_rng(SEED),
    )
    assert np.array_equal(first[0], second[0])
    assert np.array_equal(first[1], second[1])


# ---------------------------------------------------------------------------
# The back-test, and its honesty
# ---------------------------------------------------------------------------


def test_the_backtest_reports_three_variants_and_labels_the_leaky_one(tested) -> None:
    ids = [variant.id for variant in tested.variants]
    assert ids == ["as_deployed", "spatial_cv", "terrain_only"]
    by_id = {variant.id: variant for variant in tested.variants}
    assert by_id["as_deployed"].independent is False
    assert by_id["spatial_cv"].independent is True
    assert by_id["terrain_only"].independent is True
    assert "NOT independent" in by_id["as_deployed"].note


def test_the_uncross_validated_figure_reads_higher_than_the_honest_one(tested) -> None:
    """Which is exactly why it is labelled rather than reported as the headline."""
    by_id = {variant.id: variant for variant in tested.variants}
    assert by_id["as_deployed"].auc > by_id["spatial_cv"].auc


def test_the_headline_quotes_the_cross_validated_figure_and_the_sample_size(
    tested,
) -> None:
    by_id = {variant.id: variant for variant in tested.variants}
    cv = by_id["spatial_cv"]
    assert f"{cv.auc:.2f}" in tested.headline
    assert str(tested.incidents_in_study_area) in tested.headline
    assert "CI" in tested.headline


def test_every_auc_carries_an_interval_and_a_count(tested) -> None:
    for variant in tested.variants:
        assert 0.0 <= variant.auc_ci_low <= variant.auc <= variant.auc_ci_high <= 1.0
        assert variant.positives > 0
        assert variant.background > 0


def test_the_terrain_only_variant_really_contains_no_incident_evidence() -> None:
    config = _without_incident_factor(MODEL_CONFIG)
    weights = config.hazard.landslide_weights.weights
    assert weights["incident_density"].value == 0.0
    assert sum(constant.value for constant in weights.values()) == pytest.approx(1.0)
    # And the other factors keep their relative emphasis.
    original = MODEL_CONFIG.hazard.landslide_weights.weights
    ratio = weights["slope"].value / weights["ruggedness"].value
    assert ratio == pytest.approx(
        original["slope"].value / original["ruggedness"].value
    )


def test_the_model_beats_chance_on_the_independent_test(tested) -> None:
    """Not a claim about how good it is - a check that it is not upside down."""
    by_id = {variant.id: variant for variant in tested.variants}
    assert by_id["spatial_cv"].auc > 0.5
    assert by_id["terrain_only"].auc > 0.5


def test_the_backtest_names_its_own_limitations(tested) -> None:
    limitation = tested.limitation.lower()
    for phrase in ("inventory", "bias", "small"):
        assert phrase in limitation
    assert "cannot prove" in limitation


def test_per_hazard_aucs_cover_every_modelled_hazard(run, tested) -> None:
    assert {entry.hazard for entry in tested.per_hazard} == {
        hazard.value for hazard in run.result.hazards_modelled
    }


def test_the_backtest_is_reproducible(run) -> None:
    first = backtest(run)
    second = backtest(run)
    assert [variant.auc for variant in first.variants] == [
        variant.auc for variant in second.variants
    ]


# ---------------------------------------------------------------------------
# Sensitivity
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def stability(run):
    return sensitivity(run, runs=60)


def test_perturbed_weight_sets_still_sum_to_one(run) -> None:
    from astra.engines.validation import _perturb_priority, _perturb_weight_set

    rng = np.random.default_rng(SEED)
    for _ in range(20):
        perturbed = _perturb_weight_set(
            MODEL_CONFIG.hazard.landslide_weights, rng, 0.2
        )
        assert sum(c.value for c in perturbed.weights.values()) == pytest.approx(1.0)
        priority = _perturb_priority(MODEL_CONFIG.priority, rng, 0.2)
        total = (
            priority.w_hazard.value
            + priority.w_exposure.value
            + priority.w_vulnerability.value
            + priority.w_history.value
        )
        assert total == pytest.approx(1.0)


def test_re_deriving_the_composite_under_the_baseline_weights_is_the_baseline(
    run,
) -> None:
    """The fast path is exact arithmetic, not an approximation of the engine."""
    from astra.engines.validation import _composite_from

    assert np.allclose(
        _composite_from(run.result, MODEL_CONFIG), run.result.composite, equal_nan=True
    )


def test_sensitivity_reports_the_runs_it_actually_did(stability) -> None:
    assert stability.runs == 60
    assert str(60) in stability.headline.replace(",", "")
    assert stability.weights_perturbed > 0


def test_every_habitation_gets_a_stability_verdict(run, stability) -> None:
    assert len(stability.habitations) == len(run.context.habitations)
    for entry in stability.habitations:
        assert 1 <= entry.best_rank <= entry.worst_rank <= len(stability.habitations)
        assert entry.best_rank <= entry.median_rank <= entry.worst_rank
        assert entry.priority_p05 <= entry.priority_p95
        assert entry.stable == (entry.rank_spread <= 1)
        assert entry.note


def test_a_zero_perturbation_leaves_the_ranking_exactly_alone(run) -> None:
    """The control case. If this moves, the perturbation is not the only input."""
    from astra.domain.model_config import MODEL_CONFIG as base

    unchanged = base.model_copy(
        update={
            "validation": base.validation.model_copy(
                update={
                    "sensitivity_perturbation": base.validation.sensitivity_perturbation.model_copy(
                        update={"value": 0.0}
                    )
                }
            )
        }
    )
    import astra.engines.validation as module

    original = module.MODEL_CONFIG
    module.MODEL_CONFIG = unchanged
    try:
        result = sensitivity(run, runs=5)
    finally:
        module.MODEL_CONFIG = original
    assert result.spearman_min == pytest.approx(1.0)
    assert result.top_k_unchanged_share == 1.0
    assert all(entry.rank_spread == 0 for entry in result.habitations)


def test_a_larger_perturbation_moves_the_ranking_more(run) -> None:
    import astra.engines.validation as module

    def with_perturbation(amount: float):
        base = module.MODEL_CONFIG
        return base.model_copy(
            update={
                "validation": base.validation.model_copy(
                    update={
                        "sensitivity_perturbation": (
                            base.validation.sensitivity_perturbation.model_copy(
                                update={"value": amount}
                            )
                        )
                    }
                )
            }
        )

    original = module.MODEL_CONFIG
    try:
        module.MODEL_CONFIG = with_perturbation(0.05)
        gentle = sensitivity(run, runs=40)
        module.MODEL_CONFIG = with_perturbation(0.6)
        violent = sensitivity(run, runs=40)
    finally:
        module.MODEL_CONFIG = original
    assert violent.spearman_min < gentle.spearman_min
    assert violent.top_k_unchanged_share <= gentle.top_k_unchanged_share


def test_sensitivity_is_reproducible(run) -> None:
    first = sensitivity(run, runs=20)
    second = sensitivity(run, runs=20)
    assert first.spearman_median == second.spearman_median
    assert first.top_k_unchanged_share == second.top_k_unchanged_share


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


def test_the_confidence_bands_account_for_every_scored_cell(run) -> None:
    surface = confidence_surface(run)
    scored = int(np.isfinite(run.result.confidence).sum())
    assert sum(band.cells for band in surface.bands) == scored
    assert sum(band.share for band in surface.bands) == pytest.approx(1.0, abs=1e-3)


def test_confidence_reports_its_own_observed_range(run) -> None:
    """A narrow range is a finding about the corridor, not something to hide."""
    surface = confidence_surface(run)
    assert surface.minimum <= surface.median <= surface.maximum
    assert f"{surface.minimum:.2f}" in surface.note
    assert f"{surface.maximum:.2f}" in surface.note


def test_confidence_is_never_multiplied_into_the_score(run) -> None:
    """The conflation section 5.2 exists to prevent, checked on the real surfaces."""
    composite = run.result.composite
    confidence = run.result.confidence
    finite = np.isfinite(composite) & np.isfinite(confidence)
    # If confidence were folded into the score, the highest-scoring ground would
    # have to be the best-evidenced ground. It is not.
    high_score = composite[finite] >= np.percentile(composite[finite], 90)
    assert confidence[finite][high_score].min() < np.percentile(
        confidence[finite], 60
    ), "highly susceptible ground on thin evidence must remain highly susceptible"


def test_every_habitation_is_placed_in_a_confidence_band(run) -> None:
    surface = confidence_surface(run)
    assert sum(band.habitations for band in surface.bands) == len(
        run.context.habitations
    )


def test_the_terrain_only_model_can_still_be_scored(run) -> None:
    """A guard the engine enforces: a scored factor must have a declared weight."""
    config = _without_incident_factor(MODEL_CONFIG)
    result = HazardEngine(config=config).compute(run.context.surfaces)
    assert np.isfinite(result.composite).any()
