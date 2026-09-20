"""Engine 6 - constrained relocation optimisation.

The small models here are built so the right answer can be worked out by hand.
The corridor tests then assert the properties that must hold on the real data
whatever the plan turns out to be - above all that a returned plan cannot breach
capacity, use an unusable route or pick an unsuitable site, which is CLAUDE.md
section 13 question 10.
"""

from __future__ import annotations

import pytest

from astra.domain.enums import PhaseTier, RoadClass, SolverStatus
from astra.domain.model_config import MODEL_CONFIG
from astra.engines.livelihood import CONNECTIVITY_DISRUPTION, livelihood_disruption
from astra.engines.optimizer import (
    Option,
    PlanValidationError,
    RelocationOptimiser,
    SiteState,
    validate,
)

OPT = MODEL_CONFIG.optimiser


def site(site_id: str, capacity: int, *, shares=(0.45, 0.75, 1.0)) -> SiteState:
    return SiteState(
        site_id=site_id,
        effective_capacity=capacity,
        soft_capacity=int(capacity * OPT.site_soft_capacity_share.value),
        phase_ceiling={
            PhaseTier.IMMEDIATE: int(capacity * shares[0]),
            PhaseTier.SHORT_TERM: int(capacity * shares[1]),
            PhaseTier.MEDIUM_TERM: int(capacity * shares[2]),
        },
    )


def option(
    habitation_id: str,
    site_id: str,
    *,
    phase: PhaseTier = PhaseTier.MEDIUM_TERM,
    population: int = 100,
    priority: float = 50.0,
    travel: float = 30.0,
    risk: float = 0.1,
    livelihood: float = 0.3,
    delay: int = 0,
) -> Option:
    return Option(
        delay_steps=delay,
        habitation_id=habitation_id,
        site_id=site_id,
        phase=phase,
        population=population,
        priority=priority,
        travel_time_min=travel,
        route_risk=risk,
        route_reliability=1.0 - risk,
        livelihood_disruption=livelihood,
    )


# ---------------------------------------------------------------------------
# The model does what it says
# ---------------------------------------------------------------------------


def test_everyone_moves_when_there_is_room_for_everyone() -> None:
    plan = RelocationOptimiser().solve(
        [option("H-01", "S-01", population=100)], [site("S-01", 500)], {"H-01": 100}
    )
    assert plan.status is SolverStatus.OPTIMAL
    assert plan.totals.population_assigned == 100
    assert plan.totals.population_unmet == 0


def test_nobody_is_moved_to_a_site_with_no_option() -> None:
    plan = RelocationOptimiser().solve([], [site("S-01", 500)], {"H-01": 100})
    assert plan.totals.population_assigned == 0
    assert plan.unmet["H-01"] == 100
    assert plan.assignments == []


def test_a_site_is_never_filled_beyond_its_effective_capacity() -> None:
    plan = RelocationOptimiser().solve(
        [
            option("H-01", "S-01", population=200),
            option("H-02", "S-01", population=200),
        ],
        [site("S-01", 250)],
        {"H-01": 200, "H-02": 200},
    )
    assert sum(a.people for a in plan.for_site("S-01")) <= 250
    assert plan.totals.population_unmet == 150


def test_the_phase_ramp_limits_what_can_move_early() -> None:
    """A site is not built out on day one, and the plan may not pretend it is."""
    plan = RelocationOptimiser().solve(
        [option("H-01", "S-01", phase=PhaseTier.IMMEDIATE, population=400)],
        [site("S-01", 400)],
        {"H-01": 400},
    )
    immediate = sum(
        a.people for a in plan.assignments if a.phase is PhaseTier.IMMEDIATE
    )
    assert immediate <= int(400 * OPT.phase_capacity_share_immediate.value)


