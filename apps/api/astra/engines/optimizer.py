"""Engine 6 - constrained relocation optimisation.

The analytical heart. Every earlier engine produces a number about one thing:
how dangerous a place is, how many people a site can hold, whether a road
survives. This one has to reconcile all of them into a single answer to the only
question an SDMA actually asks: **who goes where, in what order.**

That is an assignment problem with hard constraints and competing objectives, so
it is solved as one, with OR-Tools CP-SAT::

    variables    x[h][s] = integer people moved from habitation h to site s
    subject to   site capacity per phase, route feasibility, travel ceilings,
                 suitability gates, and a household-integrity floor
    minimising   a weighted sum of unmet demand, travel burden, route risk,
                 site overload, livelihood disruption and fragmentation

**A greedy sort is not this.** A greedy assignment fills the best site with the
highest-priority habitation until it is full, then moves on - which produces
plans where the second habitation is stranded because the first took capacity it
did not need as badly. The solver trades those against each other. The greedy
fallback below exists as demo insurance and is labelled `FALLBACK` in the
response so nobody mistakes one for the other.

**Every constraint is hard, not a penalty.** A site that fails a suitability gate
and a route below the reliability threshold are not expensive options; they are
absent from the model. Post-solve validation then asserts that the plan that came
back respects all of them, because a solver bug that silently breaches capacity
is worse than no solver at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from astra.domain.enums import PhaseTier, SolverStatus
from astra.domain.model_config import MODEL_CONFIG, AstraModelConfig

FORMULA_OBJECTIVE = "optimiser.objective"

#: CP-SAT works in integers. Objective terms are scaled by this and rounded, so
#: a coefficient of 0.001 still separates two plans instead of vanishing.
OBJECTIVE_SCALE = 1000

PHASE_ORDER: tuple[PhaseTier, ...] = (
    PhaseTier.IMMEDIATE,
    PhaseTier.SHORT_TERM,
    PhaseTier.MEDIUM_TERM,
)

#: A variable is one habitation, one site, one phase. A village is allowed to
#: move in stages - which is what a phased relocation *is* - so the same pairing
#: can carry people in more than one phase.
OptionKey = tuple[str, str, PhaseTier]


@dataclass(frozen=True)
class Option:
    """One habitation-to-site pairing the solver is allowed to consider.

    An option exists only where the site passes every suitability gate and the
    route clears the reliability threshold and the phase travel ceiling. Where
    one of those fails there is no variable at all, which is what makes those
    constraints hard rather than expensive.
    """

    habitation_id: str
    site_id: str
    phase: PhaseTier
    population: int
    priority: float
    travel_time_min: float
    route_risk: float
    route_reliability: float
    livelihood_disruption: float
    #: Phases later than the habitation's earliest eligible one. Zero means this
    #: option moves people as soon as the phasing engine says they should go.
    delay_steps: int = 0

    @property
    def key(self) -> OptionKey:
        return (self.habitation_id, self.site_id, self.phase)

    def unit_cost(self, config: AstraModelConfig) -> float:
        """Objective cost of moving one person along this option.

        Includes the delay term, because moving someone in the medium term and
        moving them now are not the same act. Without it the solver is
        indifferent between the two and 'Immediate' means nothing.
        """
        settings = config.optimiser
        return (
            settings.beta_travel_time.value * self.travel_time_min
            + settings.beta_route_risk.value * self.route_risk
            + settings.beta_livelihood_disruption.value * self.livelihood_disruption
            + settings.beta_phase_delay.value
            * self.delay_steps
            * (self.priority / 100.0)
        )


@dataclass(frozen=True)
class RejectedOption:
    """A pairing the solver was never offered, and the constraint that removed it."""

    habitation_id: str
    site_id: str
    reason: str
    detail: str


@dataclass(frozen=True)
class SiteState:
    """A destination's capacity, as the solver is allowed to use it."""

    site_id: str
    effective_capacity: int
    soft_capacity: int
    phase_ceiling: dict[PhaseTier, int]


