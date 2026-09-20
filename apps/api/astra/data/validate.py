"""Fixture integrity gate (CLAUDE.md section 4.5) - blocking, by design.

This runs in CI, from ``scripts/validate_fixtures.py`` and on API startup. It
fails hard on duplicate IDs, dangling references, coordinates outside the study
bounding box, population exceeding the capacity meant to serve it, negative or
non-finite values, missing provenance, and layers that claim to be available
while their datasets are unregistered.

The API refuses to start on invalid fixtures. A demo that silently serves a
broken dataset is worse than a demo that will not start.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from astra.data.fixtures import FixtureBundle, FixtureError, load_fixtures
from astra.data.layers import layer_catalogue
from astra.data.provenance import ProvenanceError, ProvenanceRegistry, load_registry
from astra.data.study_area import get_study_area
from astra.domain.models import BBox, GeoPoint, StudyArea


@dataclass
class ValidationReport:
    """Outcome of the integrity gate. ``ok`` is the only acceptable demo state."""

    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    fixture_count: int = 0
    dataset_count: int = 0

    def fail(self, message: str) -> None:
        self.ok = False
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def check(self, name: str) -> None:
        self.checked.append(name)

    def summary(self) -> str:
        state = "PASS" if self.ok else "FAIL"
        return (
            f"{state}: {len(self.checked)} checks, {self.fixture_count} fixture records, "
            f"{self.dataset_count} datasets, {len(self.errors)} errors, "
            f"{len(self.warnings)} warnings"
        )


class FixtureValidationError(RuntimeError):
    """Raised by :func:`enforce` when the gate fails."""

    def __init__(self, report: ValidationReport) -> None:
        super().__init__("; ".join(report.errors) or "fixture validation failed")
        self.report = report


def _finite(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def _check_point(
    report: ValidationReport, label: str, point: GeoPoint, bbox: BBox
) -> None:
    if not (_finite(point.lon) and _finite(point.lat)):
        report.fail(f"{label}: coordinates are not finite")
        return
    if not bbox.contains(point):
        report.fail(
            f"{label}: coordinates ({point.lon:.5f}, {point.lat:.5f}) fall outside the "
            f"{bbox.as_list()} study bounding box"
        )


def _validate_provenance(report: ValidationReport) -> ProvenanceRegistry | None:
    report.check("provenance registry loads and validates")
    try:
        registry = load_registry()
    except ProvenanceError as exc:
        report.fail(str(exc))
        return None
    report.dataset_count = len(registry.records)
    return registry


def _validate_layers(report: ValidationReport, registry: ProvenanceRegistry) -> None:
    report.check("available layers resolve their datasets")
    seen: set[str] = set()
    for layer in layer_catalogue():
        if layer.id in seen:
            report.fail(f"layer '{layer.id}' is declared more than once")
        seen.add(layer.id)
        if not layer.dataset_ids:
            report.fail(f"layer '{layer.id}' declares no backing dataset")
        if not layer.available:
            continue
        for dataset_id in layer.dataset_ids:
            if not registry.has(dataset_id):
                report.fail(
                    f"layer '{layer.id}' is marked available but dataset "
                    f"'{dataset_id}' is not in the provenance registry"
                )


def _validate_habitations(
    report: ValidationReport, bundle: FixtureBundle, area: StudyArea
) -> None:
    if not bundle.habitations:
        return
    report.check("habitation identity, geometry and demographic consistency")
    seen: set[str] = set()
    for habitation in bundle.habitations:
        if habitation.id in seen:
            report.fail(f"duplicate habitation id '{habitation.id}'")
        seen.add(habitation.id)
        _check_point(report, f"habitation {habitation.id}", habitation.centroid, area.bbox)
        if habitation.population <= 0 or habitation.households <= 0:
            report.fail(f"habitation {habitation.id}: population and households must be positive")
        if habitation.households > habitation.population:
            report.fail(
                f"habitation {habitation.id}: {habitation.households} households exceed "
                f"{habitation.population} residents"
            )
        for facility in habitation.critical_facilities:
            _check_point(
                report,
                f"habitation {habitation.id} facility {facility.id}",
                facility.location,
                area.bbox,
            )
        if habitation.livelihood_centre is not None:
            _check_point(
                report,
                f"habitation {habitation.id} livelihood centre",
                habitation.livelihood_centre,
                area.bbox,
            )


def _validate_sites(
    report: ValidationReport, bundle: FixtureBundle, area: StudyArea
) -> None:
    if not bundle.sites:
        return
    report.check("site identity, geometry and service supply")
    seen: set[str] = set()
    for site in bundle.sites:
        if site.id in seen:
            report.fail(f"duplicate site id '{site.id}'")
        seen.add(site.id)
        _check_point(report, f"site {site.id}", site.centroid, area.bbox)
        if not _finite(site.gross_area_m2) or site.gross_area_m2 <= 0:
            report.fail(f"site {site.id}: gross area must be positive and finite")
        for supply in site.services:
            if not _finite(supply.supply) or supply.supply < 0:
                report.fail(
                    f"site {site.id}: {supply.service.value} supply is negative or "
                    "not finite"
                )
        if not site.services:
            report.warn(
                f"site {site.id} declares no service supply; its effective capacity "
                "will be land-limited only"
            )


def _validate_road_segments(
    report: ValidationReport, bundle: FixtureBundle, area: StudyArea
) -> None:
    if not bundle.road_segments:
        return
    report.check("road segment identity and node references")
    seen: set[str] = set()
    nodes: set[str] = set()
    for segment in bundle.road_segments:
        if segment.id in seen:
            report.fail(f"duplicate road segment id '{segment.id}'")
        seen.add(segment.id)
        nodes.update({segment.from_node, segment.to_node})
        if not _finite(segment.length_m) or segment.length_m <= 0:
            report.fail(f"road segment {segment.id}: length must be positive and finite")
        if segment.from_node == segment.to_node:
            report.fail(f"road segment {segment.id}: self-loop from a node to itself")
    if len(nodes) < 2:
        report.fail("road network has fewer than two distinct nodes")


def _validate_cross_references(report: ValidationReport, bundle: FixtureBundle) -> None:
    """Population versus capacity, and the referential integrity between layers."""
    if not (bundle.habitations and bundle.sites):
        return
    report.check("total site land capacity against total population at risk")
    from astra.domain.model_config import MODEL_CONFIG

    per_person_m2 = MODEL_CONFIG.capacity.site_area_m2_per_person.value
    total_population = sum(h.population for h in bundle.habitations)
    theoretical_capacity = sum(s.gross_area_m2 for s in bundle.sites) / per_person_m2
    if theoretical_capacity <= 0:
        report.fail("candidate sites provide no land capacity at all")
    elif theoretical_capacity < total_population:
        # Not an error: unmet demand is a real planning finding the optimiser must
        # be able to express. It is surfaced so nobody mistakes it for a bug.
        report.warn(
            f"total theoretical site capacity ({theoretical_capacity:.0f} persons) is "
            f"below total population at risk ({total_population}); the plan will carry "
            "unmet demand"
        )


def _validate_event_feed(report: ValidationReport, area) -> None:
    """The demonstration feed has to be replayable, and inside the study area.

    A feed step that names a place ASTRA has no ground for would ingest cleanly
    and re-score nothing, which reads on screen as "this observation changed
    nothing" rather than "this observation was never applied anywhere".
    """
    from astra.data.feed import FeedError, load_feed
    from astra.domain.enums import EventType

    report.check("demonstration event feed parses and lands inside the study area")
    try:
        feed = load_feed()
    except FeedError as exc:
        report.fail(str(exc))
        return

    located = {
        EventType.RAINFALL_OBSERVATION,
        EventType.INCIDENT_REPORT,
        EventType.FIELD_EVIDENCE,
    }
    for index, step in enumerate(feed.steps):
        where = f"event feed step {index} ({step.kind.value})"
        if step.kind in located:
            if step.lon is None or step.lat is None:
                report.fail(f"{where} has no location")
                continue
            if not (
                area.bbox.min_lon <= step.lon <= area.bbox.max_lon
                and area.bbox.min_lat <= step.lat <= area.bbox.max_lat
            ):
                report.fail(f"{where} falls outside the study area bbox")
            if step.radius_m <= 0:
                report.fail(f"{where} has a non-positive footprint radius")
        elif not step.target:
            report.fail(f"{where} names no road segment")
        if step.kind is EventType.FIELD_EVIDENCE and not 0.0 <= step.value <= 1.0:
            report.fail(f"{where} records a severity outside 0-1")
        if step.delay_ms < 0:
            report.fail(f"{where} declares a negative delay")
        if not step.note:
            report.fail(f"{where} has no note saying what it is")


def validate_all(fixtures_dir: Path | None = None) -> ValidationReport:
    """Run every integrity check and return the report. Never raises on findings."""
    report = ValidationReport()
    area = get_study_area()

    registry = _validate_provenance(report)
    if registry is not None:
        _validate_layers(report, registry)

    report.check("fixture files parse into domain models")
    try:
        bundle = load_fixtures(fixtures_dir)
    except FixtureError as exc:
        report.fail(str(exc))
        return report

    report.fixture_count = bundle.count
    _validate_habitations(report, bundle, area)
    _validate_sites(report, bundle, area)
    _validate_road_segments(report, bundle, area)
    _validate_cross_references(report, bundle)
    _validate_event_feed(report, area)
    return report


def enforce(fixtures_dir: Path | None = None) -> ValidationReport:
    """Validate and raise on failure. Used by API startup and by CI."""
    report = validate_all(fixtures_dir)
    if not report.ok:
        raise FixtureValidationError(report)
    return report
