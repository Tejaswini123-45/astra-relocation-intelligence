/**
 * The single source of numeric truth, on the TypeScript side.
 *
 * `api.ts` is generated from the FastAPI OpenAPI schema by
 * `npm run contracts` and is never hand-edited. The aliases below are the only
 * thing this package adds: readable names for the generated component schemas,
 * so application code imports `HealthStatus` rather than digging through
 * `components["schemas"][...]`.
 *
 * There are deliberately no hand-written interfaces here. If the backend
 * changes a field, the frontend fails to compile - which is the point
 * (CLAUDE.md section 2.4).
 */

import type { components, paths } from "./api";

export type { components, paths };

type Schemas = components["schemas"];

export type HealthStatus = Schemas["HealthStatus"];
export type ModelConfigResponse = Schemas["ModelConfigResponse"];
export type ProvenanceResponse = Schemas["ProvenanceResponse"];
export type LayersResponse = Schemas["LayersResponse"];
export type ScenarioListResponse = Schemas["ScenarioListResponse"];
export type ScenarioResponse = Schemas["ScenarioResponse"];
export type ValidationCheckResponse = Schemas["ValidationCheckResponse"];
export type HabitationsResponse = Schemas["HabitationsResponse"];
export type SitesResponse = Schemas["SitesResponse"];
export type StudyAreaDataResponse = Schemas["StudyAreaDataResponse"];
export type RiskSummaryResponse = Schemas["RiskSummaryResponse"];
export type RiskCellResponse = Schemas["RiskCellResponse"];
export type ZonesResponse = Schemas["ZonesResponse"];
export type HabitationHazardResponse = Schemas["HabitationHazardResponse"];
export type HabitationPriorityResponse = Schemas["HabitationPriorityResponse"];
export type HabitationDetailResponse = Schemas["HabitationDetailResponse"];
export type SiteCapacityListResponse = Schemas["SiteCapacityListResponse"];
export type SiteCapacityResponse = Schemas["SiteCapacityResponse"];
export type RouteAssessmentResponse = Schemas["RouteAssessmentResponse"];
export type RoutePairResponse = Schemas["RoutePairResponse"];
export type ClosureImpactResponse = Schemas["ClosureImpactResponse"];
export type PlanResponse = Schemas["PlanResponse"];
export type CounterfactualResponse = Schemas["CounterfactualResponse"];
export type ScenarioDiffResponse = Schemas["ScenarioDiffResponse"];
export type PlanDependencyListResponse = Schemas["PlanDependencyListResponse"];
export type PlanDependencyResponse = Schemas["PlanDependencyResponse"];
export type LiveStateResponse = Schemas["LiveStateResponse"];
export type RunResponse = Schemas["RunResponse"];
export type RunListResponse = Schemas["RunListResponse"];
export type RunSummary = Schemas["RunSummary"];
export type StageEventResponse = Schemas["StageEventResponse"];
export type PlanReviewResponse = Schemas["PlanReviewResponse"];
export type EventResponse = Schemas["EventResponse"];
export type EventFeedResponse = Schemas["EventFeedResponse"];
export type FeedStepResponse = Schemas["FeedStepResponse"];
export type EventSubmission = Schemas["EventSubmission"];
export type EventType = Schemas["EventType"];
export type RunStage = Schemas["RunStage"];
export type RunStageStatus = Schemas["RunStageStatus"];
export type RunStatus = Schemas["RunStatus"];
export type ValidationResponse = Schemas["ValidationResponse"];
export type BacktestResponse = Schemas["BacktestResponse"];
export type BacktestVariantResponse = Schemas["BacktestVariantResponse"];
export type SensitivityResponse = Schemas["SensitivityResponse"];
export type HabitationStabilityResponse = Schemas["HabitationStabilityResponse"];
export type ConfidenceSurfaceResponse = Schemas["ConfidenceSurfaceResponse"];
export type EvidenceRecordResponse = Schemas["EvidenceRecordResponse"];
export type EvidenceListResponse = Schemas["EvidenceListResponse"];
export type DecisionResponse = Schemas["DecisionResponse"];
export type DecisionListResponse = Schemas["DecisionListResponse"];
export type OverrideResponse = Schemas["OverrideResponse"];
export type OverrideRequest = Schemas["OverrideRequest"];
export type NarrationResponse = Schemas["NarrationResponse"];
export type AskResponse = Schemas["AskResponse"];
export type IntentResponse = Schemas["IntentResponse"];
export type BriefResponse = Schemas["BriefResponse"];
export type BriefListResponse = Schemas["BriefListResponse"];
export type BriefSummary = Schemas["BriefSummary"];
export type BriefPhaseAction = Schemas["BriefPhaseAction"];
export type BriefMovement = Schemas["BriefMovement"];
export type BriefPriorityRow = Schemas["BriefPriorityRow"];
export type BriefSite = Schemas["BriefSite"];
export type BriefComparisonRow = Schemas["BriefComparisonRow"];