@dataclass(frozen=True)
class Assignment:
    """One movement in the plan, with everything that justifies it."""

    habitation_id: str
    site_id: str
    phase: PhaseTier
    people: int
    travel_time_min: float
    route_reliability: float
    route_risk: float
    livelihood_disruption: float
    objective_contribution: float
    delay_steps: int = 0


@dataclass
class PlanTotals:
    population_assessed: int
    population_assigned: int
    population_unmet: int
    person_minutes: float
    mean_travel_time_min: float
    mean_route_reliability: float
    mean_livelihood_disruption: float
    sites_used: int
    habitations_split: int


@dataclass
class Plan:
    """The solved relocation plan."""

    assignments: list[Assignment]
    unmet: dict[str, int]
    status: SolverStatus
    objective_value: float
    objective_terms: dict[str, float]
    solve_ms: float
    totals: PlanTotals
    rejected: list[RejectedOption] = field(default_factory=list)
    site_usage: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def for_habitation(self, habitation_id: str) -> list[Assignment]:
        return [a for a in self.assignments if a.habitation_id == habitation_id]

    def for_site(self, site_id: str) -> list[Assignment]:
        return [a for a in self.assignments if a.site_id == site_id]


class PlanValidationError(RuntimeError):
    """A solved plan breached a hard constraint. Fatal, never rendered."""


