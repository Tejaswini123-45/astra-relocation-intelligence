"""Core domain models.

These types are the contract. They are exported through the OpenAPI schema and
regenerated into TypeScript in ``packages/contracts``, so the frontend renders
exactly the fields the engines produce and never redefines them by hand
(CLAUDE.md section 2.4).

Two structural commitments show up repeatedly below:

* Hazard, exposure and vulnerability are kept as separate quantities, never
  collapsed into one number (section 5.2).
* Confidence travels next to a value, never inside it (section 5.2).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from astra.domain.enums import (
    ConfidenceBand,
    EventType,
    HazardType,
    PerturbationKind,
    PhaseTier,
    ProvenanceClass,
    RoadClass,
    ServiceType,
    StructureType,
    SuitabilityGate,
    ZoneClass,
)

# ---------------------------------------------------------------------------
# Geometry primitives
# ---------------------------------------------------------------------------


class GeoPoint(BaseModel):
    """WGS84 longitude/latitude pair. All ASTRA geometry is EPSG:4326 on the wire."""

    model_config = ConfigDict(frozen=True)

    lon: float = Field(ge=-180.0, le=180.0)
    lat: float = Field(ge=-90.0, le=90.0)


class BBox(BaseModel):
    """Axis-aligned bounding box in WGS84."""

    model_config = ConfigDict(frozen=True)

    min_lon: float = Field(ge=-180.0, le=180.0)
    min_lat: float = Field(ge=-90.0, le=90.0)
    max_lon: float = Field(ge=-180.0, le=180.0)
    max_lat: float = Field(ge=-90.0, le=90.0)

    @model_validator(mode="after")
    def _ordered(self) -> BBox:
        if self.min_lon >= self.max_lon or self.min_lat >= self.max_lat:
            raise ValueError("bbox minimum must be strictly less than maximum")
        return self

    def contains(self, point: GeoPoint) -> bool:
        return (
            self.min_lon <= point.lon <= self.max_lon
            and self.min_lat <= point.lat <= self.max_lat
        )

    def as_list(self) -> list[float]:
        return [self.min_lon, self.min_lat, self.max_lon, self.max_lat]


class Geometry(BaseModel):
    """A GeoJSON geometry, carried through the API untouched."""

    model_config = ConfigDict(frozen=True)

    type: Literal[
        "Point",
        "LineString",
        "Polygon",
        "MultiPoint",
        "MultiLineString",
        "MultiPolygon",
    ]
    coordinates: Any = Field(description="GeoJSON coordinate array for the given type.")


# ---------------------------------------------------------------------------
# Provenance (section 4.2)
# ---------------------------------------------------------------------------


class DatasetRecord(BaseModel):
    """One dataset in the provenance registry. Every field below is mandatory.

    The registry is what lets the UI state honestly, per layer, whether a number
    came from real open data, from an ASTRA derivation, from a calibrated
    synthetic generator, or from an ASTRA policy constant.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Stable dataset identifier, referenced by layers.")
    name: str
    source: str = Field(description="Publishing organisation or generator.")
    source_url: str | None = Field(
        default=None, description="Where the data came from. Null only for synthetic."
    )
    provenance: ProvenanceClass
    acquired: date = Field(description="Date the data was fetched or generated.")
    processing: str = Field(description="What ASTRA did to it, in one sentence.")
    resolution: str = Field(description="Spatial or thematic resolution.")
    temporal_coverage: str = Field(description="Period the data represents.")
    confidence: ConfidenceBand = Field(description="Fitness of this dataset for use.")
    licence: str = Field(description="Licence the data is used under.")
    notes: str | None = None

    @model_validator(mode="after")
    def _real_data_cites_its_source(self) -> DatasetRecord:
        if self.provenance is ProvenanceClass.REAL_OPEN and not self.source_url:
            raise ValueError(
                f"dataset '{self.id}' is REAL_OPEN but has no source_url; real data "
                "must be traceable to where it came from"
            )
        return self


