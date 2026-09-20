/**
 * The only place the frontend talks to the API.
 *
 * Every response type comes from `@astra/contracts`, which is generated from the
 * FastAPI OpenAPI schema. Nothing here reshapes, recomputes or rounds a value:
 * the interface renders what the engines produced (CLAUDE.md section 2.4).
 */

import type {
  AskResponse,
  BriefListResponse,
  BriefResponse,
  ClosureImpactResponse,
  DecisionListResponse,
  DecisionResponse,
  EvidenceListResponse,
  EvidenceRecordResponse,
  IntentResponse,
  NarrationResponse,
  OverrideRequest,
  EventFeedResponse,
  EventSubmission,
  LiveStateResponse,
  RunListResponse,
  RunResponse,
  CounterfactualResponse,
  HabitationDetailResponse,
  HabitationHazardResponse,
  HabitationPriorityResponse,
  HabitationsResponse,
  HealthStatus,
  LayersResponse,
  ModelConfigResponse,
  PlanDependencyListResponse,
  PlanResponse,
  Perturbation,
  ProvenanceResponse,
  RiskCellResponse,
  RiskSummaryResponse,
  RouteAssessmentResponse,
  RoutePairResponse,
  ScenarioDiffResponse,
  ScenarioListResponse,
  SiteCapacityListResponse,
  SitesResponse,
  StudyAreaDataResponse,
  ValidationCheckResponse,
  ValidationResponse,
  ZonesResponse,
} from "@astra/contracts";

// The default is the IPv4 loopback rather than "localhost". The API binds IPv4;
// on Windows "localhost" resolves to ::1 first, and each refused IPv6 attempt
// costs ~200 ms before the fallback - on every server-side fetch of every page.
export const API_BASE =
  process.env.NEXT_PUBLIC_ASTRA_API_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8000";

export class ApiUnavailableError extends Error {
  constructor(
    readonly path: string,
    readonly cause_: unknown,
  ) {
    super(`ASTRA API did not respond at ${path}`);
    this.name = "ApiUnavailableError";
  }
}

/**
 * The API answered, and refused. Its reason is carried verbatim: a refusal that
 * names the perturbation it would not apply is more useful to the person who
 * built the scenario than a generic failure, and inventing a friendlier reason
 * here would be inventing a fact about the engine.
 */
export class ApiRefusedError extends Error {
  constructor(
    readonly path: string,
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
    this.name = "ApiRefusedError";
  }
}

/** Pull the API's own explanation out of an error response, if it gave one. */
async function refusal(path: string, response: Response): Promise<Error> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail) {
      return new ApiRefusedError(path, response.status, body.detail);
    }
  } catch {
    // No JSON body: fall through to the transport-level error.
  }
  return new ApiUnavailableError(path, `HTTP ${response.status}`);
}

async function get<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      headers: { accept: "application/json" },
    });
  } catch (error) {
    throw new ApiUnavailableError(path, error);
  }
  if (!response.ok) {
    throw new ApiUnavailableError(path, `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => get<HealthStatus>("/health"),
  modelConfig: () => get<ModelConfigResponse>("/model/config"),
  provenance: () => get<ProvenanceResponse>("/provenance"),
  layers: () => get<LayersResponse>("/layers"),
  scenarios: () => get<ScenarioListResponse>("/scenarios"),
  fixtureValidation: () => get<ValidationCheckResponse>("/validation/fixtures"),
  validation: () => get<ValidationResponse>("/validation"),
  evidence: () => get<EvidenceListResponse>("/evidence"),
  decisions: () => get<DecisionListResponse>("/decisions"),
  decision: (id: string) => get<DecisionResponse>(`/decisions/${encodeURIComponent(id)}`),
  askIntents: () => get<IntentResponse[]>("/ask/intents"),
  narratePlan: () => get<NarrationResponse>("/narrate/plan"),
  narrateHabitation: (id: string) =>
    get<NarrationResponse>(`/narrate/habitation/${encodeURIComponent(id)}`),
  narrateSite: (id: string) =>
    get<NarrationResponse>(`/narrate/site/${encodeURIComponent(id)}`),
  briefPreview: () => get<BriefResponse>("/brief/preview"),
  brief: (id: string) => get<BriefResponse>(`/brief/${encodeURIComponent(id)}`),
  briefs: () => get<BriefListResponse>("/briefs"),
  habitations: () => get<HabitationsResponse>("/habitations"),
  sites: () => get<SitesResponse>("/sites"),
  studyAreaData: () => get<StudyAreaDataResponse>("/study-area/data"),
  riskSummary: () => get<RiskSummaryResponse>("/risk/summary"),
  riskZones: () => get<ZonesResponse>("/risk/zones"),
  riskHabitations: () => get<HabitationHazardResponse>("/risk/habitations"),
  capacitySites: () => get<SiteCapacityListResponse>("/capacity/sites"),
  priorityHabitations: () => get<HabitationPriorityResponse>("/priority/habitations"),
  priorityHabitation: (id: string) =>
    get<HabitationDetailResponse>(`/priority/habitations/${encodeURIComponent(id)}`),
  riskCell: (lon: number, lat: number) =>
    get<RiskCellResponse>(`/risk/cell?lon=${lon.toFixed(6)}&lat=${lat.toFixed(6)}`),
  routes: () => get<RouteAssessmentResponse>("/routes"),
  plan: () => get<PlanResponse>("/plan"),
  planDependencies: () =>
    get<PlanDependencyListResponse>("/routes/critical-segments"),
  live: () => get<LiveStateResponse>("/live"),
  liveZones: () => get<ZonesResponse>("/live/zones"),
  livePlan: () => get<PlanResponse>("/live/plan"),
  eventFeed: () => get<EventFeedResponse>("/events/feed"),
  runs: () => get<RunListResponse>("/runs"),
  run: (id: string) => get<RunResponse>(`/runs/${encodeURIComponent(id)}`),
  whyNot: (habitationId: string, siteId: string) =>
    get<CounterfactualResponse>(
      `/plan/why-not/${encodeURIComponent(habitationId)}/${encodeURIComponent(siteId)}`,
    ),
  routePair: (habitationId: string, siteId: string) =>
    get<RoutePairResponse>(
      `/routes/pair/${encodeURIComponent(habitationId)}/${encodeURIComponent(siteId)}`,
    ),
};

/**
 * Close roads and get back a complete second assessment beside the baseline.
 * The only mutating call in the interface, and it mutates nothing on the server:
 * the closure is an argument, not a state change.
 */
/**
 * Re-solve the plan, optionally under closures or with the greedy fallback.
 * Like the closure evaluator, this changes nothing on the server: the baseline
 * plan stays where it is so the two can be compared.
 */
export async function optimisePlan(body: {
  closed_segments?: string[];
  use_fallback?: boolean;
}): Promise<PlanResponse> {
  return post<PlanResponse>("/plan/optimize", body);
}

/**
 * Run the whole chain under a set of perturbations and get back a structured
 * diff against the baseline. The baseline is not replaced.
 */
export async function simulate(
  changes: Perturbation[],
  name?: string,
): Promise<ScenarioDiffResponse> {
  return post<ScenarioDiffResponse>("/simulate", { changes, name });
}

/**
 * Post observations. The API answers as soon as the run is registered; the run
 * itself continues on the server and is followed over SSE.
 */
export async function ingestEvents(
  events: EventSubmission[],
  trigger = "manual",
): Promise<RunResponse> {
  return post<RunResponse>("/events", { events, trigger });
}

/** Discard every ingested observation and return to the baseline. */
export async function resetLive(): Promise<LiveStateResponse> {
  return post<LiveStateResponse>("/live/reset", {});
}

/** The SSE endpoint for one run. Consumed with EventSource, not fetch. */
export function runStreamUrl(runId: string): string {
  return `${API_BASE}/runs/${encodeURIComponent(runId)}/stream`;
}

/**
 * File one field report, with an optional photograph. Multipart, because a
 * photograph is part of the evidence rather than an attachment to it.
 */
export async function fileEvidence(form: FormData): Promise<EvidenceRecordResponse> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/evidence`, {
      method: "POST",
      cache: "no-store",
      body: form,
    });
  } catch (error) {
    throw new ApiUnavailableError("/evidence", error);
  }
  if (!response.ok) throw await refusal("/evidence", response);
  return (await response.json()) as EvidenceRecordResponse;
}