class RelocationOptimiser:
    """CP-SAT assignment of people to sites, with a deterministic fallback."""

    def __init__(self, config: AstraModelConfig | None = None) -> None:
        self.config = config or MODEL_CONFIG

    # -- the model ----------------------------------------------------------

    def solve(
        self,
        options: list[Option],
        sites: list[SiteState],
        demand: dict[str, int],
        *,
        rejected: list[RejectedOption] | None = None,
        forced: tuple[str, str, int] | None = None,
        time_limit_s: float | None = None,
    ) -> Plan:
        """Solve the assignment.

        ``forced`` pins at least that many people from a habitation onto a site,
        which is how the counterfactual "why not this site" is answered: by
        re-solving with the answer imposed and reporting what actually happens.
        """
        settings = self.config.optimiser
        model = cp_model.CpModel()
        site_by_id = {site.site_id: site for site in sites}
        priorities = {o.habitation_id: o.priority for o in options}

        # One integer variable per allowed pairing, plus a boolean that carries
        # the household-integrity floor: an assignment is zero, or it is at least
        # a whole block of people.
        moved: dict[OptionKey, cp_model.IntVar] = {}
        used: dict[OptionKey, cp_model.IntVar] = {}
        floor = int(settings.min_assignment_block.value)
        for option in options:
            key = option.key
            capacity = min(
                option.population, site_by_id[option.site_id].effective_capacity
            )
            name = f"{key[0]},{key[1]},{key[2].value}"
            variable = model.NewIntVar(0, capacity, f"x[{name}]")
            flag = model.NewBoolVar(f"y[{name}]")
            model.Add(variable <= capacity * flag)
            model.Add(variable >= min(floor, capacity) * flag)
            model.Add(variable == 0).OnlyEnforceIf(flag.Not())
            moved[key] = variable
            used[key] = flag

        by_habitation: dict[str, list[Option]] = {}
        by_site: dict[str, list[Option]] = {}
        by_habitation_site: dict[tuple[str, str], list[Option]] = {}
        for option in options:
            by_habitation.setdefault(option.habitation_id, []).append(option)
            by_site.setdefault(option.site_id, []).append(option)
            by_habitation_site.setdefault(
                (option.habitation_id, option.site_id), []
            ).append(option)

        # Nobody is moved twice, and nobody is invented.
        unmet_vars: dict[str, cp_model.IntVar] = {}
        for habitation_id, population in demand.items():
            assigned = [moved[o.key] for o in by_habitation.get(habitation_id, [])]
            unmet = model.NewIntVar(0, population, f"unmet[{habitation_id}]")
            if assigned:
                model.Add(sum(assigned) + unmet == population)
            else:
                model.Add(unmet == population)
            unmet_vars[habitation_id] = unmet

        # A site holds no more than its effective capacity, and no more than its
        # phase ramp allows by the end of each phase.
        overload_vars: dict[str, cp_model.IntVar] = {}
        for site in sites:
            site_options = by_site.get(site.site_id, [])
            if not site_options:
                continue
            total = [moved[o.key] for o in site_options]
            model.Add(sum(total) <= site.effective_capacity)
            for index, phase in enumerate(PHASE_ORDER):
                cumulative = [
                    moved[o.key]
                    for o in site_options
                    if PHASE_ORDER.index(o.phase) <= index
                ]
                if cumulative:
                    model.Add(sum(cumulative) <= site.phase_ceiling[phase])
            overload = model.NewIntVar(0, site.effective_capacity, f"over[{site.site_id}]")
            model.Add(overload >= sum(total) - site.soft_capacity)
            overload_vars[site.site_id] = overload

        if forced is not None:
            habitation_id, site_id, people = forced
            variables = [
                moved[o.key] for o in by_habitation_site.get((habitation_id, site_id), [])
            ]
            if not variables:
                # The pairing is not in the model at all, which is itself the
                # answer. Report it rather than solving a different problem.
                return _no_such_option(habitation_id, site_id, rejected or [])
            model.Add(sum(variables) >= people)

        # -- objective ------------------------------------------------------
        terms: list[cp_model.LinearExpr] = []
        for option in options:
            coefficient = round(option.unit_cost(self.config) * OBJECTIVE_SCALE)
            terms.append(coefficient * moved[option.key])

        # Fragmentation charges the *splits between places*, not between phases.
        # A village moved to one site over two phases is a phased relocation; the
        # same village moved to two sites is a divided community, and only the
        # second is what this term is about. `served` is one when the habitation
        # goes anywhere at all, so the count of extra destinations is
        # (sites used) - (habitations served).
        fragmentation_coefficient = round(
            settings.beta_fragmentation.value * OBJECTIVE_SCALE
        )
        for habitation_id, habitation_options in by_habitation.items():
            site_flags = []
            for site_id in sorted({o.site_id for o in habitation_options}):
                phase_flags = [
                    used[o.key] for o in by_habitation_site[(habitation_id, site_id)]
                ]
                site_used = model.NewBoolVar(f"site_used[{habitation_id},{site_id}]")
                model.AddMaxEquality(site_used, phase_flags)
                site_flags.append(site_used)
            served = model.NewBoolVar(f"served[{habitation_id}]")
            model.AddMaxEquality(served, site_flags)
            terms.append(fragmentation_coefficient * sum(site_flags))
            terms.append(-fragmentation_coefficient * served)

        for habitation_id, unmet in unmet_vars.items():
            coefficient = round(
                settings.beta_unmet_demand.value
                * (priorities.get(habitation_id, 0.0) / 100.0)
                * OBJECTIVE_SCALE
            )
            terms.append(coefficient * unmet)
        for overload in overload_vars.values():
            terms.append(
                round(settings.beta_site_overload.value * OBJECTIVE_SCALE) * overload
            )
        model.Minimize(sum(terms))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(
            time_limit_s if time_limit_s is not None else settings.solver_time_limit_s.value
        )
        solver.parameters.random_seed = int(settings.solver_seed.value)
        solver.parameters.num_search_workers = 1  # determinism over speed
        result = solver.Solve(model)

        if result not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            status = (
                SolverStatus.INFEASIBLE
                if result == cp_model.INFEASIBLE
                else SolverStatus.FALLBACK
            )
            if status is SolverStatus.INFEASIBLE:
                return _infeasible(demand, rejected or [], solver.WallTime() * 1000.0)
            return self.greedy(options, sites, demand, rejected=rejected)

        assignments: list[Assignment] = []
        for option in options:
            people = int(solver.Value(moved[option.key]))
            if people <= 0:
                continue
            assignments.append(
                Assignment(
                    habitation_id=option.habitation_id,
                    site_id=option.site_id,
                    phase=option.phase,
                    people=people,
                    travel_time_min=option.travel_time_min,
                    route_reliability=option.route_reliability,
                    route_risk=option.route_risk,
                    livelihood_disruption=option.livelihood_disruption,
                    objective_contribution=round(
                        option.unit_cost(self.config) * people, 2
                    ),
                    delay_steps=option.delay_steps,
                )
            )
        unmet = {
            habitation_id: int(solver.Value(variable))
            for habitation_id, variable in unmet_vars.items()
        }
        plan = _assemble(
            assignments,
            unmet,
            demand,
            status=(
                SolverStatus.OPTIMAL if result == cp_model.OPTIMAL else SolverStatus.FEASIBLE
            ),
            objective_value=solver.ObjectiveValue() / OBJECTIVE_SCALE,
            solve_ms=solver.WallTime() * 1000.0,
            config=self.config,
            rejected=rejected or [],
            priorities=priorities,
            sites=sites,
        )
        validate(plan, sites, options, demand, self.config)
        return plan

    # -- fallback -----------------------------------------------------------

    def greedy(
        self,
        options: list[Option],
        sites: list[SiteState],
        demand: dict[str, int],
        *,
        rejected: list[RejectedOption] | None = None,
    ) -> Plan:
        """Deterministic priority-ordered fill. Demo insurance, labelled as such.

        Highest priority first, each habitation to its cheapest available option.
        This is the plan a sorted table would produce, and it is kept only so a
        solver timeout on demo day degrades to something honest rather than to
        nothing. The response says `FALLBACK` and the interface says so too.
        """
        remaining = {site.site_id: site.effective_capacity for site in sites}
        phase_used: dict[tuple[str, PhaseTier], int] = {}
        site_by_id = {site.site_id: site for site in sites}
        floor = int(self.config.optimiser.min_assignment_block.value)

        by_habitation: dict[str, list[Option]] = {}
        for option in options:
            by_habitation.setdefault(option.habitation_id, []).append(option)

        order = sorted(
            demand,
            key=lambda h: -max(
                (o.priority for o in by_habitation.get(h, [])), default=0.0
            ),
        )
        assignments: list[Assignment] = []
        unmet: dict[str, int] = {}
        for habitation_id in order:
            left = demand[habitation_id]
            # Earliest phase first, then cheapest: a fallback that moved people
            # late when an early slot existed would not be a relocation plan.
            choices = sorted(
                by_habitation.get(habitation_id, []),
                key=lambda o: (PHASE_ORDER.index(o.phase), o.unit_cost(self.config)),
            )
            for option in choices:
                if left <= 0:
                    break
                site = site_by_id[option.site_id]
                index = PHASE_ORDER.index(option.phase)
                headroom = remaining[option.site_id]
                for phase in PHASE_ORDER[index:]:
                    used_by_phase = phase_used.get((option.site_id, phase), 0)
                    headroom = min(headroom, site.phase_ceiling[phase] - used_by_phase)
                take = max(min(left, headroom), 0)
                if take < min(floor, left):
                    continue
                remaining[option.site_id] -= take
                for phase in PHASE_ORDER[index:]:
                    phase_used[(option.site_id, phase)] = (
                        phase_used.get((option.site_id, phase), 0) + take
                    )
                left -= take
                assignments.append(
                    Assignment(
                        habitation_id=habitation_id,
                        site_id=option.site_id,
                        phase=option.phase,
                        people=take,
                        travel_time_min=option.travel_time_min,
                        route_reliability=option.route_reliability,
                        route_risk=option.route_risk,
                        livelihood_disruption=option.livelihood_disruption,
                        objective_contribution=round(
                            option.unit_cost(self.config) * take, 2
                        ),
                        delay_steps=option.delay_steps,
                    )
                )
            unmet[habitation_id] = left

        plan = _assemble(
            assignments,
            unmet,
            demand,
            status=SolverStatus.FALLBACK,
            objective_value=sum(a.objective_contribution for a in assignments),
            solve_ms=0.0,
            config=self.config,
            rejected=rejected or [],
            priorities={o.habitation_id: o.priority for o in options},
            sites=sites,
        )
        plan.notes.append(
            "Produced by the deterministic greedy fallback, not the constraint "
            "solver. It respects every hard constraint but does not trade "
            "habitations against each other, so it is generally worse than the "
            "solver's plan and never better."
        )
        validate(plan, sites, options, demand, self.config)
        return plan


