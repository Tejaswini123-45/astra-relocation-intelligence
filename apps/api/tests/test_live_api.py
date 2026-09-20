"""Live ingest, the SSE stream and the execution pipeline, at the API surface.

These live in their own module because live state is process-wide by design - it
is the standing picture every screen reads - so each test resets it rather than
inheriting whatever the last one left behind.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from astra.main import app


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client



# ---------------------------------------------------------------------------
# Engine 8 - live ingest, the SSE stream and the execution graph
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_live_state(client: TestClient):
    """Each live test starts from the baseline and leaves it that way.

    Live state is process-wide by design - it is the standing picture every
    screen reads - so a test that left events behind would contaminate the next.
    """
    client.post("/live/reset")
    yield
    client.post("/live/reset")


def _await_run(client: TestClient, run_id: str, timeout_s: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout_s
    payload = client.get(f"/runs/{run_id}").json()
    while payload["status"] not in ("COMPLETED", "FAILED"):
        if time.monotonic() > deadline:
            raise AssertionError(f"run {run_id} did not finish within {timeout_s}s")
        time.sleep(0.1)
        payload = client.get(f"/runs/{run_id}").json()
    return payload


def _rain(lon: float = 79.34, lat: float = 30.41, mm: float = 180.0, radius: float = 6000):
    return {
        "events": [
            {
                "kind": "RAINFALL_OBSERVATION",
                "lon": lon,
                "lat": lat,
                "radius_m": radius,
                "value": mm,
                "source": "test gauge",
            }
        ],
        "trigger": "test",
    }


def test_before_anything_is_ingested_astra_says_it_is_not_live(
    client: TestClient,
) -> None:
    payload = client.get("/live").json()
    assert payload["live"] is False
    assert payload["run_id"] is None
    assert payload["events_ingested"] == 0
    assert payload["cells_rescored"] == 0
    assert payload["zones_now"] == payload["zones_baseline"]
    assert "baseline" in payload["headline"].lower()


def test_ingesting_an_observation_starts_a_real_run(client: TestClient) -> None:
    response = client.post("/events", json=_rain())
    assert response.status_code == 202
    created = response.json()
    assert created["status"] in ("QUEUED", "RUNNING", "COMPLETED")
    assert created["events"][0]["id"].startswith("EV-")
    assert created["events"][0]["description"]

    payload = _await_run(client, created["id"])
    assert payload["status"] == "COMPLETED", payload["error"]
    assert payload["total_ms"] > 0
    assert payload["cells_rescored"] > 0
    assert payload["cells_rescored"] < payload["cells_in_grid"]


def test_a_run_emits_every_stage_with_a_payload_it_computed(
    client: TestClient,
) -> None:
    run_id = client.post("/events", json=_rain()).json()["id"]
    payload = _await_run(client, run_id)

    completed = {
        stage["stage"] for stage in payload["stages"] if stage["status"] == "COMPLETED"
    }
    assert completed == set(payload["stage_order"])
    for stage in payload["stages"]:
        assert stage["message"], "a stage event with no message says nothing"
        assert stage["event"] in (
            "stage_started",
            "stage_progress",
            "stage_completed",
            "warning",
            "stage_failed",
        )
    hazard = next(
        stage
        for stage in payload["stages"]
        if stage["stage"] == "HAZARD" and stage["status"] == "COMPLETED"
    )
    # Every one of these is a measurement, not a label.
    assert hazard["payload"]["cells_rescored"] > 0
    assert hazard["payload"]["cells_changed"] > 0
    assert hazard["payload"]["composite_delta_max"] > 0
    assert hazard["elapsed_ms"] > 0
    optimise = next(
        stage
        for stage in payload["stages"]
        if stage["stage"] == "OPTIMISATION" and stage["status"] == "COMPLETED"
    )
    assert optimise["payload"]["solver_status"] in (
        "OPTIMAL",
        "FEASIBLE",
        "FALLBACK",
    )
    assert optimise["payload"]["assignments"] >= 0


def test_the_stream_replays_the_whole_run_as_server_sent_events(
    client: TestClient,
) -> None:
    """A client always connects after the run started, so it must not miss stages."""
    run_id = client.post("/events", json=_rain()).json()["id"]
    frames: list[tuple[str, dict]] = []
    with client.stream("GET", f"/runs/{run_id}/stream") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        name = None
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: ") and name:
                frames.append((name, json.loads(line[len("data: ") :])))

    names = [name for name, _ in frames]
    assert names[0] == "stage_started"
    assert names[-1] == "run_completed"
    assert frames[0][1]["stage"] == "INGEST"
    # The stream is the run: every stage event the run recorded came out of it.
    recorded = client.get(f"/runs/{run_id}").json()["stages"]
    streamed = [data for name, data in frames if name != "run_completed"]
    assert [entry["sequence"] for entry in streamed] == [
        entry["sequence"] for entry in recorded
    ]
    assert frames[-1][1]["status"] == "COMPLETED"


def test_streaming_a_run_that_does_not_exist_is_a_404(client: TestClient) -> None:
    assert client.get("/runs/run-9999/stream").status_code == 404
    assert client.get("/runs/run-9999").status_code == 404


def test_live_state_reports_the_re_score_against_the_baseline(
    client: TestClient,
) -> None:
    run_id = client.post("/events", json=_rain()).json()["id"]
    _await_run(client, run_id)
    payload = client.get("/live").json()

    assert payload["live"] is True
    assert payload["run_id"] == run_id
    assert payload["events_ingested"] == 1
    assert 0 < payload["share_rescored"] < 1.0
    assert payload["critical_area_km2_now"] > payload["critical_area_km2_baseline"]
    assert payload["cells_rescored"] < payload["cells_in_grid"]
    assert "re-scored" in payload["rescore_note"]
    assert payload["classification_label"]
    assert payload["decision_authority"]


def test_live_zones_and_plan_come_back_in_the_baseline_shapes(
    client: TestClient,
) -> None:
    baseline_zones = client.get("/risk/zones").json()
    baseline_plan = client.get("/plan").json()
    run_id = client.post("/events", json=_rain()).json()["id"]
    _await_run(client, run_id)

    zones = client.get("/live/zones").json()
    plan = client.get("/live/plan").json()
    assert set(zones) == set(baseline_zones)
    assert set(plan) == set(baseline_plan)
    assert zones["features"], "a live run still has to produce zones"
    assert (
        plan["totals"]["population_assigned"] + plan["totals"]["population_unmet"]
        == plan["totals"]["population_assessed"]
    )


def test_a_live_run_does_not_move_the_baseline(client: TestClient) -> None:
    before = client.get("/plan").json()
    before_zones = client.get("/risk/zones").json()
    run_id = client.post("/events", json=_rain()).json()["id"]
    _await_run(client, run_id)
    after = client.get("/plan").json()
    assert after["objective_value"] == before["objective_value"]
    assert after["totals"] == before["totals"]
    assert len(client.get("/risk/zones").json()["features"]) == len(
        before_zones["features"]
    )


def test_closing_a_road_the_plan_leans_on_requires_review(client: TestClient) -> None:
    segment = client.get("/routes/critical-segments").json()["segments"][0]
    run_id = client.post(
        "/events",
        json={
            "events": [
                {
                    "kind": "INFRASTRUCTURE_STATUS",
                    "target": segment["segment_id"],
                    "value": 1,
                    "source": "PWD test",
                }
            ],
            "trigger": "test",
        },
    ).json()["id"]
    payload = _await_run(client, run_id)
    assert payload["status"] == "COMPLETED", payload["error"]

    review = payload["review"]
    assert review["required"] is True
    assert review["invalidated_movements"]
    assert review["people_affected"] > 0
    assert "requires review" in review["headline"].lower()
    assert review["decision_authority"]

    state = client.get("/live").json()
    assert state["placed_now"] < state["placed_baseline"]
    assert segment["segment_id"] in state["closed_segments"]


def test_an_observation_far_from_anything_leaves_the_plan_standing(
    client: TestClient,
) -> None:
    run_id = client.post(
        "/events", json=_rain(lon=79.74, lat=30.31, mm=15.0, radius=800)
    ).json()["id"]
    payload = _await_run(client, run_id)
    assert payload["review"]["required"] is False
    assert "still holds" in payload["review"]["headline"]


def test_resetting_returns_to_the_baseline(client: TestClient) -> None:
    run_id = client.post("/events", json=_rain()).json()["id"]
    _await_run(client, run_id)
    assert client.get("/live").json()["live"] is True

    payload = client.post("/live/reset").json()
    assert payload["live"] is False
    assert payload["events_ingested"] == 0
    assert payload["critical_area_km2_now"] == payload["critical_area_km2_baseline"]
    assert client.get("/runs").json()["runs"] == []


def test_an_observation_astra_cannot_place_is_refused(client: TestClient) -> None:
    for body, fragment in (
        (
            {
                "events": [
                    {"kind": "RAINFALL_OBSERVATION", "value": 90, "source": "t"}
                ]
            },
            "where it was observed",
        ),
        (
            {
                "events": [
                    {"kind": "INFRASTRUCTURE_STATUS", "value": 1, "source": "t"}
                ]
            },
            "name the road segment",
        ),
        (
            {
                "events": [
                    {
                        "kind": "INFRASTRUCTURE_STATUS",
                        "target": "not-a-segment",
                        "value": 1,
                        "source": "t",
                    }
                ]
            },
            "unknown road segment",
        ),
        (
            {
                "events": [
                    {
                        "kind": "FIELD_EVIDENCE",
                        "lon": 79.34,
                        "lat": 30.41,
                        "value": 8,
                        "source": "t",
                    }
                ]
            },
            "between 0 and 1",
        ),
    ):
        response = client.post("/events", json=body)
        assert response.status_code == 422, body
        assert fragment in response.json()["detail"]


def test_an_empty_batch_is_refused(client: TestClient) -> None:
    assert client.post("/events", json={"events": []}).status_code == 422


def test_the_demonstration_feed_is_data_to_be_posted_not_results(
    client: TestClient,
) -> None:
    feed = client.get("/events/feed").json()
    assert feed["provenance"] == "DEMO_CONFIG"
    assert feed["steps"]
    assert "demonstration" in feed["description"].lower()
    for step in feed["steps"]:
        assert step["kind"] in (
            "RAINFALL_OBSERVATION",
            "INCIDENT_REPORT",
            "FIELD_EVIDENCE",
            "INFRASTRUCTURE_STATUS",
        )
        assert step["note"], "every step has to say what it is"
        assert step["delay_ms"] >= 0
    # The road step resolves to a segment the plan actually depends on, not a
    # hardcoded id that may no longer matter.
    road = [step for step in feed["steps"] if step["kind"] == "INFRASTRUCTURE_STATUS"]
    if road:
        known = {
            row["segment_id"]
            for row in client.get("/routes/critical-segments").json()["segments"]
        }
        assert road[0]["target"] in known


def test_replaying_the_feed_escalates_the_corridor_and_ends_in_review(
    client: TestClient,
) -> None:
    """The judge journey, driven through the same endpoint an external feed uses."""
    feed = client.get("/events/feed").json()
    seen_hazard_growth = False
    for step in feed["steps"]:
        event = {
            key: step[key]
            for key in ("kind", "lon", "lat", "radius_m", "value", "target", "source")
            if step.get(key) is not None
        }
        run_id = client.post(
            "/events", json={"events": [event], "trigger": "feed"}
        ).json()["id"]
        payload = _await_run(client, run_id)
        assert payload["status"] == "COMPLETED", payload["error"]
        state = client.get("/live").json()
        if state["critical_area_km2_now"] > state["critical_area_km2_baseline"]:
            seen_hazard_growth = True

    final = client.get("/live").json()
    assert seen_hazard_growth, "a monsoon escalation has to grow the critical zone"
    assert final["events_ingested"] == len(feed["steps"])
    assert final["closed_segments"], "the feed closes a road"
    assert final["placed_now"] < final["placed_baseline"]
    assert final["review"]["required"] is True
    assert final["review"]["invalidated_movements"]
    # And the run history records every step of it.
    runs = client.get("/runs", params={"limit": 25}).json()
    assert len(runs["runs"]) == len(feed["steps"])
    assert all(run["status"] == "COMPLETED" for run in runs["runs"])


def test_the_pipeline_only_re_scores_ground_the_evidence_touches(
    client: TestClient,
) -> None:
    """The claim the execution graph makes about itself, checked."""
    small = client.post("/events", json=_rain(radius=2000)).json()["id"]
    _await_run(client, small)
    narrow = client.get("/live").json()["cells_rescored"]
    client.post("/live/reset")

    wide = client.post("/events", json=_rain(radius=9000)).json()["id"]
    _await_run(client, wide)
    broad = client.get("/live").json()["cells_rescored"]

    assert 0 < narrow < broad, (
        "a bigger footprint has to cost more cells; if it does not, the window is "
        "not doing anything and 'incremental' is decoration"
    )
    assert broad < client.get("/live").json()["cells_in_grid"]