def test_a_village_may_move_in_stages_to_the_same_site() -> None:
    plan = RelocationOptimiser().solve(
        [
            option("H-01", "S-01", phase=PhaseTier.IMMEDIATE, population=400),
            option(
                "H-01", "S-01", phase=PhaseTier.SHORT_TERM, population=400, delay=1
            ),
            option(
                "H-01", "S-01", phase=PhaseTier.MEDIUM_TERM, population=400, delay=2
            ),
        ],
        [site("S-01", 400)],
        {"H-01": 400},
    )
    assert plan.totals.population_assigned == 400
    assert plan.totals.habitations_split == 0, "one site is not a split community"
    assert len({a.phase for a in plan.assignments}) > 1


def test_higher_priority_wins_contested_capacity() -> None:
    plan = RelocationOptimiser().solve(
        [
            option("H-low", "S-01", population=100, priority=20.0),
            option("H-high", "S-01", population=100, priority=95.0),
        ],
        [site("S-01", 100)],
        {"H-low": 100, "H-high": 100},
    )
    assert sum(a.people for a in plan.for_habitation("H-high")) == 100
    assert plan.unmet["H-low"] == 100


def test_the_cheaper_route_is_preferred_when_both_fit() -> None:
    plan = RelocationOptimiser().solve(
        [
            option("H-01", "S-far", travel=120.0),
            option("H-01", "S-near", travel=10.0),
        ],
        [site("S-far", 500), site("S-near", 500)],
        {"H-01": 100},
    )
    assert {a.site_id for a in plan.assignments} == {"S-near"}


def test_a_more_disruptive_destination_loses_to_a_less_disruptive_one() -> None:
    plan = RelocationOptimiser().solve(
        [
            option("H-01", "S-hard", livelihood=0.9),
            option("H-01", "S-easy", livelihood=0.1),
        ],
        [site("S-hard", 500), site("S-easy", 500)],
        {"H-01": 100},
    )
    assert {a.site_id for a in plan.assignments} == {"S-easy"}


def test_stranding_people_is_never_preferred_to_using_a_site_s_last_places() -> None:
    """The overload penalty is a preference, not a hard cap.

    An earlier calibration made it large enough to act as one, and the solver
    responded by leaving people in a red zone to protect a 15% margin. That is
    not a plan an SDMA can defend, so it is asserted against.
    """
    plan = RelocationOptimiser().solve(
        [option("H-01", "S-01", population=100, priority=60.0)],
        [site("S-01", 100)],
        {"H-01": 100},
    )
    assert plan.totals.population_unmet == 0
    assert plan.site_usage["S-01"] == 100


def test_splitting_across_two_sites_happens_when_it_places_more_people() -> None:
    plan = RelocationOptimiser().solve(
        [
            option("H-01", "S-01", population=200),
            option("H-01", "S-02", population=200),
        ],
        [site("S-01", 120), site("S-02", 120)],
        {"H-01": 200},
    )
    assert plan.totals.population_assigned == 200
    assert plan.totals.habitations_split == 1


def test_an_assignment_is_never_smaller_than_the_household_floor() -> None:
    floor = int(OPT.min_assignment_block.value)
    plan = RelocationOptimiser().solve(
        [
            option("H-01", "S-01", population=200),
            option("H-01", "S-02", population=200),
        ],
        [site("S-01", 195), site("S-02", 500)],
        {"H-01": 200},
    )
    for assignment in plan.assignments:
        assert assignment.people >= floor


def test_the_plan_is_reproducible_across_runs() -> None:
    options = [
        option("H-01", "S-01", population=200, priority=61.0),
        option("H-01", "S-02", population=200, priority=61.0),
        option("H-02", "S-01", population=180, priority=58.0),
        option("H-02", "S-02", population=180, priority=58.0),
    ]
    sites = [site("S-01", 200), site("S-02", 150)]
    demand = {"H-01": 200, "H-02": 180}
    first = RelocationOptimiser().solve(options, sites, demand)
    second = RelocationOptimiser().solve(options, sites, demand)
    assert [
        (a.habitation_id, a.site_id, a.phase, a.people) for a in first.assignments
    ] == [(a.habitation_id, a.site_id, a.phase, a.people) for a in second.assignments]
    assert first.objective_value == second.objective_value