# ---------------------------------------------------------------------------
# Post-solve validation. A breach here is fatal, never rendered.
# ---------------------------------------------------------------------------


def validate(
    plan: Plan,
    sites: list[SiteState],
    options: list[Option],
    demand: dict[str, int],
    config: AstraModelConfig,
) -> None:
    """Assert the returned plan respects every hard constraint.

    CLAUDE.md section 13 question 10 asks whether the optimiser can output an
    assignment that breaches capacity, uses an unusable route or picks an
    unsuitable site. This function is the answer, and it runs on every solve
    including the fallback.
    """
    allowed = {o.key: o for o in options}
    site_by_id = {site.site_id: site for site in sites}
    floor = int(config.optimiser.min_assignment_block.value)

    per_site: dict[str, int] = {}
    per_site_phase: dict[tuple[str, PhaseTier], int] = {}
    per_habitation: dict[str, int] = {}
    for assignment in plan.assignments:
        key = (assignment.habitation_id, assignment.site_id, assignment.phase)
        option = allowed.get(key)
        if option is None:
            raise PlanValidationError(
                f"{key[0]} was assigned to {key[1]} in phase {key[2].value}, which is "
                "not an allowed option: the site fails a gate, the route is below the "
                "reliability threshold, or the travel time exceeds that phase's ceiling"
            )
        if assignment.people <= 0:
            raise PlanValidationError(f"{key} carries a non-positive assignment")
        if assignment.people < min(floor, demand[assignment.habitation_id]):
            raise PlanValidationError(
                f"{key} moves {assignment.people} people, below the "
                f"household-integrity floor of {floor}"
            )
        per_site[assignment.site_id] = per_site.get(assignment.site_id, 0) + assignment.people
        per_habitation[assignment.habitation_id] = (
            per_habitation.get(assignment.habitation_id, 0) + assignment.people
        )
        index = PHASE_ORDER.index(assignment.phase)
        for phase in PHASE_ORDER[index:]:
            per_site_phase[(assignment.site_id, phase)] = (
                per_site_phase.get((assignment.site_id, phase), 0) + assignment.people
            )

    for site_id, people in per_site.items():
        capacity = site_by_id[site_id].effective_capacity
        if people > capacity:
            raise PlanValidationError(
                f"{site_id} was assigned {people} people against an effective "
                f"capacity of {capacity}"
            )
    for (site_id, phase), people in per_site_phase.items():
        ceiling = site_by_id[site_id].phase_ceiling[phase]
        if people > ceiling:
            raise PlanValidationError(
                f"{site_id} holds {people} people by the end of {phase.value} against "
                f"a phase ceiling of {ceiling}"
            )
    for habitation_id, population in demand.items():
        assigned = per_habitation.get(habitation_id, 0)
        if assigned > population:
            raise PlanValidationError(
                f"{habitation_id} has {assigned} people assigned but only "
                f"{population} residents"
            )
        if assigned + plan.unmet.get(habitation_id, 0) != population:
            raise PlanValidationError(
                f"{habitation_id}: {assigned} assigned plus "
                f"{plan.unmet.get(habitation_id, 0)} unmet does not account for its "
                f"{population} residents"
            )


