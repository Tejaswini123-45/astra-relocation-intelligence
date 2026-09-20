"""Engine 7 - scenarios and what-if recalculation.

Two things have to hold for a what-if to be worth anything: the perturbation has
to actually reach the engine it is aimed at, and running it must not disturb the
baseline it is compared against. Both are asserted directly.
"""

from __future__ import annotations

import numpy as np
import pytest

from astra.data.scenarios import BASELINE
from astra.domain.enums import PerturbationKind, PhaseTier, ServiceType
from astra.domain.models import Perturbation
from astra.engines.context import get_context
from astra.engines.scenario import (
    ScenarioError,
    baseline_run,
    closed_segments,
    diff_runs,
    make_scenario,
    perturb_context,
    perturb_sites,
    run_scenario,
)


def change(kind: PerturbationKind, value: float, target: str | None = None, note=None):
    return Perturbation(kind=kind, target=target, value=value, note=note)


# ---------------------------------------------------------------------------
# Perturbations reach the engine they are aimed at
# ---------------------------------------------------------------------------


def test_no_changes_returns_the_same_context_object() -> None:
    context = get_context()
    assert perturb_context(context, []) is context


def test_rainfall_scales_both_intensity_and_extreme_days() -> None:
    context = get_context()
    wetter = perturb_context(
        context, [change(PerturbationKind.RAINFALL_MULTIPLIER, 1.5)]
    )
    assert np.allclose(
        wetter.surfaces.rainfall_intensity_mm,
        context.surfaces.rainfall_intensity_mm * 1.5,
        equal_nan=True,
    )
    assert np.allclose(
        wetter.surfaces.extreme_rain_days,
        context.surfaces.extreme_rain_days * 1.5,
        equal_nan=True,
    )


def test_a_perturbed_context_never_mutates_the_original() -> None:
    context = get_context()
    before = context.surfaces.rainfall_intensity_mm.copy()
    perturb_context(context, [change(PerturbationKind.RAINFALL_MULTIPLIER, 3.0)])
    assert np.allclose(
        context.surfaces.rainfall_intensity_mm, before, equal_nan=True
    )


def test_a_landslide_shift_moves_the_instability_inputs_not_the_score() -> None:
    context = get_context()
    shifted = perturb_context(context, [change(PerturbationKind.LANDSLIDE_SHIFT, 0.2)])
    assert np.nanmean(shifted.surfaces.slope_deg) > np.nanmean(
        context.surfaces.slope_deg
    )
    assert np.nanmax(shifted.surfaces.slope_deg) <= 90.0
    assert np.nanmax(shifted.surfaces.landcover_instability) <= 1.0


def test_a_negative_landslide_shift_reduces_susceptibility() -> None:
    context = get_context()
    calmer = perturb_context(context, [change(PerturbationKind.LANDSLIDE_SHIFT, -0.3)])
    assert np.nanmean(calmer.surfaces.slope_deg) < np.nanmean(
        context.surfaces.slope_deg
    )


def test_population_multiplier_scales_the_named_habitation_only() -> None:
    context = get_context()
    original = {h.id: h.population for h in context.habitations}
    scaled = perturb_context(
        context, [change(PerturbationKind.POPULATION_MULTIPLIER, 2.0, "H-01")]
    )
    after = {h.id: h.population for h in scaled.habitations}
    assert after["H-01"] == round(original["H-01"] * 2.0)
    for habitation_id, population in original.items():
        if habitation_id != "H-01":
            assert after[habitation_id] == population


def test_population_multiplier_with_no_target_scales_everyone() -> None:
    context = get_context()
    scaled = perturb_context(
        context, [change(PerturbationKind.POPULATION_MULTIPLIER, 1.5, None)]
    )
    for before, after in zip(context.habitations, scaled.habitations, strict=True):
        assert after.population == round(before.population * 1.5)
        assert after.demographics.elderly_60_plus <= after.population


def test_a_withdrawn_site_is_removed_not_zeroed() -> None:
    sites = list(get_context().sites)
    remaining = perturb_sites(sites, [change(PerturbationKind.SITE_DISABLED, 1.0, "S-02")])
    assert len(remaining) == len(sites) - 1
    assert all(site.id != "S-02" for site in remaining)


