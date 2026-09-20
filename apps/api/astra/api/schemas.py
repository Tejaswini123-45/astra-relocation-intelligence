"""Response envelopes for the public API.

Every response is a declared Pydantic model so the OpenAPI schema is complete and
``packages/contracts`` can be generated from it. The frontend imports those
generated types and renders what it is given; it never redeclares a shape or
recomputes a number (CLAUDE.md section 2.4).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from astra.domain.enums import (
    ConfidenceBand,
    EventType,
    HazardType,
    PerturbationKind,
    PhaseTier,
    ProvenanceClass,
    RoadClass,
    RouteProfile,
    RunStage,
    RunStageStatus,
    RunStatus,
    ServiceType,
    SolverStatus,
    ZoneClass,
)
from astra.domain.model_config import AstraModelConfig, Constant
from astra.domain.models import (
    CandidateSite,
    CompositeHazard,
    ConfidenceReport,
    DatasetRecord,
    FactorContribution,
    GateResult,
    Geometry,
    GeoPoint,
    Habitation,
    LayerDescriptor,
    Perturbation,
    Scenario,
    ServiceCapacity,
    StudyArea,
    ValueExplanation,
)
from astra.domain.notices import Notices
from astra.domain.registry import FormulaSpec


class ModelConfigResponse(BaseModel):
    """The full transparency payload behind the Model and Provenance screen."""

    model_config = ConfigDict(frozen=True)

    config: AstraModelConfig
    constants: list[Constant] = Field(
        description="Every constant in the tree, flattened for the transparency table."
    )
    formulas: list[FormulaSpec] = Field(
        description="Every registered formula, so any number can be traced in one hop."
    )
    notices: Notices = Field(
        description="Standing framing text, served so the UI never retypes it."
    )


class ProvenanceResponse(BaseModel):
    """The dataset registry, with per-class counts kept unblended."""

    model_config = ConfigDict(frozen=True)

    registry_version: str
    datasets: list[DatasetRecord]
    counts_by_class: dict[str, int]
    note: str


class LayersResponse(BaseModel):
    """The layer catalogue, including layers that are declared but not yet available."""

    model_config = ConfigDict(frozen=True)

    layers: list[LayerDescriptor]
    available_count: int
    declared_count: int


class ValidationCheckResponse(BaseModel):
    """The fixture integrity gate result, surfaced rather than hidden in a log."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    summary: str
    checks: list[str]
    errors: list[str]
    warnings: list[str]
    fixture_count: int
    dataset_count: int


class ScenarioResponse(BaseModel):
    """A scenario with the study area it is framed by."""

    model_config = ConfigDict(frozen=True)

    scenario: Scenario
    study_area: StudyArea


class ScenarioListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenarios: list[Scenario]
    study_areas: list[StudyArea]


class HabitationsResponse(BaseModel):
    """The habitation layer, with the totals a planner reads first."""

    model_config = ConfigDict(frozen=True)

    habitations: list[Habitation]
    total_population: int
    total_households: int
    disclaimer: str = Field(
        description="Standing reminder that these records are synthetic and fictional."
    )


class SitesResponse(BaseModel):
    """The candidate-site layer. Capacity analysis lands with the capacity engine."""

    model_config = ConfigDict(frozen=True)

    sites: list[CandidateSite]
    total_gross_area_m2: float
    limitation: str = Field(
        description="The tenure limitation ASTRA states before it is asked."
    )


class DerivedLayerSummary(BaseModel):
    """One computed surface, with the range it actually spans."""

    model_config = ConfigDict(frozen=True)

    name: str
    file: str
    unit: str
    dtype: str
    bytes: int
    min: float
    max: float
    mean: float
    note: str


class StudyAreaDataResponse(BaseModel):
    """What ASTRA has actually built for the study area, and how."""

    model_config = ConfigDict(frozen=True)

    study_area: StudyArea
    grid: dict[str, float]
    methods: dict[str, str]
    channel_threshold_km2: float
    layers: list[DerivedLayerSummary]
    generation: dict[str, Any]
    terrain_preview_url: str
    terrain_preview_bbox: list[float]
    landcover_refinement: dict[str, Any] | None = Field(
        default=None,
        description="The scoped machine-learning component: its labelling rules, what "
        "each rule found, its measured accuracy, its agreement with the published "
        "land-cover product, and its caveats. Null until the model has been built.",
    )


class ZoneFeatureProperties(BaseModel):
    """Attributes carried by every published zone polygon."""

    model_config = ConfigDict(frozen=True)

    id: str
    zone_class: ZoneClass
    classification_label: str = Field(
        description="Always the ASTRA analytical label. Never an official designation."
    )
    area_km2: float
    mean_composite: float
    max_composite: float
    dominant_hazard: HazardType
    hazard_mix: dict[str, float]
    mean_confidence: float
    cell_count: int
    population_intersected: int
    habitation_ids: list[str]
    rule_version: str