# ---------------------------------------------------------------------------
# Assembly helpers
# ---------------------------------------------------------------------------


def _assemble(
    assignments: list[Assignment],
    unmet: dict[str, int],
    demand: dict[str, int],
    *,
    status: SolverStatus,
    objective_value: float,
    solve_ms: float,
    config: AstraModelConfig,
    rejected: list[RejectedOption],
    priorities: dict[str, float],
    sites: list[SiteState],
) -> Plan:
    settings = config.optimiser
    assigned = sum(a.people for a in assignments)
    person_minutes = sum(a.people * a.travel_time_min for a in assignments)
    site_usage: dict[str, int] = {}
    per_habitation_sites: dict[str, set[str]] = {}
    for assignment in assignments:
        site_usage[assignment.site_id] = (
            site_usage.get(assignment.site_id, 0) + assignment.people
        )
        per_habitation_sites.setdefault(assignment.habitation_id, set()).add(
            assignment.site_id
        )

    overload_cost = settings.beta_site_overload.value * sum(
        max(site_usage.get(site.site_id, 0) - site.soft_capacity, 0) for site in sites
    )
    delay_cost = settings.beta_phase_delay.value * sum(
        assignment.people * assignment.delay_steps * (priorities.get(
            assignment.habitation_id, 0.0
        ) / 100.0)
        for assignment in assignments
    )

    terms = {
        "unmet_demand": round(
            sum(
                settings.beta_unmet_demand.value
                * (priorities.get(habitation_id, 0.0) / 100.0)
                * people
                for habitation_id, people in unmet.items()
            ),
            2,
        ),
        "travel_time": round(settings.beta_travel_time.value * person_minutes, 2),
        "route_risk": round(
            settings.beta_route_risk.value
            * sum(a.people * a.route_risk for a in assignments),
            2,
        ),
        "livelihood_disruption": round(
            settings.beta_livelihood_disruption.value
            * sum(a.people * a.livelihood_disruption for a in assignments),
            2,
        ),
        "fragmentation": round(
            settings.beta_fragmentation.value
            * sum(max(len(s) - 1, 0) for s in per_habitation_sites.values()),
            2,
        ),
        "phase_delay": round(delay_cost, 2),
        "site_overload": round(overload_cost, 2),
    }

    totals = PlanTotals(
        population_assessed=sum(demand.values()),
        population_assigned=assigned,
        population_unmet=sum(unmet.values()),
        person_minutes=round(person_minutes, 1),
        mean_travel_time_min=round(person_minutes / assigned, 1) if assigned else 0.0,
        mean_route_reliability=round(
            sum(a.people * a.route_reliability for a in assignments) / assigned, 4
        )
        if assigned
        else 0.0,
        mean_livelihood_disruption=round(
            sum(a.people * a.livelihood_disruption for a in assignments) / assigned, 4
        )
        if assigned
        else 0.0,
        sites_used=len(site_usage),
        habitations_split=sum(1 for s in per_habitation_sites.values() if len(s) > 1),
    )
    return Plan(
        assignments=sorted(
            assignments,
            key=lambda a: (PHASE_ORDER.index(a.phase), -a.people, a.habitation_id),
        ),
        unmet=unmet,
        status=status,
        objective_value=round(objective_value, 2),
        objective_terms=terms,
        solve_ms=round(solve_ms, 1),
        totals=totals,
        rejected=rejected,
        site_usage=site_usage,
    )