class LayerDescriptor(BaseModel):
    """A map layer offered to the frontend, bound to its backing datasets."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    description: str
    provenance: ProvenanceClass
    dataset_ids: list[str] = Field(
        description="Datasets this layer is computed from, resolved in /provenance."
    )
    geometry_type: Literal["raster", "point", "line", "polygon"]
    available: bool = Field(
        description="False until the slice that produces this layer has landed. "
        "The UI greys the control out rather than showing an empty layer."
    )
    unit: str | None = None
    default_visible: bool = False


# ---------------------------------------------------------------------------
# Explainability primitives (section 6)
# ---------------------------------------------------------------------------


class FactorContribution(BaseModel):
    """One term in a weighted sum, with the arithmetic left visible."""

    model_config = ConfigDict(frozen=True)

    factor: str
    raw_value: float | None = Field(
        default=None, description="Input in its own units, before normalisation."
    )
    normalised_value: float = Field(ge=0.0, le=1.0)
    weight: float
    contribution: float = Field(description="weight x normalised_value")
    provenance: ProvenanceClass
    unit: str | None = None

    @model_validator(mode="after")
    def _contribution_matches(self) -> FactorContribution:
        expected = self.weight * self.normalised_value
        if abs(expected - self.contribution) > 1e-6:
            raise ValueError(
                f"factor '{self.factor}': contribution {self.contribution} does not "
                f"equal weight x normalised value ({expected})"
            )
        return self


class ConfidenceReport(BaseModel):
    """Evidence confidence, reported beside a value and never folded into it."""

    model_config = ConfigDict(frozen=True)

    value: float = Field(ge=0.0, le=1.0)
    band: ConfidenceBand
    components: list[FactorContribution] = Field(default_factory=list)
    evidence_age_days: int | None = None
    note: str | None = None


class ValueExplanation(BaseModel):
    """The payload behind every inspectable number in the interface."""

    model_config = ConfigDict(frozen=True)

    value: float
    label: str
    unit: str | None = None
    formula_id: str = Field(description="Resolvable in the formula registry.")
    formula_version: str
    engine_version: str
    model_config_version: str
    factors: list[FactorContribution] = Field(default_factory=list)
    confidence: ConfidenceReport | None = None
    computed_at: datetime
    notes: str | None = None


# ---------------------------------------------------------------------------
# Habitations (section 5.2)
# ---------------------------------------------------------------------------


class DemographicProfile(BaseModel):
    """Population structure of a habitation.

    Proportions are calibrated to published Census/SECC district figures; the
    absolute counts they are applied to are synthetic (section 4.1).
    """

    model_config = ConfigDict(frozen=True)

    elderly_60_plus: int = Field(ge=0)
    children_under_5: int = Field(ge=0)
    persons_with_disability: int = Field(ge=0)
    medically_dependent: int = Field(ge=0)
    low_income_households: int = Field(ge=0)

    def shares(self, population: int, households: int) -> dict[str, float]:
        """Component shares used by the vulnerability index."""
        pop = max(population, 1)
        hh = max(households, 1)
        return {
            "elderly": self.elderly_60_plus / pop,
            "children_u5": self.children_under_5 / pop,
            "disability": self.persons_with_disability / pop,
            "medical_dependency": self.medically_dependent / pop,
            "low_income": min(self.low_income_households / hh, 1.0),
        }


class CriticalFacility(BaseModel):
    """A facility whose loss compounds the consequence of a hazard event."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: Literal["SCHOOL", "CLINIC", "ANGANWADI", "COMMUNITY_HALL"]
    name: str
    location: GeoPoint


