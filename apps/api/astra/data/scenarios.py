"""Scenario definitions.

Scenarios are first-class, versioned objects rather than UI toggles (CLAUDE.md
section 5.7). The baseline is defined here; perturbed scenarios created through
``POST /simulate`` are persisted alongside it from the scenario slice onwards.
"""

from __future__ import annotations

from datetime import UTC, datetime

from astra.data.study_area import DEFAULT_STUDY_AREA_ID
from astra.domain.models import Scenario
from astra.domain.notices import SCENARIO_DISCLAIMER

BASELINE_CREATED_AT = datetime(2026, 9, 9, 0, 0, 0, tzinfo=UTC)
"""Fixed so the baseline scenario is byte-identical across runs and machines."""

BASELINE = Scenario(
    id="baseline",
    name="Baseline - current conditions",
    description=(
        "Current-conditions assessment of the Alaknanda valley corridor: observed "
        "terrain and hydrology, recorded incident history and normal-season rainfall, "
        "with no perturbation applied."
    ),
    study_area_id=DEFAULT_STUDY_AREA_ID,
    is_baseline=True,
    created_at=BASELINE_CREATED_AT,
    disclaimer=SCENARIO_DISCLAIMER,
    changes=[],
)

SCENARIOS: dict[str, Scenario] = {BASELINE.id: BASELINE}

DEFAULT_SCENARIO_ID = BASELINE.id

SIMULATED_HISTORY = 50
"""How many simulated scenarios are kept.

A what-if is stored so a result can be traced back to the exact set of changes
that produced it, which is what lets an audit record point at something. But a
user dragging a slider produces a scenario per run, and an unbounded store would
turn a demo into a slow memory leak and the scenario list into a junk drawer.
The oldest simulated scenario is evicted; the baseline never is.
"""


def get_scenario(scenario_id: str) -> Scenario | None:
    return SCENARIOS.get(scenario_id)


def list_scenarios() -> list[Scenario]:
    """The baseline first, then simulated scenarios newest first."""
    simulated = [s for s in SCENARIOS.values() if not s.is_baseline]
    simulated.sort(key=lambda scenario: scenario.created_at, reverse=True)
    return [s for s in SCENARIOS.values() if s.is_baseline] + simulated


def remember_scenario(scenario: Scenario) -> Scenario:
    """Store a simulated scenario, evicting the oldest beyond the history bound."""
    SCENARIOS[scenario.id] = scenario
    simulated = [s for s in SCENARIOS.values() if not s.is_baseline]
    if len(simulated) > SIMULATED_HISTORY:
        simulated.sort(key=lambda entry: entry.created_at)
        for stale in simulated[: len(simulated) - SIMULATED_HISTORY]:
            SCENARIOS.pop(stale.id, None)
    return scenario