# ---------------------------------------------------------------------------
# Post-solve validation. The answer to "can it breach capacity?"
# ---------------------------------------------------------------------------


def test_validation_rejects_a_plan_that_breaches_capacity() -> None:
    from astra.engines.optimizer import Assignment, Plan, PlanTotals

    sites = [site("S-01", 50)]
    options = [option("H-01", "S-01", population=100)]
    forged = Plan(
        assignments=[
            Assignment(
                habitation_id="H-01",
                site_id="S-01",
                phase=PhaseTier.MEDIUM_TERM,
                people=100,
                travel_time_min=30.0,
                route_reliability=0.9,
                route_risk=0.1,
                livelihood_disruption=0.3,
                objective_contribution=0.0,
            )
        ],
        unmet={"H-01": 0},
        status=SolverStatus.OPTIMAL,
        objective_value=0.0,
        objective_terms={},
        solve_ms=0.0,
        totals=PlanTotals(100, 100, 0, 0.0, 0.0, 0.0, 0.0, 1, 0),
    )
    with pytest.raises(PlanValidationError, match="effective capacity"):
        validate(forged, sites, options, {"H-01": 100}, MODEL_CONFIG)


def test_validation_rejects_an_assignment_that_was_never_an_option() -> None:
    from astra.engines.optimizer import Assignment, Plan, PlanTotals

    forged = Plan(
        assignments=[
            Assignment(
                habitation_id="H-01",
                site_id="S-forbidden",
                phase=PhaseTier.MEDIUM_TERM,
                people=10,
                travel_time_min=30.0,
                route_reliability=0.9,
                route_risk=0.1,
                livelihood_disruption=0.3,
                objective_contribution=0.0,
            )
        ],
        unmet={"H-01": 90},
        status=SolverStatus.OPTIMAL,
        objective_value=0.0,
        objective_terms={},
        solve_ms=0.0,
        totals=PlanTotals(100, 10, 90, 0.0, 0.0, 0.0, 0.0, 1, 0),
    )
    with pytest.raises(PlanValidationError, match="not an allowed option"):
        validate(
            forged,
            [site("S-forbidden", 500)],
            [option("H-01", "S-01")],
            {"H-01": 100},
            MODEL_CONFIG,
        )


def test_validation_rejects_a_plan_that_loses_track_of_people() -> None:
    from astra.engines.optimizer import Plan, PlanTotals

    forged = Plan(
        assignments=[],
        unmet={"H-01": 40},
        status=SolverStatus.OPTIMAL,
        objective_value=0.0,
        objective_terms={},
        solve_ms=0.0,
        totals=PlanTotals(100, 0, 40, 0.0, 0.0, 0.0, 0.0, 0, 0),
    )
    with pytest.raises(PlanValidationError, match="does not account for"):
        validate(forged, [site("S-01", 500)], [], {"H-01": 100}, MODEL_CONFIG)


# ---------------------------------------------------------------------------
# The fallback
# ---------------------------------------------------------------------------


def test_the_fallback_produces_a_valid_plan_and_labels_itself() -> None:
    options = [
        option("H-01", "S-01", population=200, priority=61.0),
        option("H-02", "S-01", population=180, priority=58.0),
    ]
    sites = [site("S-01", 250)]
    plan = RelocationOptimiser().greedy(options, sites, {"H-01": 200, "H-02": 180})
    assert plan.status is SolverStatus.FALLBACK
    assert any("greedy fallback" in note for note in plan.notes)
    assert sum(a.people for a in plan.for_site("S-01")) <= 250