class Habitation(BaseModel):
    """A settlement under assessment.

    Habitation records in the demonstration scenario are synthetic, fictional and
    terrain-calibrated. ASTRA never classifies a real named village.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^H-\d{2}$")
    name: str
    centroid: GeoPoint
    footprint: Geometry | None = None
    population: int = Field(gt=0)
    households: int = Field(gt=0)
    demographics: DemographicProfile
    structure_mix: dict[StructureType, float] = Field(
        description="Dwelling typology shares. Must sum to 1.0."
    )
    critical_facilities: list[CriticalFacility] = Field(default_factory=list)
    livelihood_centre: GeoPoint | None = Field(
        default=None,
        description="Where residents work or trade, used by livelihood disruption.",
    )
    elevation_m: float | None = None
    block: str | None = Field(default=None, description="Administrative block.")
    district: str
    state: str
    provenance: ProvenanceClass = ProvenanceClass.SYNTHETIC_CALIBRATED

    @model_validator(mode="after")
    def _internally_consistent(self) -> Habitation:
        share_total = sum(self.structure_mix.values())
        if abs(share_total - 1.0) > 1e-6:
            raise ValueError(
                f"{self.id}: structure_mix shares sum to {share_total:.4f}, must be 1.0"
            )
        counted = (
            self.demographics.elderly_60_plus
            + self.demographics.children_under_5
            + self.demographics.persons_with_disability
            + self.demographics.medically_dependent
        )
        if counted > self.population * 2:
            raise ValueError(
                f"{self.id}: demographic counts ({counted}) are implausible against a "
                f"population of {self.population}"
            )
        if self.demographics.low_income_households > self.households:
            raise ValueError(
                f"{self.id}: low-income households exceed total households"
            )
        return self


# ---------------------------------------------------------------------------
# Candidate relocation sites (section 5.4)
# ---------------------------------------------------------------------------


class ServiceSupply(BaseModel):
    """Measured or assumed supply of one service at a candidate site."""

    model_config = ConfigDict(frozen=True)

    service: ServiceType
    supply: float = Field(ge=0.0, description="Supply in the unit below.")
    unit: str
    provenance: ProvenanceClass
    source_note: str | None = None


class CandidateSite(BaseModel):
    """A candidate relocation site.

    Site records in the demonstration scenario are synthetic and fictional.
    ASTRA does not verify land ownership, tenure or encumbrance.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^S-\d{2}$")
    name: str
    centroid: GeoPoint
    boundary: Geometry | None = None
    gross_area_m2: float = Field(gt=0.0)
    mean_slope_deg: float = Field(ge=0.0, le=90.0)
    elevation_m: float
    distance_to_road_m: float = Field(ge=0.0)
    existing_shelter_units: int = Field(ge=0, default=0)
    constructable_units: int = Field(ge=0, default=0)
    services: list[ServiceSupply] = Field(default_factory=list)
    district: str
    state: str
    provenance: ProvenanceClass = ProvenanceClass.SYNTHETIC_CALIBRATED
    notes: str | None = None

    def supply_for(self, service: ServiceType) -> ServiceSupply | None:
        for entry in self.services:
            if entry.service is service:
                return entry
        return None


class GateResult(BaseModel):
    """Outcome of one hard suitability gate. A failure is named, never scored away."""

    model_config = ConfigDict(frozen=True)

    gate: SuitabilityGate
    passed: bool
    observed: float | None = None
    threshold: float | None = None
    unit: str | None = None
    detail: str


class ServiceCapacity(BaseModel):
    """Capacity of a site as limited by one service."""

    model_config = ConfigDict(frozen=True)

    service: ServiceType
    supply: float
    supply_unit: str
    norm_value: float
    norm_unit: str
    norm_provenance: ProvenanceClass
    norm_citation: str | None = None
    capacity_persons: float = Field(ge=0.0)


# ---------------------------------------------------------------------------
# Road network and routes (section 5.5)
# ---------------------------------------------------------------------------


class RoadSegment(BaseModel):
    """One edge of the routed graph."""

    model_config = ConfigDict(frozen=True)

    id: str
    from_node: str
    to_node: str
    geometry: Geometry
    length_m: float = Field(gt=0.0)
    road_class: RoadClass
    bridge_dependency: bool = False
    hazard_exposure: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Normalised composite hazard on the segment."
    )
    closed: bool = Field(
        default=False, description="Scenario-controlled closure state."
    )
    provenance: ProvenanceClass = ProvenanceClass.REAL_OPEN


