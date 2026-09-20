"""The synthetic layer must be honest, reproducible and inside the study area.

Also asserts the property the whole data strategy rests on: nothing reads from
the network at run time. A connector without its vendored artifact fails loudly
instead of quietly fetching.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra.data.connectors import CONNECTOR_TYPES, ConnectorError, build_connectors
from astra.data.fixtures import load_fixtures
from astra.data.study_area import get_study_area
from astra.domain.enums import ProvenanceClass
from astra.domain.model_config import MODEL_CONFIG
from astra.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[3]

# Real settlements inside or beside the corridor. Not one of them may appear as
# an ASTRA habitation or site name: ASTRA never classifies a real named village.
REAL_PLACE_NAMES = {
    "joshimath",
    "karnaprayag",
    "gopeshwar",
    "chamoli",
    "pipalkoti",
    "helang",
    "tapovan",
    "reni",
    "raini",
    "malari",
    "nandprayag",
    "birahi",
    "ghat",
    "nandanagar",
    "urgam",
    "auli",
    "badrinath",
    "govindghat",
    "pandukeshwar",
    "vishnuprayag",
    "gwaldam",
    "rudraprayag",
    "srinagar",
    "dehradun",
}


@pytest.fixture(scope="module")
def bundle():
    return load_fixtures()


def test_the_demonstration_dataset_is_seeded(bundle) -> None:
    assert len(bundle.habitations) == int(MODEL_CONFIG.generation.habitation_count.value)
    assert len(bundle.sites) == int(MODEL_CONFIG.generation.site_count.value)


def test_no_habitation_or_site_carries_a_real_settlement_name(bundle) -> None:
    """Matched on whole words: 'Rauligaon' is fictional, 'Auli' would not be."""
    for record in [*bundle.habitations, *bundle.sites]:
        tokens = {token.strip(",.-").lower() for token in record.name.split()}
        collisions = tokens & REAL_PLACE_NAMES
        assert not collisions, (
            f"{record.id} is named '{record.name}', which collides with the real "
            f"settlement(s) {sorted(collisions)}. ASTRA never classifies a real "
            "named village."
        )


def test_every_synthetic_record_declares_itself_synthetic(bundle) -> None:
    for record in [*bundle.habitations, *bundle.sites]:
        assert record.provenance is ProvenanceClass.SYNTHETIC_CALIBRATED


def test_records_sit_inside_the_study_area(bundle) -> None:
    bbox = get_study_area().bbox
    for record in [*bundle.habitations, *bundle.sites]:
        assert bbox.contains(record.centroid)


def test_populations_respect_the_declared_generation_bounds(bundle) -> None:
    config = MODEL_CONFIG.generation
    for habitation in bundle.habitations:
        assert config.population_min.value <= habitation.population <= config.population_max.value


def test_habitations_sit_in_the_habitable_elevation_band(bundle) -> None:
    config = MODEL_CONFIG.generation
    for habitation in bundle.habitations:
        assert habitation.elevation_m is not None
        assert (
            config.settlement_min_elevation_m.value
            <= habitation.elevation_m
            <= config.settlement_max_elevation_m.value
        )


def test_sites_sit_on_ground_that_could_actually_be_built_on(bundle) -> None:
    for site in bundle.sites:
        assert site.mean_slope_deg <= MODEL_CONFIG.capacity.gate_max_slope_deg.value
        assert site.gross_area_m2 > 0
        assert site.distance_to_road_m <= MODEL_CONFIG.capacity.gate_max_road_distance_m.value


def test_habitations_span_a_range_of_terrain_exposure(bundle) -> None:
    """A dataset where every settlement is equally safe would prove nothing."""
    elevations = [h.elevation_m for h in bundle.habitations if h.elevation_m is not None]
    assert max(elevations) - min(elevations) > 300


def test_generation_manifest_separates_measured_from_assumed() -> None:
    manifest_path = get_settings().fixtures_dir / "generation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["measured_from_real_data"]
    assert manifest["assumed_not_measured"]
    assumed = " ".join(manifest["assumed_not_measured"]).lower()
    assert "demographic" in assumed, "demographic assumptions must be declared as assumed"
    assert manifest["provenance"] == ProvenanceClass.SYNTHETIC_CALIBRATED.value


def test_generation_is_deterministic(bundle) -> None:
    """Re-running the generator on the same surfaces must reproduce the dataset."""
    pytest.importorskip("rasterio")
    import sys

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from seed_fixtures import load_surfaces  # noqa: PLC0415

    from astra.data.generate import generate_habitations, generate_sites

    area = get_study_area()
    stack = load_surfaces()
    regenerated = generate_habitations(stack, area)
    assert [h.model_dump() for h in regenerated] == [
        h.model_dump() for h in bundle.habitations
    ]
    assert [s.model_dump() for s in generate_sites(stack, area)] == [
        s.model_dump() for s in bundle.sites
    ]


def test_demographic_counts_are_internally_consistent(bundle) -> None:
    for habitation in bundle.habitations:
        demographics = habitation.demographics
        assert demographics.low_income_households <= habitation.households
        assert (
            demographics.elderly_60_plus
            + demographics.children_under_5
            + demographics.persons_with_disability
            + demographics.medically_dependent
        ) < habitation.population


# ---------------------------------------------------------------------------
# Connectors
# ---------------------------------------------------------------------------


def test_every_real_source_is_vendored_on_disk() -> None:
    settings = get_settings()
    for connector in build_connectors(settings.raw_dir, get_study_area().bbox):
        assert connector.is_cached(), (
            f"{connector.dataset_id} is not vendored; the demo must not depend on a "
            "network fetch"
        )


def test_a_missing_artifact_fails_loudly_instead_of_fetching(tmp_path: Path) -> None:
    for connector in build_connectors(tmp_path, get_study_area().bbox):
        with pytest.raises(ConnectorError, match="not vendored"):
            connector.require_cached()


def test_connector_records_are_valid_and_cite_their_source() -> None:
    from datetime import date

    settings = get_settings()
    for connector in build_connectors(settings.raw_dir, get_study_area().bbox):
        record = connector.dataset_record(date(2026, 1, 1))
        assert record.id == connector.dataset_id
        assert record.provenance is ProvenanceClass.REAL_OPEN
        assert record.source_url, "real open data must state where it came from"
        assert record.licence
        assert record.processing


def test_every_connector_is_registered_in_the_provenance_file() -> None:
    payload = json.loads(get_settings().provenance_path.read_text(encoding="utf-8"))
    registered = {record["id"] for record in payload["datasets"]}
    for connector_type in CONNECTOR_TYPES:
        assert connector_type.dataset_id in registered
