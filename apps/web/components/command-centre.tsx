"use client";

import type {
  BriefResponse,
  CandidateSite,
  EventFeedResponse,
  EventSubmission,
  FeedStepResponse,
  Habitation,
  LiveStateResponse,
  PlanResponse,
  RunResponse,
  StudyArea,
  ZoneFeature,
} from "@astra/contracts";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { GenerateBriefButton } from "@/components/brief-actions";
import { startDemo } from "@/components/demo-mode";
import { ExecutionGraph } from "@/components/execution-graph";
import {
  type DrawnRoute,
  type LayerToggles,
  type MapCircle,
  RiskMap,
} from "@/components/risk-map";
import {
  api,
  ApiRefusedError,
  HAZARD_OVERLAY_URL,
  ingestEvents,
  resetLive,
  ROADS_GEOJSON_URL,
  ROUTE_NETWORK_URL,
  TERRAIN_PREVIEW_URL,
} from "@/lib/api";

const PHASE_LABEL: Record<string, string> = {
  IMMEDIATE: "Immediate",
  SHORT_TERM: "Short-term",
  MEDIUM_TERM: "Medium-term",
  CAPACITY_BLOCKED: "Capacity blocked",
  NOT_PRIORITISED: "Not prioritised",
};

const PHASE_TOKEN: Record<string, string> = {
  IMMEDIATE: "var(--color-critical)",
  SHORT_TERM: "var(--color-severity-elevated)",
  MEDIUM_TERM: "var(--color-severity-watch)",
  CAPACITY_BLOCKED: "var(--color-prov-synthetic)",
  NOT_PRIORITISED: "var(--color-neutral)",
};

const PHASE_RGB: Record<string, [number, number, number, number]> = {
  IMMEDIATE: [201, 66, 56, 235],
  SHORT_TERM: [205, 117, 56, 225],
  MEDIUM_TERM: [185, 147, 64, 215],
  CAPACITY_BLOCKED: [155, 127, 212, 235],
  NOT_PRIORITISED: [110, 124, 145, 190],
};

const EVENT_TOKEN: Record<string, string> = {
  RAINFALL_OBSERVATION: "var(--color-signal)",
  INCIDENT_REPORT: "var(--color-critical)",
  FIELD_EVIDENCE: "var(--color-warning)",
  INFRASTRUCTURE_STATUS: "var(--color-neutral)",
};

const INTRO_KEY = "astra.coldOpenSeen";
const COLD_OPEN_MS = 6000;
const LAYER_STEP_MS = 650;
const NO_ROUTES: DrawnRoute[] = [];
const NO_CIRCLES: MapCircle[] = [];

/**
 * The layers the study area resolves through, in order. Each entry is a layer the
 * map genuinely draws at that step; the label says where its data came from.
 */
const LAYER_SEQUENCE = [
  { label: "Terrain", source: "Copernicus DEM GLO-30, real open data" },
  { label: "Roads and drainage", source: "OpenStreetMap, real open data" },
  { label: "Analytical zones", source: "Engine 1, derived by ASTRA" },
  { label: "Habitations", source: "Synthetic, terrain-calibrated" },
  { label: "Candidate sites", source: "Synthetic, terrain-calibrated" },
  { label: "Planned movements", source: "CP-SAT optimiser" },
];