# ---------------------------------------------------------------------------
# Hazard output (section 5.1)
# ---------------------------------------------------------------------------


class HazardScore(BaseModel):
    """Susceptibility for one hazard at one location, with its decomposition."""

    model_config = ConfigDict(frozen=True)

    hazard: HazardType
    score: float = Field(ge=0.0, le=100.0)
    factors: list[FactorContribution]


class CompositeHazard(BaseModel):
    """The multi-hazard composite, with the full per-hazard vector retained."""

    model_config = ConfigDict(frozen=True)

    composite: float = Field(ge=0.0, le=100.0)
    dominant_hazard: HazardType
    second_hazard: HazardType | None = None
    per_hazard: list[HazardScore]
    zone_class: ZoneClass
    classification_label: str = Field(
        description="Always the ASTRA analytical classification label, never official."
    )


# ---------------------------------------------------------------------------
# Scenario (section 5.7)
# ---------------------------------------------------------------------------


class StudyArea(BaseModel):
    """The geographic frame every layer and fixture must fall inside."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    district: str
    state: str
    bbox: BBox
    centre: GeoPoint
    default_zoom: float = 11.0
    description: str


class Perturbation(BaseModel):
    """One change a scenario makes to the world.

    A perturbation is data, not a UI toggle: it is stored on the scenario, echoed
    on every result computed under it, and is what makes a what-if reproducible
    rather than a state the interface happened to be in.
    """

    model_config = ConfigDict(frozen=True)

    kind: PerturbationKind
    target: str | None = Field(
        default=None,
        description=(
            "What the change applies to - a site id, habitation id, road segment "
            "id or service name. Null means it applies across the study area."
        ),
    )
    value: float = Field(
        description="The magnitude, in the units the kind defines. A multiplier "
        "of 1.0, a shift of 0.0 or a loss of 0.0 changes nothing."
    )
    note: str | None = None

    def describe(self) -> str:
        """One line an official can read, assembled from the values themselves."""
        where = self.target or "the whole study area"
        match self.kind:
            case PerturbationKind.RAINFALL_MULTIPLIER:
                return f"Rainfall intensity x{self.value:g} across {where}"
            case PerturbationKind.LANDSLIDE_SHIFT:
                return f"Landslide susceptibility shifted by {self.value:+.2f}"
            case PerturbationKind.ROAD_CLOSURE:
                return f"Road segment {where} closed"
            case PerturbationKind.SITE_CAPACITY_LOSS:
                return f"{where} loses {self.value * 100:.0f}% of its service supply"
            case PerturbationKind.SERVICE_UPGRADE:
                return f"{where} gains {self.value:g} units of service supply"
            case PerturbationKind.SITE_DISABLED:
                return f"{where} withdrawn from consideration"
            case PerturbationKind.POPULATION_MULTIPLIER:
                return f"Population of {where} x{self.value:g}"
        return f"{self.kind.value} {self.value:g} at {where}"


class Scenario(BaseModel):
    """A named, versioned analysis context. Scenarios are diffable objects."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    description: str
    study_area_id: str
    is_baseline: bool = False
    created_at: datetime
    disclaimer: str
    changes: list[Perturbation] = Field(
        default_factory=list,
        description="Perturbations applied against the baseline scenario.",
    )
    derived_from: str | None = Field(
        default=None, description="Scenario this one perturbs, when it is not the baseline."
    )


# ---------------------------------------------------------------------------
# Priority output (sections 5.2, 5.3)
# ---------------------------------------------------------------------------


