"""The Decision Brief: read off the engines, consistent with the screens, frozen on generation."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from astra.main import app


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        # Other modules ingest live events into the same process-wide registry.
        # The consistency checks below compare against baseline endpoints, so the
        # brief has to be built on the baseline.
        test_client.post("/live/reset")
        yield test_client


def _latest_decision(client: TestClient) -> str | None:
    rows = client.get("/decisions", params={"limit": 1}).json()["decisions"]
    return rows[0]["id"] if rows else None


def test_preview_writes_nothing_and_is_built_on_the_baseline(client: TestClient) -> None:
    before = _latest_decision(client)
    res = client.get("/brief/preview")
    assert res.status_code == 200
    brief = res.json()
    assert brief["id"] is None
    assert brief["audit"] is None
    assert brief["basis"] == "BASELINE"
    assert _latest_decision(client) == before


def test_brief_figures_match_the_screens_that_show_them(client: TestClient) -> None:
    brief = client.get("/brief/preview").json()
    plan = client.get("/plan").json()
    priority = client.get("/priority/habitations").json()
    capacity = client.get("/capacity/sites").json()
    zones = client.get("/risk/zones").json()

    assert brief["plan"]["totals"] == plan["totals"]
    assert brief["plan"]["status"] == plan["status"]
    assert brief["plan"]["capacity_blocked"] == plan["capacity_blocked"]

    top = priority["habitations"][0]
    assert brief["priorities"][0]["habitation_id"] == top["habitation_id"]
    assert brief["priorities"][0]["priority_score"] == round(top["priority_score"], 1)
    assert brief["situation"]["by_phase"] == priority["totals_by_phase"]

    assert brief["capacity"]["total_effective_capacity"] == capacity["total_effective_capacity"]
    assert brief["capacity"]["suitable_sites"] == capacity["suitable_sites"]
    by_site = {site["site_id"]: site for site in capacity["sites"]}
    for site in brief["capacity"]["sites"]:
        assert site["marginal_headline"] == by_site[site["site_id"]]["marginal_headline"]

    assert brief["situation"]["zones"] == zones["summary"]

    dependencies = client.get("/routes/critical-segments").json()["segments"]
    assert brief["routes"]["dependencies"] == dependencies[:5]


def test_phase_actions_account_for_every_planned_movement(client: TestClient) -> None:
    brief = client.get("/brief/preview").json()
    phases = [action["phase"] for action in brief["actions"]]
    assert phases == ["IMMEDIATE", "SHORT_TERM", "MEDIUM_TERM"]

    moved = 0
    for action in brief["actions"]:
        in_phase = sum(move["people"] for move in action["movements"])
        assert in_phase == action["people_moved"]
        assert all(move["phase"] == action["phase"] for move in action["movements"])
        moved += in_phase
    assert moved == brief["plan"]["totals"]["population_assigned"]


def test_no_recommended_movement_uses_an_unusable_site_or_route(client: TestClient) -> None:
    brief = client.get("/brief/preview").json()
    suitable = {site["site_id"] for site in brief["capacity"]["sites"] if site["suitable"]}
    threshold = brief["routes"]["reliability_threshold"]
    for action in brief["actions"]:
        for move in action["movements"]:
            assert move["site_id"] in suitable
            assert move["route_reliability"] >= threshold - 1e-3


def test_generate_writes_a_ledger_row_and_freezes_the_payload(client: TestClient) -> None:
    res = client.post("/brief", json={"notes": "district review"})
    assert res.status_code == 201
    brief = res.json()
    assert brief["id"].startswith("BRF-")
    audit = brief["audit"]
    assert audit is not None

    decision = client.get(f"/decisions/{audit['decision_id']}").json()
    assert decision["trigger"] == "decision brief"
    assert decision["input_summary_hash"] == audit["input_summary_hash"]
    assert decision["confidence"] == brief["confidence"]["modal_band"]

    frozen = client.get(f"/brief/{brief['id']}").json()
    assert frozen == brief

    listing = client.get("/briefs").json()
    assert listing["briefs"][0]["id"] == brief["id"]
    assert listing["briefs"][0]["decision_id"] == audit["decision_id"]


def test_brief_carries_its_authority_and_limitations(client: TestClient) -> None:
    brief = client.get("/brief/preview").json()
    assert "SDMA" in brief["decision_authority"]
    assert brief["classification_label"] == "ASTRA analytical classification"
    assert brief["scenario_disclaimer"] in brief["limitations"]
    assert brief["narration"]["mode"] in ("template", "model")
    assert len(brief["comparison"]) >= 5
    assert all(row["astra"] and row["static_map"] for row in brief["comparison"])
    assert all(constant["provenance"] for constant in brief["assumptions"])


def test_unknown_brief_is_a_404(client: TestClient) -> None:
    assert client.get("/brief/BRF-nope").status_code == 404


def test_zone_wire_geometry_is_rounded_without_moving_the_totals(client: TestClient) -> None:
    zones = client.get("/risk/zones").json()
    summary = client.get("/risk/summary").json()
    assert zones["summary"] == summary["zone_summary"]
    ring = zones["features"][0]["geometry"]["coordinates"][0]
    for lon, lat in ring[:20]:
        assert round(lon, 5) == lon
        assert round(lat, 5) == lat