function num(value: number, digits = 0): string {
  return value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * The Command Centre.
 *
 * What is being decided, for whom, and what happens next - on one screen, over
 * the map. Every figure here is a field of the Decision Brief the API builds from
 * the standing state of the engines; the escalation control posts the real
 * observation feed to the real ingest endpoint and the execution graph follows
 * the runs it starts.
 */
export function CommandCentre({
  initialBrief,
  studyArea,
  initialZones,
  habitations,
  sites,
  initialPlan,
  initialLive,
  feed,
}: {
  initialBrief: BriefResponse;
  studyArea: StudyArea;
  initialZones: ZoneFeature[];
  habitations: Habitation[];
  sites: CandidateSite[];
  initialPlan: PlanResponse;
  initialLive: LiveStateResponse;
  feed: EventFeedResponse | null;
}) {
  const [brief, setBrief] = useState(initialBrief);
  const [zones, setZones] = useState(initialZones);
  const [plan, setPlan] = useState(initialPlan);
  const [live, setLive] = useState(initialLive);
  const [run, setRun] = useState<RunResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<{ sent: number; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // -- cold open and layer resolution ------------------------------------
  const [intro, setIntro] = useState<"pending" | "cold" | "resolving" | "done">("pending");
  const [mapReady, setMapReady] = useState(false);
  const [stage, setStage] = useState(0);

  useEffect(() => {
    let seen = false;
    try {
      seen = window.sessionStorage.getItem(INTRO_KEY) === "1";
    } catch {
      // Storage can be unavailable; the cold open simply plays.
    }
    const flag = new URLSearchParams(window.location.search).get("intro");
    if (flag === "0") seen = true;
    if (flag === "1") seen = false;
    setIntro(seen ? "done" : "cold");
  }, []);

  const endColdOpen = useCallback(() => {
    try {
      window.sessionStorage.setItem(INTRO_KEY, "1");
    } catch {
      // Not remembering it only means it plays again next visit.
    }
    setIntro((current) => (current === "cold" ? "resolving" : current));
  }, []);

  useEffect(() => {
    if (intro !== "cold") return;
    const timer = window.setTimeout(endColdOpen, COLD_OPEN_MS);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" || event.key === "Enter") endColdOpen();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("keydown", onKey);
    };
  }, [intro, endColdOpen]);

  // Layers resolve only once the terrain has actually loaded, so the sequence is
  // the map drawing real layers rather than a video of one.
  useEffect(() => {
    if (!mapReady || intro === "pending" || intro === "cold") return;
    if (intro === "done") {
      setStage(LAYER_SEQUENCE.length);
      return;
    }
    let next = 1;
    setStage(next);
    const timer = window.setInterval(() => {
      next += 1;
      setStage(next);
      if (next >= LAYER_SEQUENCE.length) {
        window.clearInterval(timer);
        window.setTimeout(() => setIntro("done"), 900);
      }
    }, LAYER_STEP_MS);
    return () => window.clearInterval(timer);
  }, [mapReady, intro]);

  const onMapReady = useCallback(() => setMapReady(true), []);
  const ignorePoint = useCallback(() => undefined, []);

  // -- map layers --------------------------------------------------------
  const phaseById = useMemo(
    () => new Map(brief.priorities.map((row) => [row.habitation_id, row.phase])),
    [brief.priorities],
  );
  const habitationColour = useCallback(
    (habitation: Habitation) =>
      PHASE_RGB[phaseById.get(habitation.id) ?? "NOT_PRIORITISED"] ?? PHASE_RGB.NOT_PRIORITISED,
    [phaseById],
  );

  const drawnRoutes: DrawnRoute[] = useMemo(
    () =>
      plan.assignments
        .filter((assignment) => assignment.route_geometry.length > 1)
        .map((assignment) => {
          const [r, g, b] = PHASE_RGB[assignment.phase] ?? PHASE_RGB.NOT_PRIORITISED;
          return {
            id: `${assignment.habitation_id}-${assignment.site_id}-${assignment.phase}`,
            geometry: assignment.route_geometry,
            colour: [r, g, b, 150] as [number, number, number, number],
            width: 38,
          };
        }),
    [plan],
  );

  const footprints: MapCircle[] = useMemo(
    () =>
      live.events
        .filter((event) => event.lon !== null && event.lat !== null)
        .map((event) => ({
          id: event.id,
          lon: event.lon as number,
          lat: event.lat as number,
          radius_m: event.radius_m,
          colour: EVENT_TOKEN[event.kind] ?? "var(--color-signal)",
        })),
    [live.events],
  );

  const closures = live.closed_segments.length > 0;
  const toggles: LayerToggles = useMemo(
    () => ({
      hazard: false,
      roads: stage >= 2,
      network: stage >= 2 && closures,
      zones: stage >= 3,
      habitations: stage >= 4,
      sites: stage >= 5,
    }),
    [stage, closures],
  );

  // -- the live escalation -----------------------------------------------
  const generation = useRef(0);
  const escalation = useRef(0);
  useEffect(
    () => () => {
      // Leaving the screen must stop the feed, not leave a loop posting
      // observations into a page nobody is looking at.
      escalation.current += 1;
    },
    [],
  );

  const refresh = useCallback(async () => {
    const mine = generation.current;
    const [nextBrief, nextZones, nextPlan, nextLive] = await Promise.all([
      api.briefPreview(),
      api.liveZones(),
      api.livePlan(),
      api.live(),
    ]);
    if (generation.current !== mine) return;
    setBrief(nextBrief);
    setZones(nextZones.features);
    setPlan(nextPlan);
    setLive(nextLive);
  }, []);

  const escalate = useCallback(async () => {
    if (!feed || busy) return;
    const token = ++escalation.current;
    setBusy(true);
    setError(null);
    setProgress({ sent: 0, total: feed.steps.length });
    try {
      for (let index = 0; index < feed.steps.length; index += 1) {
        if (escalation.current !== token) break;
        const step = feed.steps[index];
        if (index > 0 && step.delay_ms > 0) await sleep(step.delay_ms);
        if (escalation.current !== token) break;
        const started = await ingestEvents([stepToEvent(step)], "command centre escalation");
        setRun(started);
        await waitForRun(started.id);
        await refresh();
        setProgress({ sent: index + 1, total: feed.steps.length });
      }
    } catch (thrown) {
      setError(
        thrown instanceof ApiRefusedError
          ? `The API refused an observation: ${thrown.detail}`
          : "The API did not accept the observation feed. Nothing further was ingested.",
      );
    } finally {
      if (escalation.current === token) setBusy(false);
    }
  }, [feed, busy, refresh]);

  const reset = useCallback(async () => {
    escalation.current += 1;
    generation.current += 1;
    const mine = generation.current;
    setBusy(true);
    setError(null);
    try {
      const nextLive = await resetLive();
      const [nextBrief, nextZones, nextPlan] = await Promise.all([
        api.briefPreview(),
        api.liveZones(),
        api.livePlan(),
      ]);
      if (generation.current !== mine) return;
      setLive(nextLive);
      setBrief(nextBrief);
      setZones(nextZones.features);
      setPlan(nextPlan);
      setRun(null);
      setProgress(null);
    } catch {
      setError("The API did not answer the reset. The picture on screen is unchanged.");
    } finally {
      setBusy(false);
    }
  }, []);

  const escalated = live.events_ingested > 0;
  const byPhase = brief.situation.by_phase;
  const totals = brief.plan.totals;
  const resolving = intro === "resolving" || (mapReady && stage < LAYER_SEQUENCE.length);

  return (
    <div
      className="relative flex min-h-[calc(100vh-49px)] flex-col xl:h-[calc(100vh-49px)] xl:min-h-0"
      data-testid="command-centre"
      data-basis={brief.basis}
    >
      {intro === "cold" ? (
        <ColdOpen disclaimer={brief.scenario_disclaimer} onEnter={endColdOpen} />
      ) : null}

      {brief.situation.plan_requires_review ? (
        <div
          data-testid="plan-review-banner"
          className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b px-5 py-2"
          style={{
            borderColor: "var(--color-critical)",
            background: "color-mix(in srgb, var(--color-critical) 14%, var(--color-surface))",
          }}
        >
          <span
            className="text-[10px] font-semibold uppercase tracking-[0.16em]"
            style={{ color: "var(--color-critical)" }}
          >
            Plan requires review
          </span>
          <span className="text-[11px] leading-relaxed text-[var(--color-ink)]">
            {brief.situation.review_headline}
          </span>
          <Link
            href="/live"
            className="ml-auto text-[11px] text-[var(--color-ink-muted)] underline-offset-2 hover:underline"
          >
            Decisions to revisit
          </Link>
        </div>
      ) : null}

      <header className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-b border-[var(--color-line)] bg-[var(--color-surface)] px-5 py-3">
        <div className="min-w-0 max-w-2xl">
          <p className="text-[10px] uppercase tracking-[0.18em] text-[var(--color-ink-faint)]">
            Command centre &middot; Alaknanda valley corridor, Chamoli
          </p>
          <h1 className="mt-0.5 text-[17px] font-semibold text-[var(--color-ink)]">
            Who must move, where they can go, and what happens next
          </h1>
          <p className="mt-0.5 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
            {brief.scenario_name}. {brief.scenario_disclaimer}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span
            className="numeric rounded-sm border px-2 py-1 text-[10px] uppercase tracking-[0.1em]"
            style={{
              borderColor: escalated ? "var(--color-warning)" : "var(--color-line-strong)",
              color: escalated ? "var(--color-warning)" : "var(--color-ink-muted)",
            }}
            data-testid="basis-chip"
            title={brief.basis_note}
          >
            {brief.basis === "LIVE"
              ? `Live · ${brief.run_id} · ${num(brief.events_ingested)} observations`
              : "Baseline · no live observations"}
          </span>
          <button
            type="button"
            onClick={escalate}
            disabled={!feed || busy || escalated}
            data-testid="run-monsoon-escalation"
            data-busy={busy ? "true" : "false"}
            className="rounded-sm px-4 py-2 text-[12px] font-semibold uppercase tracking-[0.12em] disabled:cursor-not-allowed disabled:opacity-50"
            style={{
              background: "var(--color-critical)",
              color: "#fff",
            }}
          >
            {busy && progress
              ? `Ingesting ${progress.sent + (progress.sent < progress.total ? 1 : 0)} of ${progress.total}`
              : escalated
                ? "Escalation ingested"
                : "Run monsoon escalation"}
          </button>
          {escalated || busy ? (
            <button
              type="button"
              onClick={reset}
              data-testid="reset-live"
              className="rounded-sm border border-[var(--color-line-strong)] px-3 py-2 text-[11px] text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
            >
              Reset to baseline
            </button>
          ) : null}
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_410px]">
        <section className="grid min-h-0 grid-rows-[minmax(420px,1fr)_auto] xl:grid-rows-[minmax(0,1fr)_270px]">
          <div className="relative min-h-[420px] border-b border-[var(--color-line)]">
            <RiskMap
              studyArea={studyArea}
              terrainUrl={TERRAIN_PREVIEW_URL}
              overlayUrl={HAZARD_OVERLAY_URL}
              roadsUrl={ROADS_GEOJSON_URL}
              networkUrl={closures ? ROUTE_NETWORK_URL : undefined}
              drawnRoutes={stage >= LAYER_SEQUENCE.length ? drawnRoutes : NO_ROUTES}
              closedSegments={live.closed_segments}
              circles={stage >= 3 ? footprints : NO_CIRCLES}
              zones={zones}
              habitations={habitations}
              sites={sites}
              toggles={toggles}
              hazardOpacity={0}
              selected={null}
              onSelectPoint={ignorePoint}
              onSelectZone={ignorePoint}
              habitationColour={habitationColour}
              onReady={onMapReady}
            />
            {resolving ? <LayerResolve stage={stage} /> : <Legend />}
          </div>
          <div className="grid min-h-0 grid-cols-1 lg:grid-cols-2">
            <div className="min-h-[250px] border-b border-[var(--color-line)] lg:border-b-0 lg:border-r">
              <ExecutionGraph run={run} />
            </div>
            <Comparison rows={brief.comparison} />
          </div>
        </section>

        <aside
          aria-label="Decision intelligence"
          className="flex min-h-0 flex-col gap-0 overflow-y-auto border-t border-[var(--color-line)] bg-[var(--color-surface)] xl:border-l xl:border-t-0"
        >
          {error ? (
            <p className="border-b border-[var(--color-line)] px-4 py-2 text-[11px] text-[var(--color-critical)]" role="alert">
              {error}
            </p>
          ) : null}

          <Block title="Current situation" testId="situation">
            <p className="text-[12px] leading-relaxed text-[var(--color-ink)]" data-testid="situation-headline">
              {brief.situation.headline}
            </p>
            <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3">
              <Figure
                label="Critical ground, km2"
                value={num(brief.situation.critical_area_km2, 1)}
                was={escalated ? num(live.critical_area_km2_baseline, 1) : null}
                tone="critical"
              />
              <Figure
                label="Residents, immediate tier"
                value={num(byPhase.IMMEDIATE?.population ?? 0)}
                was={escalated ? num(live.immediate_population_baseline) : null}
                tone="critical"
              />
              <Figure
                label={`Placed, of ${num(totals.population_assessed)} assessed`}
                value={num(totals.population_assigned)}
                was={escalated ? num(live.placed_baseline) : null}
                tone="safe"
              />
              <Figure
                label="Without a destination"
                value={num(totals.population_unmet)}
                was={null}
                tone={totals.population_unmet ? "warning" : "neutral"}
              />
            </dl>
            <p className="mt-3 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
              Evidence confidence{" "}
              <span className="uppercase text-[var(--color-ink-muted)]">
                {brief.confidence.modal_band}
              </span>{" "}
              (modal band). {brief.priority_note}
            </p>
          </Block>

          <Block title="Action required" testId="action-required">
            <ol className="flex flex-col gap-3">
              {brief.actions.map((action) => (
                <li key={action.phase} className="border-l-2 pl-3" style={{ borderColor: PHASE_TOKEN[action.phase] }}>
                  <div className="flex items-baseline justify-between gap-2">
                    <span
                      className="text-[10px] font-semibold uppercase tracking-[0.14em]"
                      style={{ color: PHASE_TOKEN[action.phase] }}
                    >
                      {PHASE_LABEL[action.phase]}
                    </span>
                    <span className="numeric text-[11px] text-[var(--color-ink)]">
                      {num(action.people_moved)} residents
                    </span>
                  </div>
                  {action.steps.map((step) => (
                    <p key={step} className="mt-1 text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
                      {step}
                    </p>
                  ))}
                  {action.phase === "IMMEDIATE" && action.movements.length > 0 ? (
                    <ul className="mt-1.5 flex flex-col gap-0.5">
                      {action.movements.slice(0, 4).map((move) => (
                        <li
                          key={`${move.habitation_id}-${move.site_id}`}
                          className="numeric flex justify-between gap-2 text-[10px] text-[var(--color-ink-muted)]"
                        >
                          <span className="truncate">
                            {move.habitation_id} {move.habitation_name} &rarr; {move.site_id}
                          </span>
                          <span className="shrink-0">
                            {num(move.people)} &middot; R {move.route_reliability.toFixed(3)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              ))}
            </ol>
            {brief.plan.capacity_blocked.length > 0 ? (
              <p className="mt-3 text-[10px] leading-relaxed text-[var(--color-ink-muted)]">
                <span className="uppercase tracking-[0.1em]" style={{ color: PHASE_TOKEN.CAPACITY_BLOCKED }}>
                  Capacity blocked
                </span>{" "}
                <span className="numeric">{brief.plan.capacity_blocked.join(", ")}</span>: high
                risk with no feasible matched capacity. That is itself the finding.
              </p>
            ) : null}
          </Block>

          <Block title="ASTRA recommendation" testId="recommendation">
            <p className="text-[12px] leading-relaxed text-[var(--color-ink)]">
              {brief.narration.text}
            </p>
            <p className="mt-1 text-[9px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
              Narration: {brief.narration.mode === "model" ? "language model, numbers verified" : "deterministic template"}
            </p>

            <p className="mt-3 text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
              Priority queue
            </p>
            <ol className="mt-1 flex flex-col" data-testid="priority-queue">
              {brief.priorities.slice(0, 5).map((row) => (
                <li key={row.habitation_id}>
                  <Link
                    href="/priority"
                    className="grid grid-cols-[22px_minmax(0,1fr)_auto] items-baseline gap-2 rounded-sm px-1 py-1 hover:bg-[var(--color-surface-raised)]"
                  >
                    <span className="numeric text-[10px] text-[var(--color-ink-faint)]">{row.rank}</span>
                    <span className="truncate text-[11px] text-[var(--color-ink)]">
                      <span className="numeric text-[var(--color-ink-muted)]">{row.habitation_id}</span>{" "}
                      {row.name}
                    </span>
                    <span className="flex items-baseline gap-2">
                      <span className="numeric text-[11px] text-[var(--color-ink)]">
                        {row.priority_score.toFixed(1)}
                      </span>
                      <span
                        className="w-[62px] text-right text-[9px] uppercase tracking-[0.06em]"
                        style={{ color: PHASE_TOKEN[row.phase] }}
                      >
                        {PHASE_LABEL[row.phase]}
                      </span>
                    </span>
                  </Link>
                </li>
              ))}
            </ol>

            {brief.capacity.sites.some((site) => site.suitable && site.marginal_headline) ? (
              <>
                <p className="mt-3 text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
                  What unlocks capacity
                </p>
                <ul className="mt-1 flex flex-col gap-1.5">
                  {brief.capacity.sites
                    .filter((site) => site.suitable && site.marginal_headline)
                    .slice(0, 2)
                    .map((site) => (
                      <li key={site.site_id} className="text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
                        <Link href="/sites" className="numeric text-[var(--color-ink)] hover:underline">
                          {site.site_id} {site.name}
                        </Link>{" "}
                        {site.marginal_headline}
                      </li>
                    ))}
                </ul>
              </>
            ) : null}

            <div className="mt-4 flex flex-col gap-2">
              <GenerateBriefButton />
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={startDemo}
                  data-testid="start-demo"
                  className="rounded-sm border border-[var(--color-line-strong)] px-3 py-1.5 text-[12px] text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
                >
                  Demo mode
                </button>
                <Link
                  href="/plan"
                  className="rounded-sm border border-[var(--color-line-strong)] px-3 py-1.5 text-[12px] text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
                >
                  Open the optimised plan
                </Link>
              </div>
            </div>
          </Block>

          <Block title="How this works" testId="how-this-works">
            <p className="text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
              {brief.how_this_works}
            </p>
            <Link href="/model" className="mt-1.5 inline-block text-[11px] text-[var(--color-ink)] hover:underline">
              Weights, formulas and back-test results
            </Link>
          </Block>

          <p className="mt-auto border-t border-[var(--color-line)] px-4 py-3 text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
            {brief.decision_authority}
          </p>
        </aside>
      </div>
    </div>
  );
}

function Block({
  title,
  testId,
  children,
}: {
  title: string;
  testId: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-b border-[var(--color-line)] px-4 py-3.5" data-testid={testId}>
      <h2 className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--color-ink-faint)]">
        {title}
      </h2>
      <div className="mt-2">{children}</div>
    </section>
  );
}

function Figure({
  label,
  value,
  was,
  tone,
}: {
  label: string;
  value: string;
  was: string | null;
  tone: "critical" | "warning" | "safe" | "neutral";
}) {
  const colour = {
    critical: "var(--color-critical)",
    warning: "var(--color-warning)",
    safe: "var(--color-safe)",
    neutral: "var(--color-ink)",
  }[tone];
  return (
    <div>
      <dt className="text-[9px] uppercase leading-tight tracking-[0.12em] text-[var(--color-ink-faint)]">
        {label}
      </dt>
      <dd className="numeric mt-1 text-[20px] leading-none" style={{ color: colour }}>
        {value}
        {was !== null && was !== value ? (
          <span className="ml-1.5 text-[10px] text-[var(--color-ink-faint)]">was {was}</span>
        ) : null}
      </dd>
    </div>
  );
}

function Comparison({ rows }: { rows: BriefResponse["comparison"] }) {
  return (
    <section className="min-h-0 overflow-y-auto bg-[var(--color-surface)] px-4 py-3" data-testid="comparison-panel">
      <h2 className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--color-ink-faint)]">
        Static hazard map vs ASTRA decision mode
      </h2>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full border-collapse text-left text-[10px]">
          <thead>
            <tr className="border-b border-[var(--color-line-strong)] text-[9px] uppercase tracking-[0.08em] text-[var(--color-ink-faint)]">
              <th scope="col" className="py-1 pr-2 font-medium">Question</th>
              <th scope="col" className="py-1 pr-2 font-medium">Static hazard map</th>
              <th scope="col" className="py-1 font-medium">ASTRA decision mode</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.question} className="border-b border-[var(--color-line)] align-top">
                <th scope="row" className="py-1.5 pr-2 font-medium text-[var(--color-ink)]">
                  {row.question}
                </th>
                <td className="py-1.5 pr-2 leading-relaxed text-[var(--color-ink-faint)]">{row.static_map}</td>
                <td className="py-1.5 leading-relaxed text-[var(--color-ink-muted)]">{row.astra}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Legend() {
  return (
    <div className="pointer-events-none absolute left-2 top-2 z-10 rounded-sm border border-[var(--color-line)] bg-[var(--color-abyss)]/85 px-2.5 py-2">
      <p className="text-[9px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
        Habitations by relocation tier
      </p>
      <ul className="mt-1 flex flex-col gap-0.5">
        {["IMMEDIATE", "SHORT_TERM", "MEDIUM_TERM", "CAPACITY_BLOCKED", "NOT_PRIORITISED"].map((phase) => (
          <li key={phase} className="flex items-center gap-1.5 text-[10px] text-[var(--color-ink-muted)]">
            <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: PHASE_TOKEN[phase] }} />
            {PHASE_LABEL[phase]}
          </li>
        ))}
        <li className="mt-1 flex items-center gap-1.5 text-[10px] text-[var(--color-ink-muted)]">
          <span aria-hidden className="h-2 w-2 rounded-full border border-[#baf0e4] bg-[#3f9c8c]" />
          Candidate site
        </li>
      </ul>
      <p className="mt-1.5 text-[9px] text-[var(--color-ink-faint)]">
        Zones: ASTRA analytical classification
      </p>
    </div>
  );
}

function LayerResolve({ stage }: { stage: number }) {
  return (
    <div
      className="pointer-events-none absolute left-2 top-2 z-10 rounded-sm border border-[var(--color-line)] bg-[var(--color-abyss)]/90 px-3 py-2"
      data-testid="layer-resolve"
      aria-live="polite"
    >
      <p className="text-[9px] uppercase tracking-[0.14em] text-[var(--color-ink-faint)]">
        Resolving the study area
      </p>
      <ol className="mt-1 flex flex-col gap-0.5">
        {LAYER_SEQUENCE.map((layer, index) => (
          <li
            key={layer.label}
            className="flex items-baseline gap-2 text-[10px] transition-opacity duration-300"
            style={{ opacity: index < stage ? 1 : 0.3 }}
          >
            <span
              aria-hidden
              className="inline-block h-1.5 w-1.5 shrink-0"
              style={{ background: index < stage ? "var(--color-safe)" : "var(--color-line-strong)" }}
            />
            <span className="text-[var(--color-ink)]">{layer.label}</span>
            <span className="text-[var(--color-ink-faint)]">{layer.source}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function ColdOpen({ disclaimer, onEnter }: { disclaimer: string; onEnter: () => void }) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Why ASTRA exists"
      data-testid="cold-open"
      className="fixed inset-0 z-50 flex items-center justify-center overflow-hidden bg-[#04070c]"
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={TERRAIN_PREVIEW_URL}
        alt=""
        aria-hidden
        className="absolute inset-0 h-full w-full object-cover opacity-[0.14] grayscale"
      />
      <div className="relative max-w-2xl px-8">
        <p className="text-[10px] uppercase tracking-[0.22em] text-[var(--color-ink-faint)]">
          Alaknanda valley corridor &middot; Chamoli district, Uttarakhand
        </p>
        <p className="mt-5 text-[21px] leading-snug text-[var(--color-ink)]">
          On 7 February 2021 a rock and ice avalanche in Chamoli district became a
          debris flood down the Rishiganga and Dhauliganga valleys. More than 200
          people were killed or went missing.
        </p>
        <p className="mt-2 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
          Shugar et al., &ldquo;A massive rock and ice avalanche caused the 2021
          disaster at Chamoli, Indian Himalaya&rdquo;, Science 373 (2021), 300&ndash;306.
        </p>
        <p className="mt-6 text-[15px] leading-relaxed text-[var(--color-ink-muted)]">
          Relocation of vulnerable habitations from hazard-prone ground is still
          largely initiated after a disaster strikes, rather than planned before it.
        </p>
        <p className="mt-2 text-[10px] text-[var(--color-ink-faint)]">
          Problem statement SIH26191, Ministry of Home Affairs (NDRF, DM Division).
        </p>
        <p className="mt-8 max-w-xl text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
          {disclaimer}
        </p>
        <button
          type="button"
          onClick={onEnter}
          autoFocus
          className="mt-5 rounded-sm border border-[var(--color-line-strong)] px-3 py-1.5 text-[11px] uppercase tracking-[0.14em] text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
        >
          Enter the command centre
        </button>
      </div>
    </div>
  );
}

function stepToEvent(step: FeedStepResponse): EventSubmission {
  return {
    kind: step.kind,
    lon: step.lon,
    lat: step.lat,
    radius_m: step.radius_m,
    value: step.value,
    target: step.target,
    source: step.source,
    note: step.note,
    observed_at: null,
  };
}

/** Poll a run until it is terminal. The graph streams it; this gates the feed. */
async function waitForRun(runId: string, timeoutMs = 180_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const run = await api.run(runId);
    if (run.status === "COMPLETED" || run.status === "FAILED") return;
    await sleep(300);
  }
}