def _infeasible(
    demand: dict[str, int], rejected: list[RejectedOption], solve_ms: float
) -> Plan:
    return Plan(
        assignments=[],
        unmet=dict(demand),
        status=SolverStatus.INFEASIBLE,
        objective_value=0.0,
        objective_terms={},
        solve_ms=round(solve_ms, 1),
        totals=PlanTotals(
            population_assessed=sum(demand.values()),
            population_assigned=0,
            population_unmet=sum(demand.values()),
            person_minutes=0.0,
            mean_travel_time_min=0.0,
            mean_route_reliability=0.0,
            mean_livelihood_disruption=0.0,
            sites_used=0,
            habitations_split=0,
        ),
        rejected=rejected,
        notes=["No assignment satisfies every hard constraint."],
    )


def _no_such_option(
    habitation_id: str, site_id: str, rejected: list[RejectedOption]
) -> Plan:
    reason = next(
        (
            entry
            for entry in rejected
            if entry.habitation_id == habitation_id and entry.site_id == site_id
        ),
        None,
    )
    detail = (
        reason.detail
        if reason
        else f"{site_id} is not an allowed destination for {habitation_id}."
    )
    return Plan(
        assignments=[],
        unmet={},
        status=SolverStatus.INFEASIBLE,
        objective_value=0.0,
        objective_terms={},
        solve_ms=0.0,
        totals=PlanTotals(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0, 0),
        rejected=rejected,
        notes=[detail],
    )