/** Turn a filed report into a live observation and run the pipeline on it. */
export async function promoteEvidence(
  evidenceId: string,
  body: { value?: number | null; radius_m?: number } = {},
): Promise<{ evidence_id: string; event_id: string; run_id: string }> {
  return post(`/evidence/${encodeURIComponent(evidenceId)}/promote`, body);
}

/** Write the current computed plan into the ledger as a decision point. */
export async function recordDecision(body: {
  trigger?: string;
  notes?: string | null;
}): Promise<DecisionResponse> {
  return post<DecisionResponse>("/decisions", body);
}

/** Record what a person decided, with the computed consequence beside it. */
export async function overrideDecision(
  decisionId: string,
  body: OverrideRequest,
): Promise<DecisionResponse> {
  return post<DecisionResponse>(
    `/decisions/${encodeURIComponent(decisionId)}/override`,
    body,
  );
}

/**
 * Generate a Decision Brief. Writes a decision-ledger row and freezes the brief
 * under an id the printed page carries.
 */
export async function generateBrief(notes?: string | null): Promise<BriefResponse> {
  return post<BriefResponse>("/brief", { notes: notes ?? null });
}

/** Ask one question from the fixed allowlist. */
export async function askAstra(body: {
  question: string;
  intent_id?: string | null;
}): Promise<AskResponse> {
  return post<AskResponse>("/ask", body);
}

export async function evaluateClosure(
  closedSegments: string[],
): Promise<ClosureImpactResponse> {
  return post<ClosureImpactResponse>("/routes/evaluate", {
    closed_segments: closedSegments,
  });
}

async function post<T>(path: string, body: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      cache: "no-store",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(body),
    });
  } catch (error) {
    throw new ApiUnavailableError(path, error);
  }
  if (!response.ok) {
    throw await refusal(path, response);
  }
  return (await response.json()) as T;
}

/** Served by the API so the map works with the network unplugged. */
export const HAZARD_OVERLAY_URL = `${API_BASE}/risk/overlay/composite.png`;

/** The evidence-confidence surface, hatched. Drawn over the hazard layer. */
export const CONFIDENCE_OVERLAY_URL = `${API_BASE}/risk/overlay/confidence.png`;
export const ROADS_GEOJSON_URL = `${API_BASE}/layers/roads.geojson`;

/** The routed graph, with each segment's computed failure probability. */
export const ROUTE_NETWORK_URL = `${API_BASE}/routes/network.geojson`;

/** The API serves the terrain render; the browser fetches it straight from there. */
export const TERRAIN_PREVIEW_URL = `${API_BASE}/study-area/terrain.jpg`;

/**
 * Fetch without letting one dead endpoint blank the whole screen. A failure is
 * reported as a failure - the interface never substitutes a plausible number.
 */
export async function tryFetch<T>(loader: () => Promise<T>): Promise<T | null> {
  try {
    return await loader();
  } catch {
    return null;
  }
}