class ZoneFeature(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["Feature"] = "Feature"
    id: str
    geometry: Geometry
    properties: ZoneFeatureProperties


class ZoneClassSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    count: int
    area_km2: float
    population_intersected: int


class ZonesResponse(BaseModel):
    """Red zones as a GeoJSON feature collection, with the totals precomputed."""

    model_config = ConfigDict(frozen=True)

    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[ZoneFeature]
    summary: dict[str, ZoneClassSummary]
    classification_label: str
    decision_authority: str
    model_config_version: str
    engine_version: str
    computed_ms: float


class RiskCellResponse(BaseModel):
    """Everything behind the hazard score at one point on the map."""

    model_config = ConfigDict(frozen=True)

    lon: float
    lat: float
    row: int
    col: int
    cell_bbox: list[float]
    hazard: CompositeHazard
    confidence: ConfidenceReport
    zone_id: str | None
    zone_class: ZoneClass
    formula: FormulaSpec
    composite_formula: FormulaSpec
    model_config_version: str
    engine_version: str
    computed_at: datetime


class HabitationHazardRow(BaseModel):
    """Hazard sampled over one habitation footprint. Exposure arrives in Slice 4."""

    model_config = ConfigDict(frozen=True)

    habitation_id: str
    name: str
    population: int
    households: int
    centroid: GeoPoint
    hazard: CompositeHazard
    footprint_mean_composite: float
    footprint_max_composite: float
    footprint_radius_m: float
    confidence: ConfidenceReport
    zone_id: str | None


class HabitationHazardResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    habitations: list[HabitationHazardRow]
    classification_label: str
    decision_authority: str
    scenario_disclaimer: str
    note: str


class RiskSummaryResponse(BaseModel):
    """What the hazard engine computed, and over what."""

    model_config = ConfigDict(frozen=True)

    study_area: StudyArea
    grid_rows: int
    grid_cols: int
    cell_x_m: float
    cell_y_m: float
    hazards_modelled: list[HazardType]
    class_share_percent: dict[str, float]
    hazard_statistics: dict[str, dict[str, float]]
    zone_summary: dict[str, ZoneClassSummary]
    incidents_used: int
    incidents_excluded: int
    rainfall_points: int
    composite_lambda: float
    zone_thresholds: dict[str, float]
    overlay_url: str
    overlay_bbox: list[float]
    terrain_url: str
    computed_ms: float
    model_config_version: str
    engine_version: str


class ComponentScoreResponse(BaseModel):
    """One input to the priority score, with its own factor decomposition."""

    model_config = ConfigDict(frozen=True)

    value: float = Field(ge=0.0, le=1.0)
    formula_id: str
    factors: list[FactorContribution]
    note: str | None = None


class PhaseDecision(BaseModel):
    """Which relocation phase a habitation falls in, and exactly why."""

    model_config = ConfigDict(frozen=True)

    phase: PhaseTier
    reason: str
    rules_applied: list[str] = Field(
        default_factory=list,
        description="Override rules that fired, named. An escalation is never silent.",
    )
    pending_checks: list[str] = Field(
        default_factory=list,
        description="Constraint checks that belong in this decision but whose engine "
        "has not been built yet. Listed rather than left implicit.",
    )


class HabitationPriorityRow(BaseModel):
    """A habitation's full assessment: hazard, exposure, vulnerability, history."""

    model_config = ConfigDict(frozen=True)

    rank: int
    habitation_id: str
    name: str
    population: int
    households: int
    centroid: GeoPoint
    elevation_m: float | None

    priority_score: float = Field(ge=0.0, le=100.0)
    priority_factors: list[FactorContribution]

    hazard: CompositeHazard
    hazard_component: ComponentScoreResponse
    footprint_mean_composite: float
    footprint_max_composite: float
    footprint_radius_m: float

    exposure: ComponentScoreResponse
    vulnerability: ComponentScoreResponse
    history: ComponentScoreResponse

    confidence: ConfidenceReport
    zone_class: ZoneClass
    zone_id: str | None
    phase: PhaseDecision


class PhaseTotals(BaseModel):
    model_config = ConfigDict(frozen=True)

    habitations: int
    population: int
    households: int


class HabitationPriorityResponse(BaseModel):
    """The ranked decision list behind the Habitation Priority screen."""

    model_config = ConfigDict(frozen=True)

    habitations: list[HabitationPriorityRow]
    totals_by_phase: dict[str, PhaseTotals]
    total_population_assessed: int
    weights: dict[str, float]
    tier_thresholds: dict[str, float]
    classification_label: str
    decision_authority: str
    scenario_disclaimer: str
    priority_note: str
    history_note: str
    computed_at: datetime
    model_config_version: str
    engine_version: str


class HabitationDetailResponse(BaseModel):
    """Everything behind one habitation's ranking, for the reasoning drawer."""

    model_config = ConfigDict(frozen=True)

    row: HabitationPriorityRow
    habitation: Habitation
    explanation: ValueExplanation
    priority_formula: FormulaSpec
    exposure_formula: FormulaSpec
    vulnerability_formula: FormulaSpec
    history_formula: FormulaSpec
    peers_above: list[str] = Field(
        description="Habitations ranked immediately above this one, for context."
    )
    peers_below: list[str]


class UsableAreaResponse(BaseModel):
    """Buildable ground at a site, and exactly how it was measured."""

    model_config = ConfigDict(frozen=True)

    usable_m2: float
    usable_ha: float
    measured_m2: float
    radius_m: float
    buildable_fraction: float
    slope_pass_fraction: float
    flood_pass_fraction: float
    footprint_mean_slope_deg: float
    footprint_mean_hand_m: float
    method: str
    confidence: ConfidenceBand
    refinement_agreement: float | None = None
    refinement_note: str | None = None


class InterventionResponse(BaseModel):
    """One unit of investment and the capacity it actually unlocks."""

    model_config = ConfigDict(frozen=True)

    service: ServiceType
    description: str
    unit_size: float
    unit: str
    capacity_before: float
    capacity_after: float
    capacity_gain: float
    next_bottleneck: ServiceType | None
    next_bottleneck_capacity: float | None
    unlocks: bool


class SiteCapacityResponse(BaseModel):
    """A candidate site's suitability, capacity, bottleneck and interventions."""

    model_config = ConfigDict(frozen=True)

    site_id: str
    name: str
    centroid: GeoPoint
    elevation_m: float
    distance_to_road_m: float
    recorded_parcel_area_m2: float

    suitable: bool
    gates: list[GateResult]
    failed_gates: list[str]

    usable_area: UsableAreaResponse
    services: list[ServiceCapacity]
    theoretical_capacity: float
    effective_capacity: float
    bottleneck: ServiceType | None
    interventions: list[InterventionResponse]
    marginal_headline: str | None

    pending_constraints: list[str]
    limitation: str


class SiteCapacityListResponse(BaseModel):
    """Every candidate site, with the district totals a planner needs first."""

    model_config = ConfigDict(frozen=True)

    sites: list[SiteCapacityResponse]
    suitable_sites: int
    total_effective_capacity: float
    total_theoretical_capacity: float
    population_needing_relocation: int
    unmet_demand: float
    bottleneck_counts: dict[str, int]
    norms: list[Constant]
    decision_authority: str
    limitation: str
    model_config_version: str
    engine_version: str

# ---------------------------------------------------------------------------
# Engine 5 - route reliability and survivability
# ---------------------------------------------------------------------------


class SegmentLegResponse(BaseModel):
    """One stretch of road as travelled, with its own contribution to the risk."""

    model_config = ConfigDict(frozen=True)

    segment_id: str
    name: str | None
    road_class: RoadClass
    length_m: float
    travel_time_min: float
    hazard_max: float
    hazard_coverage: float = 1.0
    is_bridge: bool
    p_fail: float


class PointOfFailureResponse(BaseModel):
    """A stretch of road whose loss on its own decides whether the journey happens."""

    model_config = ConfigDict(frozen=True)

    segment_id: str
    name: str | None
    reason: str
    p_fail: float
    length_m: float
    no_alternative: bool


class RouteResponse(BaseModel):
    """One evaluated journey under one routing objective."""

    model_config = ConfigDict(frozen=True)

    origin_id: str
    destination_id: str
    profile: RouteProfile
    travel_time_min: float
    distance_km: float
    reliability: float = Field(
        description="Product over segments of (1 - p_fail). A survivability figure, "
        "not a confidence in the estimate."
    )
    risk: float
    off_network_m: float
    off_network_min: float
    hazard_segment_count: int
    hazard_exposed_km: float
    longest_hazard_run_km: float
    bridges_crossed: int
    feasible: bool
    scored_share: float = Field(
        description="Share of this route, by length, that runs over ground ASTRA "
        "scored. Below one, part of the road leaves the study area."
    )
    infeasible_reason: str | None
    legs: list[SegmentLegResponse]
    points_of_failure: list[PointOfFailureResponse]
    geometry: list[list[float]] = Field(
        description="The route drawn end to end as [lon, lat] pairs."
    )
    provenance: ProvenanceClass


class RoutePairResponse(BaseModel):
    """Fastest and safest for one habitation-site pair, with the trade stated."""

    model_config = ConfigDict(frozen=True)

    origin_id: str
    destination_id: str
    fastest: RouteResponse
    safest: RouteResponse
    profiles_differ: bool
    minutes_paid: float
    reliability_gained: float
    tradeoff: str
    feasible: bool


class SiteAccessResponse(BaseModel):
    """What the road network means for one candidate site."""

    model_config = ConfigDict(frozen=True)

    site_id: str
    name: str
    reachable_habitations: int
    feasible_habitations: int
    usable_routes: int
    best_reliability: float
    median_travel_time_min: float
    access_capacity_persons: float
    population_with_feasible_route: int


class RouteMatrixRow(BaseModel):
    """One habitation's options, ranked by the reliability it can actually get."""

    model_config = ConfigDict(frozen=True)

    habitation_id: str
    habitation_name: str
    population: int
    site_id: str
    site_name: str
    travel_time_min: float
    distance_km: float
    reliability: float
    feasible: bool
    site_suitable: bool
    bridges_crossed: int
    hazard_exposed_km: float
    points_of_failure: int


class NetworkSummaryResponse(BaseModel):
    """The graph itself: what was built, and how much choice it offers."""

    model_config = ConfigDict(frozen=True)

    nodes: int
    segments: int
    bridge_segments: int
    total_length_km: float
    segments_by_class: dict[str, int]
    unrouted_ways: int
    unscored_segments: int = Field(
        description="Road segments dropped from the graph because they lie outside "
        "the scored hazard surface. Routing over them would assume an unscored road "
        "is a safe one."
    )
    independent_loops: int = Field(
        description="Cycle rank of the network: how many genuinely alternative ways "
        "through it exist. A tree has none."
    )
    segments_without_alternative: int
    share_without_alternative: float
    redundancy_note: str


class RouteAssessmentResponse(BaseModel):
    """Every habitation-to-site pair under one set of closures."""

    model_config = ConfigDict(frozen=True)

    network: NetworkSummaryResponse
    closed_segments: list[str]
    rows: list[RouteMatrixRow]
    access: list[SiteAccessResponse]
    pairs_evaluated: int
    feasible_pairs: int
    habitations_with_a_reachable_suitable_site: int
    route_blocked_habitations: list[str]
    reliability_threshold: float
    profiles_differ_count: int
    constants: list[Constant]
    decision_authority: str
    model_config_version: str
    engine_version: str


class ClosureRequest(BaseModel):
    """Ask what the corridor looks like with these road segments shut."""

    model_config = ConfigDict(frozen=True)

    closed_segments: list[str] = Field(
        default_factory=list,
        description="Segment identifiers to close, as returned on route legs.",
    )


class RouteDeltaRow(BaseModel):
    """How one pair changed between the open network and the closed one."""

    model_config = ConfigDict(frozen=True)

    habitation_id: str
    site_id: str
    reliability_before: float
    reliability_after: float
    travel_time_before_min: float
    travel_time_after_min: float
    feasible_before: bool
    feasible_after: bool
    became_unreachable: bool


class ClosureImpactResponse(BaseModel):
    """The before-and-after of a closure, as two real assessments compared."""

    model_config = ConfigDict(frozen=True)

    closed_segments: list[str]
    closed_segment_detail: list[SegmentLegResponse]
    assessment: RouteAssessmentResponse
    changed: list[RouteDeltaRow]
    newly_infeasible: int
    newly_unreachable: int
    population_losing_a_reachable_site: int
    headline: str

# ---------------------------------------------------------------------------
# Engine 6 - constrained relocation optimisation
# ---------------------------------------------------------------------------


class LivelihoodResponse(BaseModel):
    """Livelihood disruption for one pairing, with its four measured components."""

    model_config = ConfigDict(frozen=True)

    value: float
    percent: float
    commute_min: float
    commute_reliability: float
    worst_road_class: RoadClass
    market_access_min: float
    reachable: bool
    factors: list[FactorContribution]


class AssignmentResponse(BaseModel):
    """One movement in the plan, with everything that justifies it."""

    model_config = ConfigDict(frozen=True)

    habitation_id: str
    habitation_name: str
    site_id: str
    site_name: str
    phase: PhaseTier
    people: int
    households_equivalent: int
    travel_time_min: float
    distance_km: float
    route_reliability: float
    route_risk: float
    livelihood_disruption: float
    livelihood: LivelihoodResponse
    objective_contribution: float
    origin: GeoPoint
    destination: GeoPoint
    route_geometry: list[list[float]]


class RejectedOptionResponse(BaseModel):
    """A pairing the solver was never offered, and the constraint that removed it."""

    model_config = ConfigDict(frozen=True)

    habitation_id: str
    site_id: str
    reason: str
    detail: str


class UnmetReasonResponse(BaseModel):
    """Why these residents were not moved."""

    model_config = ConfigDict(frozen=True)

    habitation_id: str
    habitation_name: str
    people: int
    reason: str
    detail: str


class StrandedCapacityResponse(BaseModel):
    """Assessed capacity the people who still need it cannot reach."""

    model_config = ConfigDict(frozen=True)

    site_id: str
    site_name: str
    capacity: int
    unused: int
    reachable_unmet_people: int
    stranded_places: int
    detail: str


class SiteLoadResponse(BaseModel):
    """What the plan does to one site's capacity."""

    model_config = ConfigDict(frozen=True)

    site_id: str
    site_name: str
    effective_capacity: int
    soft_capacity: int
    assigned: int
    remaining: int
    utilisation: float
    over_soft_capacity: int
    phase_ceilings: dict[str, int]


class PhasePlanTotals(BaseModel):
    """One phase of the plan, as an SDMA would read it."""

    model_config = ConfigDict(frozen=True)

    phase: PhaseTier
    people_moved: int
    habitations: int
    sites_used: int
    mean_travel_time_min: float
    mean_route_reliability: float
    travel_ceiling_min: float
    capacity_share: float


class PlanTotalsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    population_assessed: int
    population_assigned: int
    population_unmet: int
    person_minutes: float
    mean_travel_time_min: float
    mean_route_reliability: float
    mean_livelihood_disruption: float
    sites_used: int
    habitations_split: int


class PlanResponse(BaseModel):
    """The optimised relocation plan, with everything that justifies it."""

    model_config = ConfigDict(frozen=True)

    status: SolverStatus
    solver: str
    solve_ms: float
    objective_value: float
    objective_terms: dict[str, float]
    assignments: list[AssignmentResponse]
    phases: list[PhasePlanTotals]
    site_load: list[SiteLoadResponse]
    totals: PlanTotalsResponse
    unmet: list[UnmetReasonResponse]
    stranded_capacity: list[StrandedCapacityResponse]
    capacity_blocked: list[str]
    rejected: list[RejectedOptionResponse]
    options_offered: int
    notes: list[str]
    weights: list[Constant]
    constraints: list[Constant]
    headline: str
    decision_authority: str
    model_config_version: str
    engine_version: str


class OptimiseRequest(BaseModel):
    """Ask for a plan, optionally under road closures."""

    model_config = ConfigDict(frozen=True)

    closed_segments: list[str] = Field(
        default_factory=list,
        description="Road segments to treat as closed while planning.",
    )
    use_fallback: bool = Field(
        default=False,
        description="Run the deterministic greedy fallback instead of the solver. "
        "For demonstrating the difference between the two, not for planning.",
    )


class CounterfactualResponse(BaseModel):
    """Why not that site: the answer from an actual re-solve, not from prose."""

    model_config = ConfigDict(frozen=True)

    habitation_id: str
    site_id: str
    people: int
    feasible: bool
    reason: str
    objective_baseline: float
    objective_forced: float | None
    objective_delta: float | None
    assigned_elsewhere_before: int
    assigned_elsewhere_after: int
    displaced: list[list[str]]
    headline: str

# ---------------------------------------------------------------------------
# Engine 7 - scenarios and what-if
# ---------------------------------------------------------------------------


class PerturbationResponse(BaseModel):
    """One change a scenario makes, with the line an official reads."""

    model_config = ConfigDict(frozen=True)

    kind: PerturbationKind
    target: str | None
    value: float
    note: str | None
    description: str


class SimulateRequest(BaseModel):
    """Ask what the corridor looks like under these changes."""

    model_config = ConfigDict(frozen=True)

    changes: list[Perturbation] = Field(
        default_factory=list, description="Perturbations to apply to the baseline."
    )
    name: str | None = None
    description: str | None = None


class ZoneDeltaResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    zone_class: str
    area_km2_before: float
    area_km2_after: float
    area_km2_delta: float
    population_before: int
    population_after: int


class HabitationDeltaResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    habitation_id: str
    name: str
    population: int
    priority_before: float
    priority_after: float
    priority_delta: float
    phase_before: PhaseTier
    phase_after: PhaseTier
    phase_changed: bool
    rank_before: int
    rank_after: int
    hazard_before: float
    hazard_after: float


class SiteDeltaResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    site_id: str
    name: str
    suitable_before: bool
    suitable_after: bool
    effective_before: float
    effective_after: float
    effective_delta: float
    bottleneck_before: str | None
    bottleneck_after: str | None
    withdrawn: bool
    failed_gates_before: list[str] = []
    failed_gates_after: list[str] = []


class RouteDeltaResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    habitation_id: str
    site_id: str
    reliability_before: float
    reliability_after: float
    travel_before_min: float
    travel_after_min: float
    feasible_before: bool
    feasible_after: bool


class AssignmentDeltaResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    habitation_id: str
    site_id: str
    phase: PhaseTier
    people_before: int
    people_after: int
    people_delta: int


class ScenarioDiffResponse(BaseModel):
    """Before and after as two complete assessments, compared field by field."""

    model_config = ConfigDict(frozen=True)

    scenario: Scenario
    changes: list[PerturbationResponse]
    headline: str

    zones: list[ZoneDeltaResponse]
    habitations: list[HabitationDeltaResponse]
    sites: list[SiteDeltaResponse]
    routes: list[RouteDeltaResponse]
    assignments: list[AssignmentDeltaResponse]
    tier_changes: list[HabitationDeltaResponse]

    newly_immediate_population: int
    placed_before: int
    placed_after: int
    unmet_before: int
    unmet_after: int
    effective_capacity_before: float
    effective_capacity_after: float
    feasible_routes_before: int
    feasible_routes_after: int
    critical_area_km2_before: float
    critical_area_km2_after: float

    plan_after: PlanResponse
    zones_after: ZonesResponse
    decision_id: str | None = Field(
        default=None,
        description="The audit ledger row this simulation wrote, if one was written.",
    )
    elapsed_ms: float
    stage_ms: dict[str, float]
    decision_authority: str
    model_config_version: str
    engine_version: str


class PlanDependencyResponse(BaseModel):
    """One stretch of road the current plan depends on.

    Ranked by the number of residents whose planned movement crosses it, which
    is what makes closing it a question worth asking rather than an arbitrary
    perturbation of the network.
    """

    model_config = ConfigDict(frozen=True)

    segment_id: str
    name: str | None
    road_class: RoadClass
    length_m: float
    is_bridge: bool
    p_fail: float
    no_alternative: bool
    #: Residents whose assigned movement in the baseline plan crosses this road.
    people_dependent: int
    #: Habitation-to-site movements in the baseline plan that cross it.
    movements: int
    consequence: str


class PlanDependencyListResponse(BaseModel):
    """The roads the baseline plan is standing on."""

    model_config = ConfigDict(frozen=True)

    segments: list[PlanDependencyResponse]
    plan_people: int
    segments_carrying_the_plan: int
    note: str


# ---------------------------------------------------------------------------
# Engine 8 - live event ingest and the execution pipeline
# ---------------------------------------------------------------------------


class EventSubmission(BaseModel):
    """One observation offered to POST /events."""

    kind: EventType
    value: float
    lon: float | None = None
    lat: float | None = None
    radius_m: float = Field(default=1500.0, gt=0.0, le=25000.0)
    target: str | None = None
    source: str = Field(min_length=1, max_length=200)
    observed_at: datetime | None = None
    note: str | None = Field(default=None, max_length=500)


class EventBatch(BaseModel):
    """A batch of observations. One batch triggers exactly one pipeline run."""

    events: list[EventSubmission] = Field(min_length=1, max_length=20)
    trigger: str = Field(
        default="manual",
        max_length=60,
        description="Where the batch came from. Shown on the run record.",
    )


class EventResponse(BaseModel):
    """An ingested observation, as ASTRA recorded it."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: EventType
    observed_at: datetime
    received_at: datetime
    lon: float | None
    lat: float | None
    radius_m: float
    value: float
    target: str | None
    source: str
    provenance: ProvenanceClass
    note: str | None
    description: str


class StageEventResponse(BaseModel):
    """One stage event, exactly as it went out over the SSE stream."""

    model_config = ConfigDict(frozen=True)

    sequence: int
    run_id: str
    stage: RunStage
    status: RunStageStatus
    event: str = Field(description="The SSE event name this was published under.")
    at: datetime
    message: str
    elapsed_ms: float
    payload: dict[str, Any]


class PlanReviewResponse(BaseModel):
    """Which standing decisions the newest evidence has undermined."""

    model_config = ConfigDict(frozen=True)

    required: bool
    headline: str
    reasons: list[str]
    invalidated_movements: list[dict[str, Any]]
    people_affected: int
    decision_authority: str


class RunResponse(BaseModel):
    """One pipeline execution: what triggered it, what it emitted, what it found."""

    model_config = ConfigDict(frozen=True)

    id: str
    trigger: str
    status: RunStatus
    created_at: datetime
    finished_at: datetime | None
    total_ms: float
    events: list[EventResponse]
    stages: list[StageEventResponse]
    stage_order: list[RunStage]
    stage_labels: dict[str, str]
    error: str | None
    review: PlanReviewResponse | None
    cells_rescored: int | None
    cells_in_grid: int | None
    decision_id: str | None = Field(
        default=None,
        description="The audit ledger row this run wrote. Cited on a printed brief.",
    )
    engine_version: str
    model_config_version: str


class RunSummary(BaseModel):
    """A run in the history list."""

    model_config = ConfigDict(frozen=True)

    id: str
    trigger: str
    status: RunStatus
    created_at: datetime
    total_ms: float
    events: int
    plan_requires_review: bool


class RunListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    runs: list[RunSummary]
    events_ingested: int
    closed_segments: list[str]


class LiveStateResponse(BaseModel):
    """The standing live picture: baseline plus every event ingested so far."""

    model_config = ConfigDict(frozen=True)

    live: bool = Field(
        description=(
            "False when nothing has been ingested. ASTRA shows the baseline and "
            "says so rather than manufacturing a live state before anything has "
            "happened."
        )
    )
    run_id: str | None
    events_ingested: int
    events: list[EventResponse]
    closed_segments: list[str]

    zones_baseline: int
    zones_now: int
    critical_area_km2_baseline: float
    critical_area_km2_now: float
    immediate_population_baseline: int
    immediate_population_now: int
    suitable_sites_baseline: int
    suitable_sites_now: int
    feasible_routes_baseline: int
    feasible_routes_now: int
    placed_baseline: int
    placed_now: int

    cells_rescored: int
    cells_in_grid: int
    share_rescored: float
    rescore_note: str

    review: PlanReviewResponse | None
    headline: str
    classification_label: str
    decision_authority: str
    engine_version: str
    model_config_version: str


class FeedStepResponse(BaseModel):
    """One step of the demonstration feed, ready to be posted as a real event."""

    model_config = ConfigDict(frozen=True)

    index: int
    delay_ms: int
    kind: EventType
    lon: float | None
    lat: float | None
    radius_m: float
    value: float
    target: str | None
    source: str
    note: str


class EventFeedResponse(BaseModel):
    """A scripted observation sequence a client replays against POST /events.

    The sequence is data, and it is labelled ``DEMO_CONFIG``. Replaying it posts
    each step as a real event and each event triggers a real pipeline run; no
    result here is pre-computed and no stage is animated on a timer.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    description: str
    note: str
    provenance: ProvenanceClass
    steps: list[FeedStepResponse]


# ---------------------------------------------------------------------------
# Section 7 - back-test, sensitivity and confidence
# ---------------------------------------------------------------------------


class SuccessRatePointResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    area_share: float
    incident_share: float


class BacktestVariantResponse(BaseModel):
    """One way of scoring the hazard model against the recorded inventory."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    independent: bool = Field(
        description=(
            "False when the points being predicted also fed the model. Such a figure "
            "reads high and is reported for comparison, never as evidence."
        )
    )
    auc: float
    auc_ci_low: float
    auc_ci_high: float
    positives: int
    background: int
    success_curve: list[SuccessRatePointResponse]
    area_under_success_curve: float
    top_10pct_capture: float
    top_20pct_capture: float
    note: str


class HazardAucResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    hazard: str
    auc: float
    positives: int


class BacktestResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    variants: list[BacktestVariantResponse]
    per_hazard: list[HazardAucResponse]
    headline: str
    limitation: str
    incidents_in_study_area: int
    incidents_total: int
    exclusion_radius_m: float
    folds: int
    seed: int
    elapsed_ms: float


class HabitationStabilityResponse(BaseModel):
    """How far one habitation's rank moves when the weights are perturbed."""

    model_config = ConfigDict(frozen=True)

    habitation_id: str
    name: str
    baseline_rank: int
    baseline_priority: float
    median_rank: float
    best_rank: int
    worst_rank: int
    rank_spread: int
    priority_p05: float
    priority_p95: float
    stable: bool
    note: str


class SensitivityResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    runs: int
    perturbation: float
    top_k: int
    spearman_mean: float
    spearman_median: float
    spearman_p05: float
    spearman_min: float
    top_k_unchanged_share: float
    top_k_baseline: list[str]
    habitations: list[HabitationStabilityResponse]
    weights_perturbed: int
    headline: str
    method: str
    seed: int
    elapsed_ms: float


class ConfidenceBandResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    band: str
    cells: int
    area_km2: float
    share: float
    zones: int
    habitations: int


class ConfidenceSurfaceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    mean: float
    median: float
    p10: float
    minimum: float
    maximum: float
    bands: list[ConfidenceBandResponse]
    low_confidence_habitations: list[str]
    note: str


class ValidationResponse(BaseModel):
    """Everything section 7 asks for, as the last back-test run produced it."""

    model_config = ConfigDict(frozen=True)

    generated_at: str
    model_config_version: str
    engine_version: str
    backtest: BacktestResponse
    sensitivity: SensitivityResponse
    confidence: ConfidenceSurfaceResponse
    elapsed_ms: float
    notes: list[str]

    current_model_config_version: str
    current_engine_version: str
    stale: bool
    staleness_note: str
    decision_authority: str


# ---------------------------------------------------------------------------
# Section 6 and 10 - evidence, the decision ledger, narration and the ask layer
# ---------------------------------------------------------------------------


class EvidenceRecordResponse(BaseModel):
    """One filed field report, with what the classifier made of it."""

    model_config = ConfigDict(frozen=True)

    id: str
    received_at: str
    observed_at: str
    kind: str
    lon: float | None
    lat: float | None
    habitation_id: str | None
    site_id: str | None
    segment_id: str | None
    reporter: str
    role: str | None
    text: str
    analyst_note: str | None
    photo_path: str | None = Field(
        default=None,
        description="Server-side path. Not a URL; use photo_url to fetch it.",
        exclude=True,
    )
    photo_name: str | None
    photo_url: str | None
    severity: float | None
    confidence: str
    extraction_mode: str = Field(
        description="`rules` when the deterministic classifier decided, `model` "
        "when a configured language model refined it and agreed on the hazard."
    )
    extracted: dict[str, Any]
    provenance: ProvenanceClass
    ingested_event_id: str | None


class EvidenceListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence: list[EvidenceRecordResponse]
    total: int
    kinds: list[str]
    extraction_note: str
    decision_authority: str


class PromoteEvidenceRequest(BaseModel):
    """Turn a filed report into a live observation."""

    value: float | None = Field(
        default=None,
        description="Overrides the value the classifier suggested, if given.",
    )
    radius_m: float = Field(default=1500.0, gt=0.0, le=25000.0)


class OverrideResponse(BaseModel):
    """One thing a person did about a computed plan, and what it cost."""

    model_config = ConfigDict(frozen=True)

    id: str
    decision_id: str
    created_at: str
    actor: str
    action: str
    habitation_id: str | None
    site_id: str | None
    people: int | None
    reason: str
    consequence: dict[str, Any]


class OverrideRequest(BaseModel):
    """What a person decided, and why. The reason is not optional."""

    actor: str = Field(min_length=1, max_length=120)
    action: Literal["APPROVE", "FORCE_ASSIGNMENT", "REJECT_ASSIGNMENT", "ANNOTATE"]
    reason: str = Field(
        min_length=3,
        max_length=1000,
        description=(
            "Why the decision departs from, or accepts, the computed plan. "
            "Required: an unexplained departure is the one thing an audit trail "
            "cannot be built from."
        ),
    )
    habitation_id: str | None = None
    site_id: str | None = None
    people: int | None = Field(default=None, ge=1)


class RecordDecisionRequest(BaseModel):
    trigger: str = Field(default="manual", max_length=80)
    notes: str | None = Field(default=None, max_length=1000)


class DecisionResponse(BaseModel):
    """One ledger row: the computed state a decision could be taken on."""

    model_config = ConfigDict(frozen=True)

    id: str
    created_at: str
    scenario_id: str
    trigger: str
    run_id: str | None
    engine_version: str
    model_config_version: str
    source_layer_ids: list[str]
    input_summary_hash: str
    score_components: dict[str, Any]
    constraint_status: dict[str, Any]
    solver_status: str
    objective_value: float | None
    confidence: str
    state: str
    notes: str | None
    overrides: list[OverrideResponse]
    decision_authority: str


class DecisionListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    decisions: list[DecisionResponse]
    total: int
    engine_version: str
    model_config_version: str
    decision_authority: str


class NarrationResponse(BaseModel):
    """Prose for an official, and an honest account of where it came from."""

    model_config = ConfigDict(frozen=True)

    text: str
    mode: Literal["template", "model"]
    validated: bool
    note: str
    llm_configured: bool
    decision_authority: str


class IntentResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    question: str
    needs: str


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=400)
    intent_id: str | None = Field(
        default=None,
        description="Choose an intent from the catalogue directly, skipping matching.",
    )