def test_the_fallback_is_never_better_than_the_solver() -> None:
    """If it were, the solver would be wrong. Asserted rather than assumed."""
    options = [
        option("H-01", "S-01", population=200, priority=61.0, travel=90.0),
        option("H-01", "S-02", population=200, priority=61.0, travel=20.0),
        option("H-02", "S-01", population=180, priority=58.0, travel=15.0),
        option("H-02", "S-02", population=180, priority=58.0, travel=80.0),
    ]
    sites = [site("S-01", 200), site("S-02", 200)]
    demand = {"H-01": 200, "H-02": 180}
    solved = RelocationOptimiser().solve(options, sites, demand)
    fallback = RelocationOptimiser().greedy(options, sites, demand)
    assert solved.objective_terms["travel_time"] <= (
        fallback.objective_terms["travel_time"] + 1e-6
    )


# ---------------------------------------------------------------------------
# Livelihood disruption
# ---------------------------------------------------------------------------


def test_livelihood_disruption_is_the_weighted_sum_of_its_four_components() -> None:
    result = livelihood_disruption(
        habitation_id="H-01",
        site_id="S-01",
        commute_min=45.0,
        commute_reliability=0.8,
        worst_road_class=RoadClass.DISTRICT_ROAD,
        market_access_min=30.0,
    )
    assert len(result.factors) == 4
    assert result.value == pytest.approx(
        sum(f.contribution for f in result.factors), abs=1e-4
    )
    assert sum(f.weight for f in result.factors) == pytest.approx(1.0)


def test_a_perfect_destination_disrupts_nothing() -> None:
    result = livelihood_disruption(
        habitation_id="H-01",
        site_id="S-01",
        commute_min=0.0,
        commute_reliability=1.0,
        worst_road_class=RoadClass.NATIONAL_HIGHWAY,
        market_access_min=0.0,
    )
    assert result.value == pytest.approx(0.0)


def test_a_worse_road_class_disrupts_more_at_the_same_travel_time() -> None:
    def score(road_class: RoadClass) -> float:
        return livelihood_disruption(
            habitation_id="H-01",
            site_id="S-01",
            commute_min=30.0,
            commute_reliability=0.9,
            worst_road_class=road_class,
            market_access_min=20.0,
        ).value

    assert score(RoadClass.TRACK) > score(RoadClass.DISTRICT_ROAD)
    assert score(RoadClass.DISTRICT_ROAD) > score(RoadClass.NATIONAL_HIGHWAY)
    assert CONNECTIVITY_DISRUPTION[RoadClass.TRACK] == 1.0


def test_an_unreachable_livelihood_centre_is_maximum_disruption_not_missing_data() -> None:
    result = livelihood_disruption(
        habitation_id="H-01",
        site_id="S-01",
        commute_min=0.0,
        commute_reliability=0.0,
        worst_road_class=RoadClass.TRACK,
        market_access_min=0.0,
        reachable=False,
    )
    assert result.value == 1.0
    assert not result.reachable


def test_livelihood_disruption_is_not_a_distance_rule() -> None:
    """Same commute, different roads, different answer. That is the whole point."""
    near_bad_road = livelihood_disruption(
        habitation_id="H-01",
        site_id="S-01",
        commute_min=30.0,
        commute_reliability=0.4,
        worst_road_class=RoadClass.TRACK,
        market_access_min=55.0,
    )
    near_good_road = livelihood_disruption(
        habitation_id="H-01",
        site_id="S-02",
        commute_min=30.0,
        commute_reliability=0.98,
        worst_road_class=RoadClass.STATE_HIGHWAY,
        market_access_min=5.0,
    )
    assert near_bad_road.commute_min == near_good_road.commute_min
    assert near_bad_road.value > near_good_road.value + 0.2


# ---------------------------------------------------------------------------
# Against the real corridor
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corridor_plan():
    from astra.engines.optimizer_service import solve_plan

    return solve_plan()


def test_the_solver_reaches_an_answer_on_the_real_corridor(corridor_plan) -> None:
    plan, inputs = corridor_plan
    assert plan.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
    assert plan.assignments
    assert inputs.options


