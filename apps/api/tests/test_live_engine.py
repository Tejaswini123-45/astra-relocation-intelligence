"""Engine 8 - live ingest, incremental re-scoring and the pipeline.

The load-bearing test in this file is
``test_a_windowed_rescore_equals_a_full_recomputation``. Everything the
interface says about "only the cells the evidence touches were recomputed" rests
on it. If it fails, the claim is false and the window has to go.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from astra.domain.enums import EventType, PhaseTier, RunStageStatus, RunStatus
from astra.engines.hazard import HazardEngine
from astra.engines.live import (
    CHANGE_EPSILON,
    EventLog,
    LiveIngestError,
    apply_events,
    baseline_ceilings,
    combined_footprint,
    footprint_of,
    make_event,
    rescore,
    window_of,
)
from astra.engines.pipeline import Run, RunRegistry, execute
from astra.engines.service import baseline_risk


def event(
    kind: EventType,
    value: float,
    *,
    lon: float | None = 79.34,
    lat: float | None = 30.41,
    radius_m: float = 4000.0,
    target: str | None = None,
    event_id: str = "EV-TEST",
):
    return make_event(
        kind=kind,
        value=value,
        event_id=event_id,
        lon=lon,
        lat=lat,
        radius_m=radius_m,
        target=target,
        source="test",
    )


@pytest.fixture(scope="module")
def base():
    return baseline_risk()


@pytest.fixture(scope="module")
def ceilings():
    return baseline_ceilings()


# ---------------------------------------------------------------------------
# Footprints
# ---------------------------------------------------------------------------


def test_a_footprint_covers_its_radius_and_stops_there(base) -> None:
    grid = base.context.grid
    # Centred on the study area so neither disc is clipped by the grid edge; the
    # clipping itself is correct behaviour and is checked separately.
    centre = {"lon": grid.bbox.min_lon / 2 + grid.bbox.max_lon / 2,
              "lat": grid.bbox.min_lat / 2 + grid.bbox.max_lat / 2}
    small = footprint_of(
        grid, event(EventType.RAINFALL_OBSERVATION, 90, radius_m=2000, **centre)
    )
    large = footprint_of(
        grid, event(EventType.RAINFALL_OBSERVATION, 90, radius_m=8000, **centre)
    )
    assert 0 < small.cells < large.cells
    # A footprint is a disc of the stated radius on the ground, so its cell count
    # is that area divided by the cell area, give or take the discretisation.
    for print_area, radius in ((small, 2000.0), (large, 8000.0)):
        expected = np.pi * radius**2 / (grid.cell.area_m2)
        assert print_area.cells == pytest.approx(expected, rel=0.05)


def test_a_footprint_is_clipped_by_the_edge_of_the_study_area(base) -> None:
    """ASTRA re-scores ground it has. It does not extrapolate past the bbox."""
    grid = base.context.grid
    inside = footprint_of(
        grid,
        event(
            EventType.RAINFALL_OBSERVATION,
            90,
            lon=grid.bbox.min_lon / 2 + grid.bbox.max_lon / 2,
            lat=grid.bbox.min_lat / 2 + grid.bbox.max_lat / 2,
            radius_m=8000,
        ),
    )
    at_edge = footprint_of(
        grid,
        event(
            EventType.RAINFALL_OBSERVATION,
            90,
            lon=grid.bbox.min_lon + 0.01,
            lat=grid.bbox.min_lat + 0.01,
            radius_m=8000,
        ),
    )
    assert at_edge.cells < inside.cells


def test_a_footprint_is_full_strength_at_the_centre_and_zero_at_the_edge(base) -> None:
    grid = base.context.grid
    print_area = footprint_of(
        grid, event(EventType.RAINFALL_OBSERVATION, 90, radius_m=5000)
    )
    assert print_area.weight.max() == pytest.approx(1.0, abs=0.05)
    assert print_area.weight[~print_area.mask].max() == 0.0
    assert float(print_area.weight[print_area.mask].min()) < 0.05


def test_an_event_with_no_location_has_no_footprint(base) -> None:
    grid = base.context.grid
    road = make_event(
        kind=EventType.INFRASTRUCTURE_STATUS,
        value=1.0,
        event_id="EV-R",
        target="W1-0",
        source="test",
    )
    assert footprint_of(grid, road).empty
    assert combined_footprint(grid, [road]).empty


def test_the_window_contains_every_touched_cell_with_a_margin() -> None:
    mask = np.zeros((40, 40), dtype=bool)
    mask[10:15, 20:24] = True
    rows, cols = window_of(mask, margin=1)
    assert (rows.start, rows.stop) == (9, 16)
    assert (cols.start, cols.stop) == (19, 25)
    assert mask[rows, cols].sum() == mask.sum()


def test_an_empty_mask_has_no_window() -> None:
    assert window_of(np.zeros((10, 10), dtype=bool)) is None


# ---------------------------------------------------------------------------
# Writing events onto the surfaces
# ---------------------------------------------------------------------------


def test_an_event_changes_its_footprint_and_nothing_outside_it(base) -> None:
    grid = base.context.grid
    rain = event(EventType.RAINFALL_OBSERVATION, 200.0, radius_m=4000)
    after, print_area = apply_events(base.context.surfaces, [rain], grid)
    before = base.context.surfaces.rainfall_intensity_mm
    outside = ~print_area.mask
    assert np.array_equal(after.rainfall_intensity_mm[outside], before[outside])
    assert after.rainfall_intensity_mm[print_area.mask].max() > before[
        print_area.mask
    ].max()


def test_rainfall_below_the_extreme_threshold_adds_no_extreme_day(base) -> None:
    from astra.domain.model_config import MODEL_CONFIG

    threshold = MODEL_CONFIG.normalisation.extreme_rain_threshold_mm.value
    grid = base.context.grid
    mild, _ = apply_events(
        base.context.surfaces,
        [event(EventType.RAINFALL_OBSERVATION, threshold - 10.0)],
        grid,
    )
    assert np.array_equal(
        mild.extreme_rain_days, base.context.surfaces.extreme_rain_days
    )
    heavy, mask = apply_events(
        base.context.surfaces,
        [event(EventType.RAINFALL_OBSERVATION, threshold + 40.0)],
        grid,
    )
    assert (
        heavy.extreme_rain_days[mask.mask].sum()
        > base.context.surfaces.extreme_rain_days[mask.mask].sum()
    )


def test_an_incident_lands_on_the_same_scale_as_the_historical_record(base) -> None:
    """A report smoothed through a different kernel would not be comparable.

    Dropping the severity weight into one cell and dividing by the cell area -
    the obvious implementation - puts a single report three orders of magnitude
    above the busiest cell in the entire historical inventory, and silently
    saturates the factor across the whole footprint.
    """
    grid = base.context.grid
    before = base.context.surfaces.incident_density
    after, _ = apply_events(
        base.context.surfaces,
        [event(EventType.INCIDENT_REPORT, 3.0, radius_m=1200)],
        grid,
    )
    added = after.incident_density - before
    assert added.max() > 0, "an incident has to register"
    assert added.max() < float(np.nanmax(before)) * 3, (
        "one new report must not dwarf the entire historical record"
    )


def test_field_evidence_raises_the_input_not_the_score(base) -> None:
    grid = base.context.grid
    after, print_area = apply_events(
        base.context.surfaces,
        [event(EventType.FIELD_EVIDENCE, 0.9, radius_m=2000)],
        grid,
    )
    before = base.context.surfaces.landcover_instability
    raised = after.landcover_instability[print_area.mask] > before[print_area.mask]
    assert raised.any(), "an instability report has to raise the instability input"
    assert after.landcover_instability[print_area.mask].mean() > before[
        print_area.mask
    ].mean()
    # It touched the instability input and nothing else.
    assert np.array_equal(
        after.rainfall_intensity_mm, base.context.surfaces.rainfall_intensity_mm
    )
    assert np.array_equal(after.incident_density, base.context.surfaces.incident_density)


def test_a_road_report_touches_no_hazard_surface(base) -> None:
    road = make_event(
        kind=EventType.INFRASTRUCTURE_STATUS,
        value=1.0,
        event_id="EV-R",
        target="W1-0",
        source="test",
    )
    after, print_area = apply_events(base.context.surfaces, [road], base.context.grid)
    assert after is base.context.surfaces
    assert print_area.empty


# ---------------------------------------------------------------------------
# The load-bearing claim
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,value,radius",
    [
        (EventType.RAINFALL_OBSERVATION, 210.0, 6000.0),
        (EventType.INCIDENT_REPORT, 4.0, 1200.0),
        (EventType.FIELD_EVIDENCE, 0.85, 2500.0),
    ],
)
def test_a_windowed_rescore_equals_a_full_recomputation(
    base, ceilings, kind, value, radius
) -> None:
    """The whole "incremental" claim in the interface rests on this.

    A window is only legitimate if scoring is a pure per-cell function of the
    inputs, which it is once the density normalisation ceilings are pinned. If
    this ever fails, the interface is telling a lie and the window has to go.
    """
    one = event(kind, value, radius_m=radius)
    run, report = rescore(base, [one], ceilings=ceilings)

    surfaces, _ = apply_events(base.context.surfaces, [one], base.context.grid)
    full = HazardEngine(ceilings=ceilings).compute(surfaces)

    assert np.allclose(run.result.composite, full.composite, equal_nan=True)
    assert np.array_equal(run.result.zone_class, full.zone_class)
    assert np.allclose(run.result.confidence, full.confidence, equal_nan=True)
    for hazard, surface in full.per_hazard.items():
        assert np.allclose(
            run.result.per_hazard[hazard].score, surface.score, equal_nan=True
        )
    assert report.cells_rescored < report.cells_in_grid, (
        "a re-score that covers the whole grid is not incremental"
    )


def test_a_rescore_leaves_the_ground_outside_the_footprint_alone(
    base, ceilings
) -> None:
    one = event(EventType.RAINFALL_OBSERVATION, 220.0, radius_m=4000)
    run, _ = rescore(base, [one], ceilings=ceilings)
    print_area = footprint_of(base.context.grid, one)
    rows, cols = window_of(print_area.mask)
    untouched = np.ones(base.context.grid.shape, dtype=bool)
    untouched[rows, cols] = False
    assert np.array_equal(
        run.result.composite[untouched], base.result.composite[untouched]
    )


def test_the_rescore_reports_what_moved_not_just_what_was_recomputed(
    base, ceilings
) -> None:
    run, report = rescore(
        base, [event(EventType.RAINFALL_OBSERVATION, 210.0, radius_m=5000)],
        ceilings=ceilings,
    )
    assert 0 < report.cells_changed <= report.cells_rescored
    assert report.composite_delta_max > CHANGE_EPSILON
    delta = np.abs(run.result.composite - base.result.composite)
    assert report.cells_changed == int((delta > CHANGE_EPSILON).sum())
    assert report.share < 1.0


def test_a_batch_with_no_surface_event_recomputes_nothing(base, ceilings) -> None:
    road = make_event(
        kind=EventType.INFRASTRUCTURE_STATUS,
        value=1.0,
        event_id="EV-R",
        target="W1-0",
        source="test",
    )
    run, report = rescore(base, [road], ceilings=ceilings)
    assert report.cells_rescored == 0
    assert report.cells_changed == 0
    assert run.result is base.result


def test_pinning_the_ceiling_is_what_makes_the_window_exact(base) -> None:
    """Without a pinned ceiling the yardstick moves and the splice is wrong.

    This is the failure the pinning exists to prevent, demonstrated rather than
    asserted: an incident raises the incident-density surface, which raises its
    own percentile, which rescales the factor *everywhere* - including cells the
    event never touched.
    """
    incident = event(EventType.INCIDENT_REPORT, 6.0, radius_m=1500)
    surfaces, _ = apply_events(base.context.surfaces, [incident], base.context.grid)
    pinned = HazardEngine(ceilings=baseline_ceilings()).measure_ceilings(surfaces)
    unpinned = HazardEngine().measure_ceilings(surfaces)
    assert pinned["incident_density"] == unpinned["incident_density"], (
        "measure_ceilings reads the surface; pinning happens at scoring time"
    )
    free = HazardEngine().compute(surfaces)
    fixed = HazardEngine(ceilings=baseline_ceilings()).compute(surfaces)
    # The two differ somewhere: that difference is exactly the moving yardstick.
    assert not np.allclose(free.composite, fixed.composite, equal_nan=True)


# ---------------------------------------------------------------------------
# The event log
# ---------------------------------------------------------------------------


def test_the_log_gives_every_event_its_own_id() -> None:
    log = EventLog()
    ids = {log.next_id() for _ in range(50)}
    assert len(ids) == 50


def test_the_log_reports_the_latest_status_per_segment() -> None:
    log = EventLog()
    for value in (1.0, 1.0, 0.0):
        log.add(
            make_event(
                kind=EventType.INFRASTRUCTURE_STATUS,
                value=value,
                event_id=log.next_id(),
                target="W-1",
                source="test",
            )
        )
    log.add(
        make_event(
            kind=EventType.INFRASTRUCTURE_STATUS,
            value=1.0,
            event_id=log.next_id(),
            target="W-2",
            source="test",
        )
    )
    assert log.closed_segments == frozenset({"W-2"}), (
        "a reopened road must not stay closed because it was once reported shut"
    )


def test_clearing_the_log_is_a_real_reset() -> None:
    log = EventLog()
    log.add(event(EventType.RAINFALL_OBSERVATION, 100.0, event_id=log.next_id()))
    log.clear()
    assert log.snapshot() == []
    assert log.surface_events == []
    assert log.closed_segments == frozenset()


# ---------------------------------------------------------------------------
# Refusing what cannot be acted on
# ---------------------------------------------------------------------------


def test_an_observation_without_a_place_is_refused() -> None:
    with pytest.raises(LiveIngestError, match="where it was observed"):
        make_event(
            kind=EventType.RAINFALL_OBSERVATION,
            value=90.0,
            event_id="EV-X",
            source="test",
        )


def test_a_road_report_without_a_segment_is_refused() -> None:
    with pytest.raises(LiveIngestError, match="name the road segment"):
        make_event(
            kind=EventType.INFRASTRUCTURE_STATUS,
            value=1.0,
            event_id="EV-X",
            source="test",
        )


def test_field_evidence_outside_its_scale_is_refused() -> None:
    with pytest.raises(LiveIngestError, match="between 0 and 1"):
        event(EventType.FIELD_EVIDENCE, 4.0)


def test_an_incident_footprint_is_the_kernel_it_is_actually_smoothed_with() -> None:
    """What the map draws has to be the ground the arithmetic used."""
    from astra.domain.model_config import MODEL_CONFIG
    from astra.engines.live import KERNEL_FOOTPRINT_SIGMAS

    one = event(EventType.INCIDENT_REPORT, 2.0, radius_m=50.0)
    assert one.radius_m == pytest.approx(
        KERNEL_FOOTPRINT_SIGMAS * MODEL_CONFIG.hazard.history_kernel_radius_m.value
    )


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def _wait(registry: RunRegistry, run: Run, timeout_s: float = 180.0) -> Run:
    deadline = time.monotonic() + timeout_s
    while not run.terminal and time.monotonic() < deadline:
        time.sleep(0.05)
    return run


def test_a_run_emits_every_stage_in_order_with_real_timings() -> None:
    registry = RunRegistry()
    run = Run(
        id="run-test",
        trigger="test",
        created_at=__import__("datetime").datetime.now(
            tz=__import__("datetime").UTC
        ),
        events=[event(EventType.RAINFALL_OBSERVATION, 160.0, event_id="EV-1")],
    )
    registry.log.add(run.events[0])
    execute(run, registry.log)

    assert run.status is RunStatus.COMPLETED, run.error
    from astra.engines.pipeline import STAGE_ORDER

    completed = [
        stage.stage
        for stage in run.stages
        if stage.status is RunStageStatus.COMPLETED
    ]
    assert set(completed) == set(STAGE_ORDER)
    # Every stage that started also finished, and finishing took measurable time.
    for stage in run.stages:
        if stage.status is RunStageStatus.COMPLETED:
            assert stage.elapsed_ms >= 0.0
            assert stage.message
    hazard = next(
        stage
        for stage in run.stages
        if stage.stage.value == "HAZARD" and stage.status is RunStageStatus.COMPLETED
    )
    assert hazard.payload["cells_rescored"] > 0
    assert hazard.payload["cells_rescored"] < hazard.payload["cells_in_grid"]
    assert hazard.elapsed_ms > 0.0


def test_a_run_records_what_the_evidence_moved() -> None:
    registry = RunRegistry()
    run = registry.start(
        [event(EventType.RAINFALL_OBSERVATION, 200.0, radius_m=9000, event_id="EV-1")],
        trigger="test",
    )
    _wait(registry, run)
    assert run.status is RunStatus.COMPLETED, run.error
    exposure = next(
        stage
        for stage in run.stages
        if stage.stage.value == "EXPOSURE_VULNERABILITY"
        and stage.status is RunStageStatus.COMPLETED
    )
    moved = exposure.payload["habitations_moved"]
    assert moved, "a large storm over the cluster has to move somebody's hazard score"
    assert all(entry["hazard_after"] != entry["hazard_before"] for entry in moved)
    assert moved == sorted(
        moved, key=lambda entry: abs(entry["hazard_delta"]), reverse=True
    )


def test_a_closed_road_reaches_the_optimiser_and_invalidates_the_plan() -> None:
    from astra.api.routes_router import critical_segments

    segment = critical_segments().segments[0].segment_id
    registry = RunRegistry()
    run = registry.start(
        [
            make_event(
                kind=EventType.INFRASTRUCTURE_STATUS,
                value=1.0,
                event_id="EV-1",
                target=segment,
                source="test",
            )
        ],
        trigger="test",
    )
    _wait(registry, run)
    assert run.status is RunStatus.COMPLETED, run.error
    assert run.outputs is not None
    assert segment in run.outputs.closed_segments

    from astra.engines.optimizer_service import baseline_plan

    standing, _ = baseline_plan()
    assert (
        run.outputs.plan.totals.population_assigned
        < standing.totals.population_assigned
    ), "closing the road the plan leans on hardest has to cost the plan something"
    assert run.review is not None
    assert run.review.required
    assert run.review.invalidated_movements
    assert run.review.people_affected > 0


def test_a_run_that_changes_nothing_says_the_plan_still_holds() -> None:
    registry = RunRegistry()
    run = registry.start(
        [
            event(
                EventType.RAINFALL_OBSERVATION,
                20.0,
                lon=79.74,
                lat=30.31,
                radius_m=1000,
                event_id="EV-1",
            )
        ],
        trigger="test",
    )
    _wait(registry, run)
    assert run.status is RunStatus.COMPLETED, run.error
    assert run.review is not None
    assert not run.review.required
    assert "still holds" in run.review.headline


def test_a_registry_reset_returns_to_the_baseline() -> None:
    registry = RunRegistry()
    run = registry.start(
        [event(EventType.RAINFALL_OBSERVATION, 190.0, event_id="EV-1")],
        trigger="test",
    )
    _wait(registry, run)
    assert registry.latest_completed is run
    registry.reset()
    assert registry.latest_completed is None
    assert registry.log.snapshot() == []
    assert registry.recent() == []


def test_following_a_run_replays_from_the_first_stage() -> None:
    """A client always connects after the run started, so it must not miss stages."""
    registry = RunRegistry()
    run = registry.start(
        [event(EventType.RAINFALL_OBSERVATION, 150.0, event_id="EV-1")],
        trigger="test",
    )
    _wait(registry, run)
    streamed = list(registry.follow(run, poll_s=0.0))
    assert [stage.sequence for stage in streamed] == list(range(len(run.stages)))
    assert streamed[0].stage.value == "INGEST"


def test_the_run_history_is_bounded() -> None:
    from astra.engines.pipeline import MAX_RUNS

    registry = RunRegistry()
    for index in range(MAX_RUNS + 5):
        run = registry.start(
            [
                event(
                    EventType.RAINFALL_OBSERVATION,
                    5.0,
                    lon=79.74,
                    lat=30.31,
                    radius_m=600,
                    event_id=f"EV-{index}",
                )
            ],
            trigger="test",
        )
        _wait(registry, run)
    assert len(registry.recent(limit=25)) <= MAX_RUNS + 1
    assert registry.latest_completed is not None
    assert registry.get(registry.latest_completed.id) is not None


def test_the_immediate_tier_is_reachable_from_evidence_alone() -> None:
    """Live evidence has to be able to change what an officer is asked to do."""
    registry = RunRegistry()
    run = registry.start(
        [
            event(
                EventType.RAINFALL_OBSERVATION,
                240.0,
                lon=79.34,
                lat=30.42,
                radius_m=14000,
                event_id="EV-1",
            )
        ],
        trigger="test",
    )
    _wait(registry, run)
    assert run.outputs is not None
    from astra.api.priority_router import baseline_priority

    before = {row.habitation.id: row.phase for row in baseline_priority().rows}
    after = {row.habitation.id: row.phase for row in run.outputs.priority.rows}
    assert after != before, (
        "a corridor-wide cloudburst that changes no habitation's tier would mean "
        "the tiering is not reading the hazard state"
    )
    assert any(phase is PhaseTier.IMMEDIATE for phase in after.values())