class AskResponse(BaseModel):
    """A structured answer, plus the prose assembled from it."""

    model_config = ConfigDict(frozen=True)

    intent: str
    question: str
    matched_on: str
    entity: str | None
    data: dict[str, Any]
    text: str
    follow_up: list[str]
    decision_authority: str
    note: str


# ---------------------------------------------------------------------------
# The Decision Brief
# ---------------------------------------------------------------------------


class BriefPriorityRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    rank: int
    habitation_id: str
    name: str
    population: int
    priority_score: float
    phase: PhaseTier
    phase_reason: str
    rules_applied: list[str]
    zone_class: ZoneClass
    dominant_hazard: HazardType
    hazard_composite: float
    components: dict[str, float]
    confidence_band: ConfidenceBand


class BriefMovement(BaseModel):
    model_config = ConfigDict(frozen=True)

    habitation_id: str
    habitation_name: str
    site_id: str
    site_name: str
    phase: PhaseTier
    people: int
    travel_time_min: float
    route_reliability: float


class BriefPhaseAction(BaseModel):
    """One phase of the plan as an instruction, with the movements behind it."""

    model_config = ConfigDict(frozen=True)

    phase: PhaseTier
    tier_rule: str
    steps: list[str]
    people_moved: int
    habitations: int
    sites_used: int
    travel_ceiling_min: float | None
    movements: list[BriefMovement]