def test_the_plan_cannot_breach_capacity_use_a_bad_route_or_an_unsuitable_site(
    corridor_plan,
) -> None:
    """CLAUDE.md section 13 question 10, asserted on the real data."""
    from astra.engines.capacity_service import baseline_capacity
    from astra.engines.routes_service import baseline_routes

    plan, inputs = corridor_plan
    validate(plan, inputs.sites, inputs.options, inputs.demand, MODEL_CONFIG)

    suitable = {e.site.id for e in baseline_capacity() if e.suitable}
    capacity = {e.site.id: e.effective_capacity for e in baseline_capacity()}
    corridor = baseline_routes()
    threshold = MODEL_CONFIG.route.min_reliability_threshold.value

    for assignment in plan.assignments:
        assert assignment.site_id in suitable, "an unsuitable site was used"
        pair = corridor.pair(assignment.habitation_id, assignment.site_id)
        assert pair is not None and pair.feasible, "an unusable route was used"
        assert pair.best.reliability >= threshold
    for site_id, people in plan.site_usage.items():
        assert people <= capacity[site_id]


def test_every_resident_is_accounted_for(corridor_plan) -> None:
    plan, inputs = corridor_plan
    for habitation_id, population in inputs.demand.items():
        assigned = sum(a.people for a in plan.for_habitation(habitation_id))
        assert assigned + plan.unmet[habitation_id] == population
    assert (
        plan.totals.population_assigned + plan.totals.population_unmet
        == plan.totals.population_assessed
    )


def test_every_rejected_pairing_names_the_constraint_that_removed_it(
    corridor_plan,
) -> None:
    plan, inputs = corridor_plan
    assert inputs.rejected, "the corridor should exercise the hard constraints"
    allowed = {(o.habitation_id, o.site_id) for o in inputs.options}
    for entry in inputs.rejected:
        assert entry.reason
        assert entry.detail
        assert (entry.habitation_id, entry.site_id) not in allowed


def test_unmet_demand_is_explained_not_just_counted(corridor_plan) -> None:
    from astra.engines.optimizer_service import (
        UNMET_CAPACITY_EXHAUSTED,
        UNMET_NO_DESTINATION,
        UNMET_OUTWEIGHED,
        explain_unmet,
    )

    plan, inputs = corridor_plan
    reasons = explain_unmet(plan, inputs)
    assert sum(entry.people for entry in reasons) == plan.totals.population_unmet
    valid = {UNMET_NO_DESTINATION, UNMET_CAPACITY_EXHAUSTED, UNMET_OUTWEIGHED}
    for entry in reasons:
        assert entry.reason in valid
        assert entry.detail


def test_stranded_capacity_is_measured_against_who_can_reach_it(corridor_plan) -> None:
    from astra.engines.optimizer_service import stranded_capacity

    plan, inputs = corridor_plan
    stranded = stranded_capacity(plan, inputs)
    for entry in stranded:
        assert entry.stranded_places == entry.unused - entry.reachable_unmet_people
        assert entry.stranded_places > 0
        assert entry.unused <= entry.capacity


def test_a_habitation_with_no_destination_is_flagged_not_silently_ranked(
    corridor_plan,
) -> None:
    from astra.engines.optimizer_service import capacity_blocked

    plan, inputs = corridor_plan
    blocked = capacity_blocked(plan, inputs)
    served = {o.habitation_id for o in inputs.options}
    for habitation_id in blocked:
        assert habitation_id not in served
        assert plan.unmet[habitation_id] > 0


def test_the_counterfactual_for_a_blocked_site_names_the_hard_constraint(
    corridor_plan,
) -> None:
    from astra.engines.optimizer_service import counterfactual

    plan, inputs = corridor_plan
    blocked = next(
        entry
        for entry in inputs.rejected
        if entry.habitation_id in inputs.demand
        and entry.reason != "HABITATION_NOT_PRIORITISED_FOR_RELOCATION"
    )
    result = counterfactual(
        blocked.habitation_id, blocked.site_id, inputs=inputs, baseline=plan
    )
    assert not result.feasible
    assert result.reason == blocked.reason
    assert result.objective_delta is None