def test_capacity_loss_reduces_every_service_supply_at_that_site() -> None:
    sites = list(get_context().sites)
    damaged = perturb_sites(
        sites, [change(PerturbationKind.SITE_CAPACITY_LOSS, 0.4, "S-05")]
    )
    before = next(site for site in sites if site.id == "S-05")
    after = next(site for site in damaged if site.id == "S-05")
    for supply in before.services:
        matched = after.supply_for(supply.service)
        assert matched is not None
        assert matched.supply == pytest.approx(supply.supply * 0.6)
    assert after.existing_shelter_units <= before.existing_shelter_units


def test_a_service_upgrade_adds_supply_to_the_named_service() -> None:
    sites = list(get_context().sites)
    upgraded = perturb_sites(
        sites,
        [
            change(
                PerturbationKind.SERVICE_UPGRADE,
                5000.0,
                "S-05",
                note=ServiceType.WATER.value,
            )
        ],
    )
    before = next(site for site in sites if site.id == "S-05")
    after = next(site for site in upgraded if site.id == "S-05")
    assert after.supply_for(ServiceType.WATER).supply == pytest.approx(
        before.supply_for(ServiceType.WATER).supply + 5000.0
    )
    assert after.supply_for(ServiceType.SANITATION).supply == pytest.approx(
        before.supply_for(ServiceType.SANITATION).supply
    )


def test_a_service_upgrade_without_a_named_service_is_refused() -> None:
    sites = list(get_context().sites)
    with pytest.raises(ScenarioError, match="must name the service"):
        perturb_sites(sites, [change(PerturbationKind.SERVICE_UPGRADE, 100.0, "S-05")])


def test_road_closures_are_collected_for_the_route_engine() -> None:
    changes = [
        change(PerturbationKind.ROAD_CLOSURE, 1.0, "W1-0"),
        change(PerturbationKind.ROAD_CLOSURE, 1.0, "W2-0"),
        change(PerturbationKind.RAINFALL_MULTIPLIER, 1.2),
    ]
    assert closed_segments(changes) == frozenset({"W1-0", "W2-0"})


def test_a_scenario_records_what_it_changed_in_readable_form() -> None:
    scenario = make_scenario(
        [change(PerturbationKind.RAINFALL_MULTIPLIER, 1.6)], baseline=BASELINE
    )
    assert scenario.derived_from == BASELINE.id
    assert not scenario.is_baseline
    assert scenario.changes[0].describe() == "Rainfall intensity x1.6 across the whole study area"
    assert scenario.disclaimer


# ---------------------------------------------------------------------------
# Running a scenario end to end
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def base():
    return baseline_run()


@pytest.fixture(scope="module")
def wetter(base):
    scenario = make_scenario(
        [change(PerturbationKind.RAINFALL_MULTIPLIER, 1.6)],
        baseline=BASELINE,
        scenario_id="test-wetter",
    )
    return run_scenario(scenario, get_context())


def test_a_wetter_monsoon_enlarges_the_critical_zone(base, wetter) -> None:
    diff = diff_runs(base, wetter)
    assert diff.critical_area_km2_after > diff.critical_area_km2_before
    assert "Critical zone area" in diff.headline


def test_a_wetter_monsoon_cascades_all_the_way_to_the_plan(base, wetter) -> None:
    """The point of a what-if is that it reaches the decision, not just the map."""
    diff = diff_runs(base, wetter)
    assert diff.tier_changes, "phases should move when the hazard surface moves"
    assert diff.assignments, "the plan should change when the phases change"
    assert diff.placed_after != diff.placed_before


def test_the_scenario_run_does_not_disturb_the_baseline(base, wetter) -> None:
    """A simulation that mutated the cached baseline would corrupt every screen."""
    again = baseline_run()
    assert again.plan.objective_value == base.plan.objective_value
    assert again.plan.totals.population_assigned == base.plan.totals.population_assigned
    assert [z.id for z in again.risk.zones] == [z.id for z in base.risk.zones]
    assert [
        (row.id, round(row.priority_score, 4)) for row in again.priority.rows
    ] == [(row.id, round(row.priority_score, 4)) for row in base.priority.rows]