class BriefSite(BaseModel):
    model_config = ConfigDict(frozen=True)

    site_id: str
    name: str
    suitable: bool
    failed_gates: list[str]
    theoretical_capacity: float
    effective_capacity: float
    bottleneck: ServiceType | None
    marginal_headline: str | None
    assigned: int | None
    remaining: int | None


class BriefSituation(BaseModel):
    model_config = ConfigDict(frozen=True)

    headline: str
    zones: dict[str, ZoneClassSummary]
    zone_count: int
    critical_area_km2: float
    habitations_assessed: int
    population_assessed: int
    habitations_in_critical_or_elevated: int
    by_phase: dict[str, PhaseTotals]
    plan_requires_review: bool
    review_headline: str | None


class BriefCapacity(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_sites: int
    suitable_sites: int
    total_effective_capacity: float
    total_theoretical_capacity: float
    bottlenecks: list[ServiceType]
    sites: list[BriefSite]
    limitation: str


class BriefPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: SolverStatus
    solver: str
    objective_value: float
    headline: str
    totals: PlanTotalsResponse
    unmet: list[UnmetReasonResponse]
    capacity_blocked: list[str]


class BriefRoutes(BaseModel):
    model_config = ConfigDict(frozen=True)

    closed_segments: list[str]
    pairs_evaluated: int
    feasible_pairs: int
    reliability_threshold: float
    habitations_without_reachable_suitable_site: list[str]
    dependencies: list[PlanDependencyResponse]
    weakest_movements: list[BriefMovement]


class BriefValidation(BaseModel):
    model_config = ConfigDict(frozen=True)

    available: bool
    auc: float | None
    auc_ci_low: float | None
    auc_ci_high: float | None
    incidents: int | None
    runs: int | None
    spearman_median: float | None
    top_k: int | None
    top_k_unchanged_share: float | None
    backtest_headline: str | None
    sensitivity_headline: str | None
    limitation: str | None
    stale: bool


class BriefConfidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    modal_band: ConfidenceBand
    bands: dict[str, int]
    note: str


class BriefComparisonRow(BaseModel):
    """What a static hazard map answers, beside what ASTRA's added layers answer."""

    model_config = ConfigDict(frozen=True)

    question: str
    static_map: str
    astra: str


class BriefNarration(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    mode: str
    note: str


class BriefAudit(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str
    created_at: str
    input_summary_hash: str
    engine_version: str
    model_config_version: str
    solver_status: str
    objective_value: float | None
    state: str


class BriefResponse(BaseModel):
    """The Decision Brief. ``id`` and ``audit`` are null on a preview."""

    model_config = ConfigDict(frozen=True)

    id: str | None
    generated_at: str
    basis: Literal["BASELINE", "LIVE"]
    basis_note: str
    run_id: str | None
    events_ingested: int
    scenario_id: str
    scenario_name: str
    situation: BriefSituation
    priorities: list[BriefPriorityRow]
    capacity: BriefCapacity
    plan: BriefPlan
    actions: list[BriefPhaseAction]
    routes: BriefRoutes
    validation: BriefValidation
    confidence: BriefConfidence
    comparison: list[BriefComparisonRow]
    narration: BriefNarration
    assumptions: list[Constant]
    limitations: list[str]
    audit: BriefAudit | None
    how_this_works: str
    decision_authority: str
    classification_label: str
    scenario_disclaimer: str
    priority_note: str
    engine_version: str
    model_config_version: str


class BriefSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    decision_id: str
    created_at: str
    basis: str
    headline: str


class BriefListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    briefs: list[BriefSummary]
    total: int
    decision_authority: str


class GenerateBriefRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=1000)