def test_the_counterfactual_for_an_allowed_site_comes_from_a_real_resolve(
    corridor_plan,
) -> None:
    from astra.engines.optimizer_service import counterfactual

    plan, inputs = corridor_plan
    chosen = {(a.habitation_id, a.site_id) for a in plan.assignments}
    alternative = next(
        o for o in inputs.options if (o.habitation_id, o.site_id) not in chosen
    )
    result = counterfactual(
        alternative.habitation_id, alternative.site_id, inputs=inputs, baseline=plan
    )
    assert result.feasible
    assert result.objective_forced is not None
    assert result.objective_delta == pytest.approx(
        result.objective_forced - result.objective_baseline, abs=0.05
    )
    assert result.objective_delta >= -1e-6, (
        "an alternative that beat the chosen plan would mean the solver was wrong"
    )


def test_the_corridor_plan_is_deterministic(corridor_plan) -> None:
    from astra.engines.optimizer_service import solve_plan

    plan, inputs = corridor_plan
    again, _ = solve_plan(inputs)
    assert [
        (a.habitation_id, a.site_id, a.phase, a.people) for a in again.assignments
    ] == [(a.habitation_id, a.site_id, a.phase, a.people) for a in plan.assignments]


def test_moving_people_sooner_is_preferred_when_both_phases_fit() -> None:
    """'Immediate' has to mean something to the objective, not only to the label.

    Without a delay term the solver is indifferent between moving a habitation
    now and moving it in the medium term, and it will pick whichever the search
    happens to reach first. That is not a phased relocation plan.
    """
    plan = RelocationOptimiser().solve(
        [
            option("H-01", "S-01", phase=PhaseTier.IMMEDIATE, population=100),
            option(
                "H-01", "S-01", phase=PhaseTier.MEDIUM_TERM, population=100, delay=2
            ),
        ],
        [site("S-01", 500)],
        {"H-01": 100},
    )
    assert {a.phase for a in plan.assignments} == {PhaseTier.IMMEDIATE}
    assert plan.objective_terms["phase_delay"] == 0.0


def test_delay_is_accepted_rather_than_leaving_people_behind() -> None:
    """A late move beats no move. The delay penalty must never invert that."""
    plan = RelocationOptimiser().solve(
        [
            option(
                "H-01",
                "S-01",
                phase=PhaseTier.MEDIUM_TERM,
                population=100,
                priority=95.0,
                delay=2,
            )
        ],
        [site("S-01", 500)],
        {"H-01": 100},
    )
    assert plan.totals.population_unmet == 0
    assert plan.objective_terms["phase_delay"] > 0


def test_the_delay_penalty_orders_the_plan_without_shrinking_it(corridor_plan) -> None:
    """Delay must discipline *when* people move, never *whether* they move.

    A penalty large enough to make a late move not worth making turns a phasing
    term into a coverage cut, which is the wrong trade and is exactly what
    happened at four times the configured value.
    """
    from astra.engines.optimizer import RelocationOptimiser

    plan, inputs = corridor_plan
    without_delay = MODEL_CONFIG.model_copy(deep=True)
    object.__setattr__(without_delay.optimiser.beta_phase_delay, "value", 0.0)
    unpenalised = RelocationOptimiser(without_delay).solve(
        inputs.options, inputs.sites, inputs.demand, rejected=inputs.rejected
    )
    assert (
        plan.totals.population_assigned == unpenalised.totals.population_assigned
    ), "the delay penalty changed how many people are placed, not just when"


def test_the_plan_moves_people_as_early_as_the_ramp_allows(corridor_plan) -> None:
    from astra.engines.optimizer import PHASE_ORDER

    plan, inputs = corridor_plan
    immediate = sum(
        a.people for a in plan.assignments if a.phase is PHASE_ORDER[0]
    )
    ceiling = sum(
        site.phase_ceiling[PHASE_ORDER[0]] for site in inputs.sites
    )
    assert immediate > 0
    assert immediate <= ceiling