def test_a_scenario_that_changes_nothing_produces_an_empty_diff(base) -> None:
    scenario = make_scenario([], baseline=BASELINE, scenario_id="test-null")
    diff = diff_runs(base, run_scenario(scenario, get_context()))
    assert diff.tier_changes == []
    assert diff.assignments == []
    assert diff.routes == []
    assert diff.placed_after == diff.placed_before
    assert "no zone area, no phase and no assignment" in diff.headline


def test_the_same_scenario_run_twice_gives_the_same_answer(base) -> None:
    scenario = make_scenario(
        [change(PerturbationKind.RAINFALL_MULTIPLIER, 1.3)],
        baseline=BASELINE,
        scenario_id="test-repeat",
    )
    first = diff_runs(base, run_scenario(scenario, get_context()))
    second = diff_runs(base, run_scenario(scenario, get_context()))
    assert first.placed_after == second.placed_after
    assert first.critical_area_km2_after == second.critical_area_km2_after
    assert [entry.habitation_id for entry in first.tier_changes] == [
        entry.habitation_id for entry in second.tier_changes
    ]


def test_withdrawing_a_used_site_moves_its_people_or_leaves_them_unmet(base) -> None:
    used = max(base.plan.site_usage, key=lambda site: base.plan.site_usage[site])
    scenario = make_scenario(
        [change(PerturbationKind.SITE_DISABLED, 1.0, used)],
        baseline=BASELINE,
        scenario_id="test-withdraw",
    )
    run = run_scenario(scenario, get_context())
    assert all(a.site_id != used for a in run.plan.assignments)
    diff = diff_runs(base, run)
    assert any(entry.site_id == used and entry.withdrawn for entry in diff.sites)
    assert diff.placed_after <= diff.placed_before


def test_upgrading_the_bottleneck_service_raises_capacity_and_places_more(base) -> None:
    """The marginal intervention the Sites screen promises, actually carried out."""
    binding = next(
        entry
        for entry in base.capacity
        if entry.suitable and entry.bottleneck is ServiceType.WATER
    )
    scenario = make_scenario(
        [
            change(
                PerturbationKind.SERVICE_UPGRADE,
                40_000.0,
                binding.site.id,
                note=ServiceType.WATER.value,
            )
        ],
        baseline=BASELINE,
        scenario_id="test-upgrade",
    )
    diff = diff_runs(base, run_scenario(scenario, get_context()))
    upgraded = next(entry for entry in diff.sites if entry.site_id == binding.site.id)
    assert upgraded.effective_after > upgraded.effective_before
    assert upgraded.bottleneck_after != upgraded.bottleneck_before
    assert diff.effective_capacity_after > diff.effective_capacity_before


def test_every_resident_is_still_accounted_for_under_a_scenario(wetter) -> None:
    totals = wetter.plan.totals
    assert totals.population_assigned + totals.population_unmet == (
        totals.population_assessed
    )


def test_a_scenario_plan_respects_every_hard_constraint(wetter) -> None:
    from astra.domain.model_config import MODEL_CONFIG
    from astra.engines.optimizer import validate

    validate(
        wetter.plan,
        wetter.plan_inputs.sites,
        wetter.plan_inputs.options,
        wetter.plan_inputs.demand,
        MODEL_CONFIG,
    )
    suitable = {entry.site.id for entry in wetter.capacity if entry.suitable}
    for assignment in wetter.plan.assignments:
        assert assignment.site_id in suitable
        assert assignment.phase in set(PhaseTier)


def test_a_scenario_that_withdraws_every_site_is_refused() -> None:
    sites = get_context().sites
    scenario = make_scenario(
        [change(PerturbationKind.SITE_DISABLED, 1.0, site.id) for site in sites],
        baseline=BASELINE,
        scenario_id="test-empty",
    )
    with pytest.raises(ScenarioError, match="nothing to plan against"):
        run_scenario(scenario, get_context())
