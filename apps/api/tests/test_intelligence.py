"""Tests for intelligence router: evidence, decision ledger, narration, and ask layer."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from astra.main import app


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_evidence_empty_list(client: TestClient) -> None:
    res = client.get("/evidence")
    assert res.status_code == 200
    data = res.json()
    assert "evidence" in data
    assert "kinds" in data
    assert data["decision_authority"] is not None


def test_file_and_get_evidence(client: TestClient) -> None:
    res = client.post(
        "/evidence",
        data={
            "text": "Landslide observed near Joshimath road slope",
            "reporter": "Inspector Sharma",
            "role": "Field Observer",
            "habitation_id": "H-01",
        },
    )
    assert res.status_code == 201
    item = res.json()
    assert item["id"].startswith("EV-")
    assert item["reporter"] == "Inspector Sharma"
    assert item["kind"] == "LANDSLIDE"

    # Detail check
    detail = client.get(f"/evidence/{item['id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == item["id"]


def test_decisions_list_and_record(client: TestClient) -> None:
    res = client.get("/decisions?limit=1")
    assert res.status_code == 200
    previous = res.json()["decisions"]

    # Record a decision
    post_res = client.post(
        "/decisions",
        json={"trigger": "manual_review", "notes": "Baseline review for Phase 1"},
    )
    assert post_res.status_code == 201
    rec = post_res.json()
    assert rec["id"].startswith("DEC-")
    assert rec["state"] in ("COMPUTED", "UNDER_REVIEW")

    # List again
    # The ledger lists newest first. Comparing the head rather than a count keeps
    # this true however many rows the store already holds.
    after_res = client.get("/decisions?limit=1")
    assert after_res.status_code == 200
    assert after_res.json()["decisions"][0]["id"] == rec["id"]
    assert not previous or previous[0]["id"] != rec["id"]


def test_ask_intents_and_query(client: TestClient) -> None:
    intents_res = client.get("/ask/intents")
    assert intents_res.status_code == 200
    intents = intents_res.json()
    assert isinstance(intents, list)
    assert len(intents) > 0

    # Ask an allowlisted question
    ask_res = client.post("/ask", json={"question": "Why is H-01 prioritized?"})
    assert ask_res.status_code == 200
    ans = ask_res.json()
    assert "text" in ans
    assert ans["intent"] is not None


def test_narrate_plan(client: TestClient) -> None:
    res = client.get("/narrate/plan")
    assert res.status_code == 200
    data = res.json()
    assert "text" in data
    assert data["mode"] in ("template", "model")
    assert data["decision_authority"] is not None
