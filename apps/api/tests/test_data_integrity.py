"""The fixture integrity gate must actually block bad data, not merely report it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra.data.fixtures import FixtureError, load_fixtures
from astra.data.provenance import ProvenanceError, ProvenanceRegistry, load_registry
from astra.data.study_area import get_study_area
from astra.data.validate import FixtureValidationError, enforce, validate_all
from astra.domain.enums import ProvenanceClass, StructureType
from astra.domain.models import DatasetRecord, DemographicProfile, GeoPoint, Habitation


def _habitation(hid: str, lon: float, lat: float, population: int = 400) -> Habitation:
    return Habitation(
        id=hid,
        name=f"Test Tok {hid}",
        centroid=GeoPoint(lon=lon, lat=lat),
        population=population,
        households=population // 5,
        demographics=DemographicProfile(
            elderly_60_plus=int(population * 0.12),
            children_under_5=int(population * 0.09),
            persons_with_disability=int(population * 0.03),
            medically_dependent=int(population * 0.02),
            low_income_households=int(population / 5 * 0.4),
        ),
        structure_mix={
            StructureType.KUTCHA: 0.3,
            StructureType.SEMI_PUCCA: 0.45,
            StructureType.PUCCA: 0.25,
        },
        district="Chamoli",
        state="Uttarakhand",
    )


def _write(directory: Path, filename: str, records: list[dict]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(json.dumps(records), encoding="utf-8")


def test_repository_state_passes_the_gate() -> None:
    report = validate_all()
    assert report.ok, report.errors
    assert report.dataset_count >= 1
    assert "provenance registry loads and validates" in report.checked


def test_structure_mix_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match="structure_mix"):
        Habitation(
            id="H-99",
            name="Broken Tok",
            centroid=GeoPoint(lon=79.5, lat=30.4),
            population=100,
            households=20,
            demographics=DemographicProfile(
                elderly_60_plus=1,
                children_under_5=1,
                persons_with_disability=0,
                medically_dependent=0,
                low_income_households=5,
            ),
            structure_mix={StructureType.KUTCHA: 0.5, StructureType.PUCCA: 0.2},
            district="Chamoli",
            state="Uttarakhand",
        )


def test_habitation_id_pattern_is_enforced() -> None:
    with pytest.raises(ValueError):
        _habitation("village-1", 79.5, 30.4)


def test_coordinates_outside_the_study_bbox_fail_the_gate(tmp_path: Path) -> None:
    outside = _habitation("H-01", 77.2090, 28.6139)  # Delhi, far outside the corridor
    _write(tmp_path, "habitations.json", [json.loads(outside.model_dump_json())])
    report = validate_all(fixtures_dir=tmp_path)
    assert not report.ok
    assert any("outside" in error for error in report.errors)


def test_duplicate_habitation_ids_fail_the_gate(tmp_path: Path) -> None:
    area = get_study_area()
    a = _habitation("H-01", area.centre.lon, area.centre.lat)
    b = _habitation("H-01", area.centre.lon + 0.01, area.centre.lat + 0.01)
    _write(
        tmp_path,
        "habitations.json",
        [json.loads(a.model_dump_json()), json.loads(b.model_dump_json())],
    )
    report = validate_all(fixtures_dir=tmp_path)
    assert not report.ok
    assert any("duplicate habitation id" in error for error in report.errors)


def test_enforce_raises_so_the_api_refuses_to_start(tmp_path: Path) -> None:
    outside = _habitation("H-02", 77.2090, 28.6139)
    _write(tmp_path, "habitations.json", [json.loads(outside.model_dump_json())])
    with pytest.raises(FixtureValidationError):
        enforce(fixtures_dir=tmp_path)


def test_malformed_fixture_file_is_a_load_failure_not_a_silent_empty_list(
    tmp_path: Path,
) -> None:
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "habitations.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(FixtureError):
        load_fixtures(tmp_path)


def test_absent_fixture_files_are_absent_not_invented(tmp_path: Path) -> None:
    bundle = load_fixtures(tmp_path)
    assert bundle.count == 0
    assert bundle.files_present == []


def test_real_open_dataset_must_cite_a_source_url() -> None:
    with pytest.raises(ValueError, match="source_url"):
        DatasetRecord(
            id="mystery-layer",
            name="Unsourced real data",
            source="somewhere",
            provenance=ProvenanceClass.REAL_OPEN,
            acquired="2026-01-01",
            processing="none",
            resolution="30 m",
            temporal_coverage="2026",
            confidence="HIGH",
            licence="unknown",
        )


def test_duplicate_dataset_ids_are_rejected() -> None:
    record = DatasetRecord(
        id="dup",
        name="Duplicate",
        source="ASTRA",
        provenance=ProvenanceClass.DEMO_CONFIG,
        acquired="2026-01-01",
        processing="none",
        resolution="not spatial",
        temporal_coverage="static",
        confidence="HIGH",
        licence="Repository licence",
    )
    with pytest.raises(ProvenanceError, match="duplicate dataset ids"):
        ProvenanceRegistry([record, record])


def test_missing_registry_is_a_hard_failure(tmp_path: Path) -> None:
    with pytest.raises(ProvenanceError, match="missing"):
        load_registry(tmp_path / "nope.json")


def test_registry_counts_do_not_blend_provenance_classes() -> None:
    registry = load_registry()
    counts = registry.counts()
    assert set(counts) == {p.value for p in ProvenanceClass}
    assert sum(counts.values()) == len(registry.records)
