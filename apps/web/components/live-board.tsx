"use client";

import type {
  CandidateSite,
  EventFeedResponse,
  EventResponse,
  EventSubmission,
  Habitation,
  LiveStateResponse,
  PlanResponse,
  RunResponse,
  StudyArea,
  ZoneFeature,
} from "@astra/contracts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ExecutionGraph } from "@/components/execution-graph";
import { type DrawnRoute, RiskMap } from "@/components/risk-map";
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

const KIND_LABEL: Record<string, string> = {
  RAINFALL_OBSERVATION: "Rainfall",
  INCIDENT_REPORT: "Incident",
  FIELD_EVIDENCE: "Field evidence",
  INFRASTRUCTURE_STATUS: "Road status",
};

const KIND_COLOUR: Record<string, string> = {
  RAINFALL_OBSERVATION: "var(--color-signal)",
  INCIDENT_REPORT: "var(--color-critical)",
  FIELD_EVIDENCE: "var(--color-warning)",
  INFRASTRUCTURE_STATUS: "var(--color-neutral)",
};

function num(value: number, digits = 0): string {
  return value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function deltaColour(value: number, goodWhenUp = true): string {
  if (Math.abs(value) < 1e-9) return "var(--color-ink-muted)";
  return (goodWhenUp ? value > 0 : value < 0)
    ? "var(--color-safe)"
    : "var(--color-critical)";
}

/**
 * Live operations.
 *
 * Observations arrive, the pipeline executes, and the map, the tiers and the
 * plan move with them. Every number on this screen came back from the API after
 * a run the graph on the right actually watched execute; the feed control posts
 * real events to the real ingest endpoint one at a time, exactly as an external
 * feed would.
 */
export function LiveBoard({
  studyArea,
  baselineZones,
  habitations,
  sites,
  initialState,
  initialZones,
  initialPlan,
  feed,
}: {
  studyArea: StudyArea;
  baselineZones: ZoneFeature[];
  habitations: Habitation[];
  sites: CandidateSite[];
  initialState: LiveStateResponse;
  initialZones: ZoneFeature[];
  initialPlan: PlanResponse;
  feed: EventFeedResponse | null;
}) {
  const [state, setState] = useState(initialState);
  const [zones, setZones] = useState(initialZones);
  const [plan, setPlan] = useState(initialPlan);
  const [run, setRun] = useState<RunResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"live" | "baseline">("live");
  const [feedIndex, setFeedIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  // The replay loop reads this ref to know whether it has been paused. It is set
  // directly rather than mirrored from state during render: state has not been
  // committed by the time the loop's second iteration runs, so a mirrored ref
  // would still read `false` and stop the feed after a single observation.
  const playingRef = useRef(false);
  const setPlayingBoth = useCallback((value: boolean) => {
    playingRef.current = value;
    setPlaying(value);
  }, []);

  // A refresh is three fetches, and a run's completion can start one at the same
  // moment the user presses Reset. Without a generation token the in-flight
  // refresh lands *after* the reset and puts the discarded numbers back on
  // screen - a reset that visibly does not reset. Each refresh remembers the
  // generation it started in and drops its own result if that generation is
  // over.
  const generation = useRef(0);

  const refresh = useCallback(async () => {
    const mine = generation.current;
    const [next, nextZones, nextPlan] = await Promise.all([
      api.live(),
      api.liveZones(),
      api.livePlan(),
    ]);
    if (generation.current !== mine) return;
    setState(next);
    setZones(nextZones.features);
    setPlan(nextPlan);
  }, []);

  /** Post one observation and follow the run it starts. */
  const send = useCallback(
    async (events: EventSubmission[], trigger: string) => {
      setBusy(true);
      setError(null);
      try {
        const started = await ingestEvents(events, trigger);
        setRun(started);
        return started;
      } catch (thrown) {
        setError(
          thrown instanceof ApiRefusedError
            ? `The API refused this observation: ${thrown.detail}`
            : "The API did not accept this observation. Nothing was ingested.",
        );
        setBusy(false);
        return null;
      }
    },
    [],
  );

  const onRunFinished = useCallback(
    async (_runId: string, status: string) => {
      if (status === "COMPLETED") await refresh();
      setBusy(false);
    },
    [refresh],
  );

  /** Replay the demonstration feed, one real POST per step. */
  const playFeed = useCallback(async () => {
    if (!feed) return;
    setPlayingBoth(true);
    setError(null);
    for (let index = feedIndex; index < feed.steps.length; index += 1) {
      if (!playingRef.current) break;
      const step = feed.steps[index];
      if (step.delay_ms > 0 && index > feedIndex) {
        await new Promise((resolve) => setTimeout(resolve, step.delay_ms));
        if (!playingRef.current) break;
      }
      const started = await send([stepToEvent(step)], "demonstration feed");
      setFeedIndex(index + 1);
      if (!started) break;
      await waitForRun(started.id);
      await refresh();
    }
    setPlayingBoth(false);
    setBusy(false);
  }, [feed, feedIndex, refresh, send, setPlayingBoth]);

  const stepOnce = useCallback(async () => {
    if (!feed || feedIndex >= feed.steps.length) return;
    const step = feed.steps[feedIndex];
    const started = await send([stepToEvent(step)], "demonstration feed");
    setFeedIndex(feedIndex + 1);
    if (started) {
      await waitForRun(started.id);
      await refresh();
      setBusy(false);
    }
  }, [feed, feedIndex, refresh, send]);

  const reset = useCallback(async () => {
    setPlayingBoth(false);
    generation.current += 1;
    setBusy(true);
    setError(null);
    try {
      const mine = generation.current;
      const next = await resetLive();
      const [nextZones, nextPlan] = await Promise.all([
        api.liveZones(),
        api.livePlan(),
      ]);
      if (generation.current !== mine) return;
      setState(next);
      setZones(nextZones.features);
      setPlan(nextPlan);
      setRun(null);
      setFeedIndex(0);
    } finally {
      setBusy(false);
    }
  }, [setPlayingBoth]);

  // Leaving the screen mid-replay must stop the replay, not leave a loop posting
  // observations into a page nobody is looking at.
  useEffect(() => () => setPlayingBoth(false), [setPlayingBoth]);

  const shownZones = view === "live" ? zones : baselineZones;

  const drawnRoutes: DrawnRoute[] = useMemo(
    () =>
      plan.assignments
        .filter((assignment) => assignment.route_geometry.length > 1)
        .map((assignment) => ({
          id: `${assignment.habitation_id}-${assignment.site_id}-${assignment.phase}`,
          geometry: assignment.route_geometry,
          colour: [120, 132, 150, 120] as [number, number, number, number],
          width: 34,
        })),
    [plan],
  );

  /** Event footprints, drawn as the circles the engine actually re-scored. */
  const footprints = useMemo(
    () =>
      state.events
        .filter((event) => event.lon !== null && event.lat !== null)
        .map((event) => ({
          id: event.id,
          lon: event.lon as number,
          lat: event.lat as number,
          radius_m: event.radius_m,
          colour: KIND_COLOUR[event.kind] ?? "var(--color-signal)",
        })),
    [state.events],
  );

  const review = state.review;

  return (
    <div className="flex min-h-[calc(100vh-49px)] flex-col xl:h-[calc(100vh-49px)] xl:min-h-0">
      {review?.required ? (
        <div
          data-testid="plan-review-banner"
          className="flex flex-wrap items-start gap-x-4 gap-y-1 border-b px-5 py-2.5"
          style={{
            borderColor: "var(--color-critical)",
            background:
              "color-mix(in srgb, var(--color-critical) 14%, var(--color-surface))",
          }}
        >
          <span
            className="text-[10px] font-semibold uppercase tracking-[0.16em]"
            style={{ color: "var(--color-critical)" }}
          >
            Plan requires review
          </span>
          <span className="text-[11px] leading-relaxed text-[var(--color-ink)]">
            {review.headline}
          </span>
          <span className="ml-auto text-[10px] text-[var(--color-ink-faint)]">
            {review.decision_authority}
          </span>
        </div>
      ) : null}

      <header className="flex flex-wrap items-start justify-between gap-6 border-b border-[var(--color-line)] bg-[var(--color-surface)] px-5 py-3">
        <div className="max-w-xl">
          <p className="text-[10px] uppercase tracking-[0.18em] text-[var(--color-ink-faint)]">
            Live operations
          </p>
          <h1 className="mt-1 text-[17px] font-semibold text-[var(--color-ink)]">
            Evidence arrives, the assessment moves
          </h1>
          <p
            className="mt-1 text-[11px] leading-relaxed text-[var(--color-ink-muted)]"
            data-testid="live-headline"
          >
            {state.headline}
          </p>
        </div>
        <div className="flex flex-wrap gap-7" data-testid="live-metrics">
          <Metric
            label="Critical zone, km2"
            before={state.critical_area_km2_baseline}
            after={state.critical_area_km2_now}
            digits={1}
            goodWhenUp={false}
          />
          <Metric
            label="Residents, immediate tier"
            before={state.immediate_population_baseline}
            after={state.immediate_population_now}
            goodWhenUp={false}
          />
          <Metric
            label="Routes above threshold"
            before={state.feasible_routes_baseline}
            after={state.feasible_routes_now}
          />
          <Metric
            label="Residents placed"
            before={state.placed_baseline}
            after={state.placed_now}
          />
          <div className="flex flex-col gap-1">
            <span
              className="numeric text-[19px] leading-none text-[var(--color-ink)]"
              data-testid="cells-rescored"
            >
              {num(state.cells_rescored)}
            </span>
            <span className="max-w-[150px] text-[9px] uppercase leading-tight tracking-[0.14em] text-[var(--color-ink-faint)]">
              Cells re-scored of {num(state.cells_in_grid)}
            </span>
          </div>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="flex min-h-0 flex-col overflow-y-auto border-b border-[var(--color-line)] bg-[var(--color-surface)] px-4 pb-4 xl:border-b-0 xl:border-r">
          <div className="sticky top-0 -mx-4 bg-[var(--color-surface)] px-4 pb-2 pt-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
              Observation feed
            </p>
            <div
              className="mt-1.5 flex flex-wrap items-center gap-2"
              data-testid="feed-controls"
              data-busy={busy || playing ? "true" : "false"}
              data-sent={feedIndex}
              data-total={feed?.steps.length ?? 0}
            >
              <button
                type="button"
                data-testid="feed-play"
                onClick={playing ? () => setPlayingBoth(false) : playFeed}
                disabled={!feed || (busy && !playing) || feedIndex >= (feed?.steps.length ?? 0)}
                className="rounded-sm border px-3 py-1.5 text-[11px] disabled:opacity-40"
                style={{ borderColor: "var(--color-signal)", color: "var(--color-ink)" }}
              >
                {playing ? "Pause feed" : "Play feed"}
              </button>
              <button
                type="button"
                data-testid="feed-step"
                onClick={stepOnce}
                disabled={!feed || busy || playing || feedIndex >= (feed?.steps.length ?? 0)}
                className="rounded-sm border border-[var(--color-line-strong)] px-2 py-1.5 text-[11px] text-[var(--color-ink-muted)] disabled:opacity-40"
              >
                Send next
              </button>
              <button
                type="button"
                data-testid="live-reset"
                onClick={reset}
                disabled={busy && !playing}
                className="rounded-sm px-2 py-1.5 text-[11px] text-[var(--color-ink-faint)] hover:text-[var(--color-ink)] disabled:opacity-40"
              >
                Reset
              </button>
              <span className="numeric ml-auto text-[10px] text-[var(--color-ink-faint)]">
                {feedIndex}/{feed?.steps.length ?? 0}
              </span>
            </div>
          </div>

          {feed ? (
            <>
              <p className="mt-1 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
                <span
                  className="mr-1.5 rounded-sm border px-1 py-px text-[9px] uppercase tracking-[0.1em]"
                  style={{
                    borderColor: "var(--color-line-strong)",
                    color: "var(--color-ink-muted)",
                  }}
                >
                  {feed.provenance.replace(/_/g, " ")}
                </span>
                {feed.description}
              </p>
              <ol className="mt-2 flex flex-col gap-1">
                {feed.steps.map((step, index) => (
                  <li
                    key={step.index}
                    className="rounded-sm border px-2 py-1.5"
                    style={{
                      borderColor:
                        index < feedIndex
                          ? "var(--color-line)"
                          : index === feedIndex
                            ? "var(--color-signal)"
                            : "var(--color-line)",
                      opacity: index < feedIndex ? 0.55 : 1,
                    }}
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span
                        className="text-[10px] uppercase tracking-[0.1em]"
                        style={{ color: KIND_COLOUR[step.kind] }}
                      >
                        {KIND_LABEL[step.kind] ?? step.kind}
                      </span>
                      <span className="numeric text-[10px] text-[var(--color-ink-muted)]">
                        {step.kind === "RAINFALL_OBSERVATION"
                          ? `${num(step.value)} mm`
                          : step.kind === "INFRASTRUCTURE_STATUS"
                            ? "closed"
                            : num(step.value, 2)}
                      </span>
                    </div>
                    <p className="mt-0.5 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
                      {step.note}
                    </p>
                  </li>
                ))}
              </ol>
            </>
          ) : (
            <p className="mt-2 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
              The demonstration feed is not available. Observations can still be
              posted to the ingest endpoint directly.
            </p>
          )}

          {error ? (
            <p
              className="mt-3 text-[11px] text-[var(--color-critical)]"
              data-testid="live-error"
            >
              {error}
            </p>
          ) : null}

          <div className="mt-4 border-t border-[var(--color-line)] pt-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
              Ingested ({state.events_ingested})
            </p>
            {state.events.length === 0 ? (
              <p className="mt-1.5 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
                Nothing ingested. ASTRA is showing the baseline assessment.
              </p>
            ) : (
              <ul className="mt-1.5 flex flex-col gap-1.5" data-testid="event-log">
                {[...state.events].reverse().map((event) => (
                  <EventRow key={event.id} event={event} />
                ))}
              </ul>
            )}
          </div>

          {review?.required ? (
            <div className="mt-4 border-t border-[var(--color-line)] pt-3">
              <p
                className="text-[10px] uppercase tracking-[0.12em]"
                style={{ color: "var(--color-critical)" }}
              >
                Decisions to revisit
              </p>
              <ul className="mt-1.5 flex flex-col gap-1 text-[10px] text-[var(--color-ink-muted)]">
                {review.invalidated_movements.slice(0, 8).map((movement, index) => {
                  const entry = movement as Record<string, unknown>;
                  return (
                    <li key={`${entry.habitation_id}-${entry.site_id}-${index}`}>
                      <span className="numeric text-[var(--color-ink)]">
                        {String(entry.habitation_id)} &rarr; {String(entry.site_id)}
                      </span>{" "}
                      <span className="numeric">
                        {num(Number(entry.people ?? 0))} residents
                      </span>
                      <span className="block text-[var(--color-ink-faint)]">
                        {(entry.reasons as string[] | undefined)?.join("; ")}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : null}

          <p className="mt-4 border-t border-[var(--color-line)] pt-3 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
            {state.rescore_note}
          </p>
        </aside>

        <section className="grid min-h-0 grid-rows-[minmax(340px,1fr)_minmax(240px,320px)]">
          <div className="relative min-h-0 border-b border-[var(--color-line)]">
            <RiskMap
              studyArea={studyArea}
              terrainUrl={TERRAIN_PREVIEW_URL}
              overlayUrl={HAZARD_OVERLAY_URL}
              roadsUrl={ROADS_GEOJSON_URL}
              networkUrl={ROUTE_NETWORK_URL}
              drawnRoutes={drawnRoutes}
              closedSegments={state.closed_segments}
              circles={view === "live" ? footprints : []}
              zones={shownZones}
              habitations={habitations}
              sites={sites}
              toggles={{
                hazard: false,
                zones: true,
                habitations: true,
                sites: true,
                roads: false,
                network: true,
              }}
              hazardOpacity={0}
              selected={null}
              onSelectPoint={() => undefined}
              onSelectZone={() => undefined}
            />
            <div className="absolute left-2 top-2 z-10 rounded-sm border border-[var(--color-line)] bg-[var(--color-abyss)] px-2.5 py-1.5">
              <p className="text-[9px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
                Map showing
              </p>
              <div className="mt-1 flex gap-1" role="group" aria-label="Map comparison">
                {(["baseline", "live"] as const).map((option) => (
                  <button
                    key={option}
                    type="button"
                    data-testid={`map-view-${option}`}
                    aria-pressed={view === option}
                    onClick={() => setView(option)}
                    className="rounded-sm border px-2 py-0.5 text-[10px]"
                    style={{
                      borderColor:
                        view === option
                          ? "var(--color-signal)"
                          : "var(--color-line-strong)",
                      color:
                        view === option
                          ? "var(--color-ink)"
                          : "var(--color-ink-faint)",
                    }}
                  >
                    {option === "baseline" ? "Baseline" : "Live"}
                  </button>
                ))}
              </div>
              <p
                className="mt-1 text-[10px] text-[var(--color-ink-muted)]"
                data-testid="map-caption"
              >
                <span className="numeric">{num(shownZones.length)}</span> zones
                <span className="text-[var(--color-ink-faint)]">
                  {" "}
                  ({state.classification_label})
                </span>
                {view === "live" && footprints.length > 0
                  ? ` · ${num(footprints.length)} observation footprint${footprints.length === 1 ? "" : "s"}`
                  : ""}
              </p>
            </div>
          </div>
          <div className="min-h-0">
            <ExecutionGraph run={run} onFinished={onRunFinished} />
          </div>
        </section>
      </div>
    </div>
  );
}

function EventRow({ event }: { event: EventResponse }) {
  return (
    <li className="border-l-2 pl-2" style={{ borderColor: KIND_COLOUR[event.kind] }}>
      <div className="flex items-baseline justify-between gap-2">
        <span
          className="text-[9px] uppercase tracking-[0.1em]"
          style={{ color: KIND_COLOUR[event.kind] }}
        >
          {KIND_LABEL[event.kind] ?? event.kind}
        </span>
        <span className="numeric text-[9px] text-[var(--color-ink-faint)]">
          {event.id}
        </span>
      </div>
      <p className="text-[10px] leading-relaxed text-[var(--color-ink-muted)]">
        {event.description}
      </p>
      <p className="text-[9px] text-[var(--color-ink-faint)]">{event.source}</p>
    </li>
  );
}

function Metric({
  label,
  before,
  after,
  digits = 0,
  goodWhenUp = true,
}: {
  label: string;
  before: number;
  after: number;
  digits?: number;
  goodWhenUp?: boolean;
}) {
  const change = after - before;
  return (
    <div className="flex flex-col gap-1">
      <span className="numeric text-[19px] leading-none text-[var(--color-ink)]">
        {num(before, digits)}
        <span className="mx-1 text-[var(--color-ink-faint)]">&rarr;</span>
        <span style={{ color: deltaColour(change, goodWhenUp) }}>
          {num(after, digits)}
        </span>
      </span>
      <span className="max-w-[150px] text-[9px] uppercase leading-tight tracking-[0.14em] text-[var(--color-ink-faint)]">
        {label}
      </span>
    </div>
  );
}

function stepToEvent(step: {
  kind: string;
  lon: number | null;
  lat: number | null;
  radius_m: number;
  value: number;
  target: string | null;
  source: string;
  note: string;
}): EventSubmission {
  return {
    kind: step.kind as EventSubmission["kind"],
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
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
}