export type AstraModelConfig = Schemas["AstraModelConfig"];
export type Constant = Schemas["Constant"];
export type FormulaSpec = Schemas["FormulaSpec"];
export type Notices = Schemas["Notices"];
export type DatasetRecord = Schemas["DatasetRecord"];
export type LayerDescriptor = Schemas["LayerDescriptor"];
export type Scenario = Schemas["Scenario"];
export type StudyArea = Schemas["StudyArea"];
export type BBox = Schemas["BBox"];
export type Habitation = Schemas["Habitation"];
export type CandidateSite = Schemas["CandidateSite"];
export type DerivedLayerSummary = Schemas["DerivedLayerSummary"];
export type ServiceSupply = Schemas["ServiceSupply"];
export type ZoneFeature = Schemas["ZoneFeature"];
export type ZoneFeatureProperties = Schemas["ZoneFeatureProperties"];
export type CompositeHazard = Schemas["CompositeHazard"];
export type HazardScore = Schemas["HazardScore"];
export type FactorContribution = Schemas["FactorContribution"];
export type ConfidenceReport = Schemas["ConfidenceReport"];
export type HabitationHazardRow = Schemas["HabitationHazardRow"];
export type HabitationPriorityRow = Schemas["HabitationPriorityRow"];
export type ComponentScoreResponse = Schemas["ComponentScoreResponse"];
export type PhaseDecision = Schemas["PhaseDecision"];
export type PhaseTier = Schemas["PhaseTier"];
export type ValueExplanation = Schemas["ValueExplanation"];
export type ServiceCapacity = Schemas["ServiceCapacity"];
export type GateResult = Schemas["GateResult"];
export type InterventionResponse = Schemas["InterventionResponse"];
export type UsableAreaResponse = Schemas["UsableAreaResponse"];
export type ServiceType = Schemas["ServiceType"];
export type RouteResponse = Schemas["RouteResponse"];
export type SegmentLegResponse = Schemas["SegmentLegResponse"];
export type PointOfFailureResponse = Schemas["PointOfFailureResponse"];
export type RouteMatrixRow = Schemas["RouteMatrixRow"];
export type RouteDeltaRow = Schemas["RouteDeltaRow"];
export type SiteAccessResponse = Schemas["SiteAccessResponse"];
export type NetworkSummaryResponse = Schemas["NetworkSummaryResponse"];
export type RouteProfile = Schemas["RouteProfile"];
export type RoadClass = Schemas["RoadClass"];
export type AssignmentResponse = Schemas["AssignmentResponse"];
export type LivelihoodResponse = Schemas["LivelihoodResponse"];
export type PhasePlanTotals = Schemas["PhasePlanTotals"];
export type SiteLoadResponse = Schemas["SiteLoadResponse"];
export type UnmetReasonResponse = Schemas["UnmetReasonResponse"];
export type StrandedCapacityResponse = Schemas["StrandedCapacityResponse"];
export type RejectedOptionResponse = Schemas["RejectedOptionResponse"];
export type SolverStatus = Schemas["SolverStatus"];
export type Perturbation = Schemas["Perturbation"];
export type PerturbationKind = Schemas["PerturbationKind"];
export type PerturbationResponse = Schemas["PerturbationResponse"];
export type ZoneDeltaResponse = Schemas["ZoneDeltaResponse"];
export type HabitationDeltaResponse = Schemas["HabitationDeltaResponse"];
export type SiteDeltaResponse = Schemas["SiteDeltaResponse"];
export type RouteDeltaResponse = Schemas["RouteDeltaResponse"];
export type AssignmentDeltaResponse = Schemas["AssignmentDeltaResponse"];
export type ZoneClass = Schemas["ZoneClass"];
export type HazardType = Schemas["HazardType"];
export type GeoPoint = Schemas["GeoPoint"];

export type ProvenanceClass = Schemas["ProvenanceClass"];
export type ConfidenceBand = Schemas["ConfidenceBand"];

/** Severity ordering, most severe first. Used wherever zones are listed. */
export const ZONE_CLASS_ORDER: ZoneClass[] = ["CRITICAL", "ELEVATED", "WATCH", "LOW"];

/** Palette-independent ordering used wherever provenance classes are listed. */
export const PROVENANCE_ORDER: ProvenanceClass[] = [
  "REAL_OPEN",
  "DERIVED",
  "SYNTHETIC_CALIBRATED",
  "DEMO_CONFIG",
];

/** Short labels for the provenance chips. Long text comes from the API. */
export const PROVENANCE_LABEL: Record<ProvenanceClass, string> = {
  REAL_OPEN: "Real open data",
  DERIVED: "Derived by ASTRA",
  SYNTHETIC_CALIBRATED: "Synthetic, calibrated",
  DEMO_CONFIG: "ASTRA demo constant",
};