class HabitationRisk(BaseModel):
    """Hazard, exposure, vulnerability and history, kept structurally separate."""

    model_config = ConfigDict(frozen=True)

    habitation_id: str
    hazard: CompositeHazard
    exposure_index: float = Field(ge=0.0, le=1.0)
    vulnerability_index: float = Field(ge=0.0, le=1.0)
    history_index: float = Field(ge=0.0, le=1.0)
    priority_score: float = Field(ge=0.0, le=100.0)
    priority_rank: int = Field(ge=1)
    phase: PhaseTier
    phase_reason: str
    overrides_applied: list[str] = Field(default_factory=list)
    confidence: ConfidenceReport
    explanation: ValueExplanation
    note: str = Field(
        description="Standing reminder that priority is a rank, not a probability."
    )



# ---------------------------------------------------------------------------
# Live event ingest (section 5.8)
# ---------------------------------------------------------------------------


class HazardEvent(BaseModel):
    """One observation arriving while the system is running.

    An event is not a scenario. A scenario asks *what if*; an event says *this
    happened, here, at this time*. So it carries a location and a footprint - the
    ground the observation actually speaks for - and only that ground is
    re-scored because of it. Everything outside the footprint keeps the value it
    already had, which is what makes a live update incremental rather than a
    quiet full recomputation wearing a progress bar.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Assigned by ASTRA on ingest.")
    kind: EventType
    observed_at: datetime
    received_at: datetime
    lon: float | None = Field(
        default=None, description="Longitude, WGS84. Null for a road-status event."
    )
    lat: float | None = None
    radius_m: float = Field(
        default=1500.0,
        gt=0.0,
        description=(
            "The footprint this observation speaks for. A rain gauge does not "
            "measure the next valley, and ASTRA does not pretend it does: outside "
            "this radius nothing is re-scored."
        ),
    )
    value: float = Field(
        description=(
            "The measurement, in the units the kind defines: millimetres for "
            "rainfall, a severity weight for an incident, a 0-1 instability "
            "observation for field evidence, 1 for a road closed and 0 for one "
            "reopened."
        )
    )
    target: str | None = Field(
        default=None,
        description="Road segment id for an infrastructure-status event.",
    )
    source: str = Field(
        description="Who or what reported this. Shown on the evidence trail."
    )
    provenance: ProvenanceClass = ProvenanceClass.SYNTHETIC_CALIBRATED
    note: str | None = None

    @property
    def has_location(self) -> bool:
        return self.lon is not None and self.lat is not None

    def describe(self) -> str:
        """One line an official can read, assembled from the values themselves."""
        where = (
            f"{self.lat:.4f}, {self.lon:.4f}"
            if self.has_location
            else (self.target or "the study area")
        )
        match self.kind:
            case EventType.RAINFALL_OBSERVATION:
                return (
                    f"{self.value:g} mm rainfall observed at {where} "
                    f"({self.radius_m / 1000:g} km footprint)"
                )
            case EventType.INCIDENT_REPORT:
                return f"Incident reported at {where}, severity weight {self.value:g}"
            case EventType.FIELD_EVIDENCE:
                return (
                    f"Field evidence of ground instability at {where}, "
                    f"observed severity {self.value:.2f}"
                )
            case EventType.INFRASTRUCTURE_STATUS:
                state = "impassable" if self.value >= 0.5 else "reopened"
                return f"Road segment {self.target} reported {state}"
        return f"{self.kind.value} {self.value:g} at {where}"


# ---------------------------------------------------------------------------
# System status
# ---------------------------------------------------------------------------


class HealthStatus(BaseModel):
    """Served by ``GET /health``. Also drives the status strip in the UI header."""

    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "degraded"]
    api_version: str
    engine_version: str
    model_config_version: str
    environment: str
    fixtures_valid: bool
    fixture_count: int
    llm_mode: Literal["template", "connected"] = Field(
        description="Template mode means no LLM key is configured. The full analysis "
        "runs identically either way; only the prose narration differs."
    )
    how_this_works: str = Field(
        description="What is computed and what AI does. Rendered on every screen."
    )
    decision_authority: str
    started_at: datetime
    checked_at: datetime
