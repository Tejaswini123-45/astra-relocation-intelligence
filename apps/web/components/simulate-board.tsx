"use client";

import type {
  CandidateSite,
  Habitation,
  PerturbationKind,
  PlanDependencyListResponse,
  PlanResponse,
  Perturbation,
  ScenarioDiffResponse,
  ServiceType,
  SiteCapacityListResponse,
  StudyArea,
  ZoneFeature,
} from "@astra/contracts";
import { useCallback, useMemo, useState } from "react";

import { type DrawnRoute, RiskMap } from "@/components/risk-map";
import { Slider } from "@/components/slider";
import {
  ApiRefusedError,
  HAZARD_OVERLAY_URL,
  ROADS_GEOJSON_URL,
  ROUTE_NETWORK_URL,
  simulate,
  TERRAIN_PREVIEW_URL,
} from "@/lib/api";

const PHASE_LABEL: Record<string, string> = {
  IMMEDIATE: "Immediate",
  SHORT_TERM: "Short-term",
  MEDIUM_TERM: "Medium-term",
  NOT_PRIORITISED: "Not prioritised",
  CAPACITY_BLOCKED: "Capacity blocked",
};

const SERVICE_LABEL: Record<string, string> = {
  LAND: "land",
  SHELTER: "shelter",
  WATER: "water",
  SANITATION: "sanitation",
  HEALTHCARE: "healthcare",
  POWER: "power",
  ACCESS: "access",
};

/** Services a site can actually be given more of. Land and access are not. */
const UPGRADABLE: ServiceType[] = [
  "WATER",
  "SANITATION",
  "HEALTHCARE",
  "POWER",
  "SHELTER",
];

/** Which engine each perturbation enters at. Shown on the change list. */
const ENTRY_STAGE: Record<string, string> = {
  RAINFALL_MULTIPLIER: "enters at Engine 1 (hazard)",
  LANDSLIDE_SHIFT: "enters at Engine 1 (hazard)",
  POPULATION_MULTIPLIER: "enters at Engine 2 (exposure)",
  SITE_CAPACITY_LOSS: "enters at Engine 4 (capacity)",
  SERVICE_UPGRADE: "enters at Engine 4 (capacity)",
  SITE_DISABLED: "enters at Engine 4 (candidate set)",
  ROAD_CLOSURE: "enters at Engine 5 (network)",
};

function num(value: number, digits = 0): string {
  return value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function pct(value: number): string {
  return `${(value * 100).toFixed(0)}%`;
}

function delta(value: number, digits = 0): string {
  return `${value > 0 ? "+" : ""}${num(value, digits)}`;
}

function deltaColour(value: number, goodWhenUp = true): string {
  if (Math.abs(value) < 1e-9) return "var(--color-ink-muted)";
  const good = goodWhenUp ? value > 0 : value < 0;
  return good ? "var(--color-safe)" : "var(--color-critical)";
}

type SiteEdit = {
  /** Fraction of supply lost, 0 when untouched. */
  loss: number;
  /** Service to add supply to, and how much, from the site's own intervention. */
  upgrade: { service: ServiceType; amount: number; unit: string } | null;
  withdrawn: boolean;
};

const NO_EDIT: SiteEdit = { loss: 0, upgrade: null, withdrawn: false };

/**
 * The scenario builder.
 *
 * Every control maps to exactly one typed perturbation, and each perturbation
 * enters the chain at one named engine. Nothing here computes a result: the
 * controls assemble a scenario, the API runs the whole chain over it, and the
 * numbers on the right are the difference between two complete assessments.
 */
export function SimulateBoard({
  studyArea,
  zones,
  habitations,
  sites,
  capacity,
  dependencies,
  basePlan,
}: {
  studyArea: StudyArea;
  zones: ZoneFeature[];
  habitations: Habitation[];
  sites: CandidateSite[];
  capacity: SiteCapacityListResponse;
  dependencies: PlanDependencyListResponse;
  basePlan: PlanResponse;
}) {
  const [rainfall, setRainfall] = useState(1.0);
  const [landslide, setLandslide] = useState(0);
  const [population, setPopulation] = useState(1.0);
  const [siteEdits, setSiteEdits] = useState<Record<string, SiteEdit>>({});
  const [closedSegments, setClosedSegments] = useState<string[]>([]);
  const [result, setResult] = useState<ScenarioDiffResponse | null>(null);
  const [view, setView] = useState<"before" | "after">("after");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const editOf = useCallback(
    (siteId: string) => siteEdits[siteId] ?? NO_EDIT,
    [siteEdits],
  );
  const setEdit = useCallback((siteId: string, patch: Partial<SiteEdit>) => {
    setSiteEdits((current) => ({
      ...current,
      [siteId]: { ...(current[siteId] ?? NO_EDIT), ...patch },
    }));
  }, []);

  /**
   * The scenario, assembled from the controls. This is exactly what is posted;
   * the API echoes it back on the result so what produced a diff stays on the
   * diff.
   */
  const changes: Perturbation[] = useMemo(() => {
    const list: Perturbation[] = [];
    const add = (
      kind: PerturbationKind,
      value: number,
      target: string | null = null,
      note: string | null = null,
    ) => list.push({ kind, target, value, note });

    if (rainfall !== 1.0) add("RAINFALL_MULTIPLIER", rainfall);
    if (landslide !== 0) add("LANDSLIDE_SHIFT", landslide);
    if (population !== 1.0) add("POPULATION_MULTIPLIER", population);
    for (const [siteId, edit] of Object.entries(siteEdits)) {
      if (edit.withdrawn) {
        add("SITE_DISABLED", 1, siteId);
        continue;
      }
      if (edit.loss > 0) add("SITE_CAPACITY_LOSS", edit.loss, siteId);
      if (edit.upgrade) {
        add("SERVICE_UPGRADE", edit.upgrade.amount, siteId, edit.upgrade.service);
      }
    }
    for (const segmentId of closedSegments) add("ROAD_CLOSURE", 1, segmentId);
    return list;
  }, [rainfall, landslide, population, siteEdits, closedSegments]);

  const run = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await simulate(changes));
      setView("after");
    } catch (thrown) {
      setResult(null);
      setError(
        thrown instanceof ApiRefusedError
          ? // The engine's own words. A friendlier sentence invented here would
            // be a claim about why the scenario was refused that nothing checked.
            `The engine refused this scenario: ${thrown.detail}`
          : "The API did not answer this scenario. Nothing was changed, and no number on this screen was substituted.",
      );
    } finally {
      setBusy(false);
    }
  }, [changes]);

  const reset = useCallback(() => {
    setRainfall(1.0);
    setLandslide(0);
    setPopulation(1.0);
    setSiteEdits({});
    setClosedSegments([]);
    setResult(null);
    setError(null);
  }, []);

  /** A preset only fills the controls. It never fabricates a result. */
  const applyPreset = useCallback(
    (preset: "monsoon" | "bridge" | "withdraw") => {
      setResult(null);
      setError(null);
      if (preset === "monsoon") {
        // A strong monsoon, not a modelled apocalypse: at higher multipliers the
        // hazard surface swallows every candidate site and the plan places
        // nobody, which is a real output but a useless comparison.
        setRainfall(1.5);
      } else if (preset === "bridge") {
        const bridge =
          dependencies.segments.find((entry) => entry.is_bridge) ??
          dependencies.segments[0];
        if (bridge) setClosedSegments([bridge.segment_id]);
      } else {
        const busiest = [...capacity.sites]
          .filter((site) => site.suitable)
          .sort((a, b) => b.effective_capacity - a.effective_capacity)[0];
        if (busiest) setEdit(busiest.site_id, { withdrawn: true });
      }
    },
    [capacity.sites, dependencies.segments, setEdit],
  );

  const showAfter = Boolean(result) && view === "after";
  const shownZones = showAfter ? result!.zones_after.features : zones;
  const shownPlan = showAfter ? result!.plan_after : basePlan;

  const drawnRoutes: DrawnRoute[] = useMemo(
    () =>
      shownPlan.assignments
        .filter((assignment) => assignment.route_geometry.length > 1)
        .map((assignment) => ({
          id: `${assignment.habitation_id}-${assignment.site_id}-${assignment.phase}`,
          geometry: assignment.route_geometry,
          colour: (showAfter
            ? [86, 190, 172, 200]
            : [130, 142, 162, 150]) as [number, number, number, number],
          width: 40,
        })),
    [shownPlan, showAfter],
  );

  return (
    <div className="flex min-h-[calc(100vh-49px)] flex-col xl:h-[calc(100vh-49px)] xl:min-h-0">
      <header className="flex flex-wrap items-start justify-between gap-6 border-b border-[var(--color-line)] bg-[var(--color-surface)] px-5 py-3">
        <div className="max-w-2xl">
          <p className="text-[10px] uppercase tracking-[0.18em] text-[var(--color-ink-faint)]">
            What-if simulation
          </p>
          <h1 className="mt-1 text-[17px] font-semibold text-[var(--color-ink)]">
            What changes if conditions change
          </h1>
          <p className="mt-1 text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
            {result
              ? result.headline
              : "A scenario runs the whole chain again - hazard, exposure, capacity, routes, optimiser - and reports the difference. The baseline is not replaced; the two results sit side by side."}
          </p>
        </div>
        {result ? (
          <div className="flex flex-wrap gap-7" data-testid="scenario-headline-metrics">
            <BeforeAfter
              label="Critical zone, km2"
              before={result.critical_area_km2_before}
              after={result.critical_area_km2_after}
              digits={1}
              goodWhenUp={false}
            />
            <BeforeAfter
              label="Residents placed"
              before={result.placed_before}
              after={result.placed_after}
            />
            <BeforeAfter
              label="Effective capacity"
              before={result.effective_capacity_before}
              after={result.effective_capacity_after}
            />
            <BeforeAfter
              label="Routes above threshold"
              before={result.feasible_routes_before}
              after={result.feasible_routes_after}
            />
            <div className="flex flex-col gap-1">
              <span className="numeric text-[19px] leading-none text-[var(--color-ink)]">
                {num(result.elapsed_ms)} ms
              </span>
              <span className="text-[9px] uppercase tracking-[0.14em] text-[var(--color-ink-faint)]">
                Full chain re-run
              </span>
            </div>
          </div>
        ) : null}
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[338px_minmax(0,1fr)_390px]">
        <aside className="flex min-h-0 flex-col overflow-y-auto border-b border-[var(--color-line)] bg-[var(--color-surface)] px-4 pb-4 xl:border-b-0 xl:border-r">
          <div className="sticky top-0 -mx-4 bg-[var(--color-surface)] px-4 pb-2 pt-3">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={run}
                disabled={busy || changes.length === 0}
                data-testid="run-scenario"
                className="rounded-sm border px-3 py-1.5 text-[11px] disabled:opacity-40"
                style={{ borderColor: "var(--color-signal)", color: "var(--color-ink)" }}
              >
                {busy ? "Re-running the chain…" : "Run scenario"}
              </button>
              <button
                type="button"
                onClick={reset}
                className="rounded-sm px-2 py-1 text-[11px] text-[var(--color-ink-faint)] hover:text-[var(--color-ink)]"
              >
                Reset
              </button>
              <span className="numeric ml-auto text-[10px] text-[var(--color-ink-faint)]">
                {changes.length} change{changes.length === 1 ? "" : "s"}
              </span>
            </div>
          </div>

          <p className="mt-1 text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
            Presets
          </p>
          <div className="mt-1.5 flex flex-wrap gap-1">
            <Preset label="Monsoon escalation" onClick={() => applyPreset("monsoon")} />
            <Preset label="Bridge down" onClick={() => applyPreset("bridge")} />
            <Preset label="Site withdrawn" onClick={() => applyPreset("withdraw")} />
          </div>
          <p className="mt-1 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
            A preset only fills the controls below. Nothing is simulated until you
            run it.
          </p>

          <Group title="Hazard conditions">
            <Slider
              label="Rainfall intensity"
              value={rainfall}
              min={0.6}
              max={2.5}
              step={0.1}
              format={(v) => `x${v.toFixed(1)}`}
              onChange={setRainfall}
              note="Scales both the intensity surface and the count of extreme-rain days, then re-scores every hazard sub-model."
            />
            <Slider
              label="Landslide susceptibility shift"
              value={landslide}
              min={-0.4}
              max={0.4}
              step={0.05}
              format={(v) => (v >= 0 ? `+${v.toFixed(2)}` : v.toFixed(2))}
              onChange={setLandslide}
              note="Moves the terrain instability inputs, not the finished score, so the factor decomposition stays honest."
            />
          </Group>

          <Group title="Demand">
            <Slider
              label="Population"
              value={population}
              min={0.5}
              max={2.0}
              step={0.1}
              format={(v) => `x${v.toFixed(1)}`}
              onChange={setPopulation}
              note="Scales every habitation, and with it exposure, priority and the demand the optimiser has to place."
            />
          </Group>

          <Group title="Candidate sites">
            <ul className="flex flex-col gap-2">
              {capacity.sites.map((site) => {
                const edit = editOf(site.site_id);
                // Land and access are measured off the ground and the road
                // network. Neither is a supply anyone can deliver to a site, so
                // neither is offered as an upgrade.
                const upgrade = site.interventions.find((intervention) =>
                  UPGRADABLE.includes(intervention.service),
                );
                return (
                  <li
                    key={site.site_id}
                    className="rounded-sm border px-2 py-1.5"
                    style={{
                      borderColor: edit.withdrawn
                        ? "var(--color-critical)"
                        : edit.loss > 0 || edit.upgrade
                          ? "var(--color-warning)"
                          : "var(--color-line)",
                    }}
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-[11px] text-[var(--color-ink)]">
                        <span className="numeric">{site.site_id}</span>{" "}
                        <span className="text-[var(--color-ink-muted)]">{site.name}</span>
                      </span>
                      <span className="numeric text-[10px] text-[var(--color-ink-faint)]">
                        {num(site.effective_capacity)}
                      </span>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-1">
                      <Chip
                        label="Withdraw"
                        active={edit.withdrawn}
                        activeColour="var(--color-critical)"
                        onClick={() =>
                          setEdit(site.site_id, { withdrawn: !edit.withdrawn })
                        }
                      />
                      {[0.25, 0.5].map((loss) => (
                        <Chip
                          key={loss}
                          label={`-${loss * 100}% supply`}
                          active={!edit.withdrawn && edit.loss === loss}
                          disabled={edit.withdrawn}
                          activeColour="var(--color-warning)"
                          onClick={() =>
                            setEdit(site.site_id, {
                              loss: edit.loss === loss ? 0 : loss,
                            })
                          }
                        />
                      ))}
                      {upgrade ? (
                        <Chip
                          label={`+${num(upgrade.unit_size, upgrade.unit_size < 10 ? 2 : 0)} ${upgrade.unit}`}
                          title={upgrade.description}
                          active={Boolean(edit.upgrade)}
                          disabled={edit.withdrawn}
                          activeColour="var(--color-safe)"
                          onClick={() =>
                            setEdit(site.site_id, {
                              upgrade: edit.upgrade
                                ? null
                                : {
                                    service: upgrade.service,
                                    amount: upgrade.unit_size,
                                    unit: upgrade.unit,
                                  },
                            })
                          }
                        />
                      ) : null}
                    </div>
                    {upgrade && !edit.withdrawn ? (
                      <p className="mt-1 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
                        Bottleneck{" "}
                        {site.bottleneck
                          ? (SERVICE_LABEL[site.bottleneck] ?? site.bottleneck)
                          : "none"}
                        . The upgrade offered is the highest-ranked intervention the
                        capacity engine found for this site that can actually be
                        delivered to it.
                      </p>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </Group>

          <Group title="Close a road the plan depends on">
            <p className="text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
              {dependencies.note}
            </p>
            <ul className="mt-1.5 flex flex-col gap-1">
              {dependencies.segments.slice(0, 8).map((segment) => {
                const closed = closedSegments.includes(segment.segment_id);
                return (
                  <li key={segment.segment_id}>
                    <button
                      type="button"
                      data-testid="close-segment"
                      aria-pressed={closed}
                      onClick={() =>
                        setClosedSegments((current) =>
                          current.includes(segment.segment_id)
                            ? current.filter((id) => id !== segment.segment_id)
                            : [...current, segment.segment_id],
                        )
                      }
                      className="w-full rounded-sm border px-1.5 py-1 text-left hover:bg-[var(--color-surface-raised)]"
                      style={{
                        borderColor: closed
                          ? "var(--color-critical)"
                          : "var(--color-line)",
                      }}
                    >
                      <span className="flex items-baseline justify-between gap-2">
                        <span className="numeric text-[10px] text-[var(--color-ink)]">
                          {segment.name ?? segment.segment_id}
                          {segment.is_bridge ? " · bridge" : ""}
                        </span>
                        <span className="numeric text-[10px] text-[var(--color-ink-muted)]">
                          {num(segment.people_dependent)} carried
                        </span>
                      </span>
                      <span className="mt-0.5 block text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
                        {segment.consequence}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </Group>

          {changes.length > 0 ? (
            <Group title="This scenario">
              <ul className="flex flex-col gap-1 text-[10px] leading-relaxed text-[var(--color-ink-muted)]">
                {(result?.changes ?? []).length > 0
                  ? result!.changes.map((change, index) => (
                      <li key={`${change.kind}-${change.target}-${index}`}>
                        {change.description}
                        <span className="text-[var(--color-ink-faint)]">
                          {" "}
                          — {ENTRY_STAGE[change.kind] ?? "enters the chain"}
                        </span>
                      </li>
                    ))
                  : changes.map((change, index) => (
                      <li key={`${change.kind}-${change.target}-${index}`}>
                        {change.kind.replace(/_/g, " ").toLowerCase()}{" "}
                        <span className="numeric">{change.value}</span>
                        {change.target ? ` at ${change.target}` : ""}
                        <span className="text-[var(--color-ink-faint)]">
                          {" "}
                          — {ENTRY_STAGE[change.kind] ?? "enters the chain"}
                        </span>
                      </li>
                    ))}
              </ul>
            </Group>
          ) : (
            <p className="mt-3 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
              Move a control to build a scenario. Nothing is simulated until you
              run it.
            </p>
          )}

          {error ? (
            <p
              className="mt-3 text-[11px] text-[var(--color-critical)]"
              data-testid="scenario-error"
            >
              {error}
            </p>
          ) : null}

          {result ? (
            <p className="mt-3 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
              Stages:{" "}
              {Object.entries(result.stage_ms)
                .map(([stage, ms]) => `${stage} ${num(ms)} ms`)
                .join(" · ")}
            </p>
          ) : null}
        </aside>

        <section className="relative h-[68vh] min-h-[420px] xl:h-auto">
          <RiskMap
            studyArea={studyArea}
            terrainUrl={TERRAIN_PREVIEW_URL}
            overlayUrl={HAZARD_OVERLAY_URL}
            roadsUrl={ROADS_GEOJSON_URL}
            networkUrl={ROUTE_NETWORK_URL}
            drawnRoutes={drawnRoutes}
            closedSegments={closedSegments}
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
              {(["before", "after"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  data-testid={`map-view-${option}`}
                  disabled={option === "after" && !result}
                  aria-pressed={
                    result ? view === option : option === "before"
                  }
                  onClick={() => setView(option)}
                  className="rounded-sm border px-2 py-0.5 text-[10px] disabled:opacity-35"
                  style={{
                    borderColor:
                      (result ? view === option : option === "before")
                        ? "var(--color-signal)"
                        : "var(--color-line-strong)",
                    color:
                      (result ? view === option : option === "before")
                        ? "var(--color-ink)"
                        : "var(--color-ink-faint)",
                  }}
                >
                  {option === "before" ? "Baseline" : "Scenario"}
                </button>
              ))}
            </div>
            <p
              className="mt-1 text-[10px] text-[var(--color-ink-muted)]"
              data-testid="map-caption"
            >
              <span className="numeric">{num(shownZones.length)}</span> analytical
              zones ·{" "}
              <span className="numeric">{num(shownPlan.assignments.length)}</span>{" "}
              movements
            </p>
          </div>
          {closedSegments.length > 0 ? (
            <div className="pointer-events-none absolute bottom-2 left-2 z-10 rounded-sm border px-2.5 py-1.5"
              style={{
                borderColor: "var(--color-critical)",
                background: "var(--color-abyss)",
              }}
            >
              <p className="numeric text-[10px] text-[var(--color-critical)]">
                {closedSegments.length} segment(s) closed in this scenario
              </p>
            </div>
          ) : null}
        </section>

        <aside className="flex min-h-0 flex-col overflow-y-auto border-t border-[var(--color-line)] bg-[var(--color-surface)] xl:border-l xl:border-t-0">
          {!result ? (
            <p className="px-4 py-3 text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
              Run a scenario to see the difference it makes: red-zone area,
              habitations changing phase, capacity, routes and the plan itself,
              each as a before and after rather than a replaced number.
            </p>
          ) : (
            <div data-testid="scenario-diff">
              <Section title="Habitations changing phase">
                {result.tier_changes.length === 0 ? (
                  <p className="text-[11px] text-[var(--color-ink-muted)]">
                    No habitation changes phase under this scenario.
                  </p>
                ) : (
                  <ul className="flex flex-col gap-1.5">
                    {result.tier_changes.map((entry) => (
                      <li key={entry.habitation_id}>
                        <div className="flex items-baseline justify-between gap-2 text-[11px]">
                          <span className="text-[var(--color-ink)]">{entry.name}</span>
                          <span className="numeric text-[10px] text-[var(--color-ink-faint)]">
                            {num(entry.population)} residents
                          </span>
                        </div>
                        <p className="numeric mt-0.5 text-[10px] text-[var(--color-ink-muted)]">
                          {PHASE_LABEL[entry.phase_before] ?? entry.phase_before}{" "}
                          &rarr;{" "}
                          <span style={{ color: "var(--color-critical)" }}>
                            {PHASE_LABEL[entry.phase_after] ?? entry.phase_after}
                          </span>
                          {"  ·  priority "}
                          {num(entry.priority_before, 1)} &rarr;{" "}
                          {num(entry.priority_after, 1)} (
                          {delta(entry.priority_delta, 1)})
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
                {result.newly_immediate_population > 0 ? (
                  <p
                    className="mt-2 border-l-2 pl-2 text-[11px] leading-relaxed"
                    style={{
                      borderColor: "var(--color-critical)",
                      color: "var(--color-ink)",
                    }}
                  >
                    {num(result.newly_immediate_population)} residents newly require
                    immediate action.
                  </p>
                ) : null}
              </Section>

              <Section title="Priority movement">
                <DeltaTable
                  rows={result.habitations
                    .filter((entry) => Math.abs(entry.priority_delta) >= 0.05)
                    .slice(0, 8)
                    .map((entry) => ({
                      key: entry.habitation_id,
                      label: `${entry.name} (rank ${entry.rank_before}→${entry.rank_after})`,
                      before: entry.priority_before,
                      after: entry.priority_after,
                      digits: 1,
                      goodWhenUp: false,
                    }))}
                  empty="No habitation's priority score moves."
                />
              </Section>

              <Section title="Red-zone area, km2">
                <DeltaTable
                  rows={result.zones.map((zone) => ({
                    key: zone.zone_class,
                    label: zone.zone_class,
                    before: zone.area_km2_before,
                    after: zone.area_km2_after,
                    digits: 1,
                    goodWhenUp: false,
                  }))}
                  empty="No zone class changes area."
                />
              </Section>

              <Section title="Candidate sites">
                <ul className="flex flex-col gap-1.5">
                  {result.sites.map((site) => {
                    const lostGate =
                      site.suitable_before && !site.suitable_after;
                    const gained = site.failed_gates_before.filter(
                      (gate) => !site.failed_gates_after.includes(gate),
                    );
                    const lost = site.failed_gates_after.filter(
                      (gate) => !site.failed_gates_before.includes(gate),
                    );
                    return (
                      <li key={site.site_id}>
                        <div className="flex items-baseline justify-between gap-2 text-[10px]">
                          <span className="text-[var(--color-ink)]">
                            <span className="numeric">{site.site_id}</span>{" "}
                            {site.name}
                            {site.withdrawn ? (
                              <span style={{ color: "var(--color-critical)" }}>
                                {" "}
                                withdrawn
                              </span>
                            ) : null}
                          </span>
                          <span className="numeric text-right text-[var(--color-ink-muted)]">
                            {num(site.effective_before)}
                            <span className="mx-1 text-[var(--color-ink-faint)]">
                              &rarr;
                            </span>
                            <span
                              style={{
                                color: deltaColour(site.effective_delta, true),
                              }}
                            >
                              {num(site.effective_after)}
                            </span>
                          </span>
                        </div>
                        {lostGate ? (
                          <p
                            className="mt-0.5 text-[10px] leading-relaxed"
                            style={{ color: "var(--color-critical)" }}
                          >
                            Still has capacity, but no longer a candidate: now
                            fails {lost.length > 0 ? lost.join(", ") : "a hard gate"}.
                            A gate is a yes or no, so the capacity figure beside it
                            does not fall - the site simply stops being somewhere
                            anyone can be moved.
                          </p>
                        ) : null}
                        {!site.suitable_before && site.suitable_after ? (
                          <p
                            className="mt-0.5 text-[10px] leading-relaxed"
                            style={{ color: "var(--color-safe)" }}
                          >
                            Now clears every gate
                            {gained.length > 0
                              ? `; ${gained.join(", ")} no longer fails`
                              : ""}
                            .
                          </p>
                        ) : null}
                        {!lostGate &&
                        site.bottleneck_before !== site.bottleneck_after ? (
                          <p className="mt-0.5 text-[10px] text-[var(--color-ink-faint)]">
                            Binding constraint{" "}
                            {site.bottleneck_before
                              ? (SERVICE_LABEL[site.bottleneck_before] ??
                                site.bottleneck_before)
                              : "none"}{" "}
                            &rarr;{" "}
                            {site.bottleneck_after
                              ? (SERVICE_LABEL[site.bottleneck_after] ??
                                site.bottleneck_after)
                              : "none"}
                          </p>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              </Section>

              <Section title="Assignments that change">
                {result.assignments.length === 0 ? (
                  <p className="text-[11px] text-[var(--color-ink-muted)]">
                    The plan is unchanged.
                  </p>
                ) : (
                  <table className="w-full border-collapse text-left text-[10px] text-[var(--color-ink-muted)]">
                    <thead>
                      <tr className="text-[9px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)]">
                        <th className="pb-1 pr-2 font-medium">Movement</th>
                        <th className="pb-1 pr-2 font-medium">Phase</th>
                        <th className="pb-1 text-right font-medium">People</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.assignments.map((entry) => (
                        <tr
                          key={`${entry.habitation_id}-${entry.site_id}-${entry.phase}`}
                        >
                          <td className="numeric py-0.5 pr-2 text-[var(--color-ink)]">
                            {entry.habitation_id} &rarr; {entry.site_id}
                          </td>
                          <td className="py-0.5 pr-2">
                            {PHASE_LABEL[entry.phase] ?? entry.phase}
                          </td>
                          <td className="numeric py-0.5 text-right">
                            {num(entry.people_before)} &rarr;{" "}
                            <span style={{ color: deltaColour(entry.people_delta) }}>
                              {num(entry.people_after)}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                <p className="mt-2 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
                  Unplaced residents move {num(result.unmet_before)} &rarr;{" "}
                  <span style={{ color: deltaColour(result.unmet_after - result.unmet_before, false) }}>
                    {num(result.unmet_after)}
                  </span>
                  .
                </p>
              </Section>

              <Section title="Routes that change">
                {result.routes.length === 0 ? (
                  <p className="text-[11px] text-[var(--color-ink-muted)]">
                    No route reliability changes.
                  </p>
                ) : (
                  <>
                    <table className="w-full border-collapse text-left text-[10px] text-[var(--color-ink-muted)]">
                      <tbody>
                        {result.routes.slice(0, 10).map((entry) => (
                          <tr key={`${entry.habitation_id}-${entry.site_id}`}>
                            <td className="numeric py-0.5 pr-2 text-[var(--color-ink)]">
                              {entry.habitation_id} &rarr; {entry.site_id}
                            </td>
                            <td className="numeric py-0.5 pr-2 text-right">
                              {pct(entry.reliability_before)} &rarr;{" "}
                              <span
                                style={{
                                  color:
                                    entry.feasible_before && !entry.feasible_after
                                      ? "var(--color-critical)"
                                      : "var(--color-ink)",
                                }}
                              >
                                {pct(entry.reliability_after)}
                              </span>
                            </td>
                            <td className="numeric py-0.5 text-right">
                              {num(entry.travel_before_min)} &rarr;{" "}
                              {num(entry.travel_after_min)} min
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {result.routes.length > 10 ? (
                      <p className="mt-1 text-[10px] text-[var(--color-ink-faint)]">
                        {result.routes.length - 10} further routes changed.
                      </p>
                    ) : null}
                  </>
                )}
              </Section>

              <footer className="px-4 py-3 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
                Scenario{" "}
                <span className="numeric text-[var(--color-ink-muted)]">
                  {result.scenario.id}
                </span>{" "}
                · engine{" "}
                <span className="numeric text-[var(--color-ink-muted)]">
                  {result.engine_version}
                </span>{" "}
                · config{" "}
                <span className="numeric text-[var(--color-ink-muted)]">
                  {result.model_config_version}
                </span>
                <span className="mt-2 block">{result.scenario.disclaimer}</span>
                <span className="mt-2 block">{result.decision_authority}</span>
              </footer>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-4 border-t border-[var(--color-line)] pt-3">
      <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
        {title}
      </p>
      <div className="mt-1.5">{children}</div>
    </section>
  );
}

function Preset({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-sm border border-[var(--color-line-strong)] px-2 py-0.5 text-[10px] text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
    >
      {label}
    </button>
  );
}

function Chip({
  label,
  active,
  disabled,
  activeColour,
  title,
  onClick,
}: {
  label: string;
  active: boolean;
  disabled?: boolean;
  activeColour: string;
  title?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      title={title}
      className="numeric rounded-sm border px-1.5 py-0.5 text-[10px] disabled:opacity-35"
      style={{
        borderColor: active ? activeColour : "var(--color-line-strong)",
        color: active ? activeColour : "var(--color-ink-muted)",
      }}
    >
      {label}
    </button>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-b border-[var(--color-line)] px-4 py-3">
      <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
        {title}
      </p>
      <div className="mt-2">{children}</div>
    </div>
  );
}

function BeforeAfter({
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
      <span className="max-w-[160px] text-[9px] uppercase leading-tight tracking-[0.14em] text-[var(--color-ink-faint)]">
        {label}
      </span>
    </div>
  );
}

function DeltaTable({
  rows,
  empty,
}: {
  rows: {
    key: string;
    label: string;
    before: number;
    after: number;
    digits: number;
    goodWhenUp: boolean;
  }[];
  empty?: string;
}) {
  if (rows.length === 0) {
    return (
      <p className="text-[11px] text-[var(--color-ink-muted)]">
        {empty ?? "Nothing changes."}
      </p>
    );
  }
  return (
    <table className="w-full border-collapse text-left text-[10px] text-[var(--color-ink-muted)]">
      <tbody>
        {rows.map((row) => {
          const change = row.after - row.before;
          return (
            <tr key={row.key}>
              <td className="py-0.5 pr-2 text-[var(--color-ink)]">{row.label}</td>
              <td className="numeric py-0.5 text-right">
                {num(row.before, row.digits)}
                <span className="mx-1 text-[var(--color-ink-faint)]">&rarr;</span>
                <span style={{ color: deltaColour(change, row.goodWhenUp) }}>
                  {num(row.after, row.digits)}
                </span>
                <span
                  className="ml-1.5"
                  style={{ color: deltaColour(change, row.goodWhenUp) }}
                >
                  ({delta(change, row.digits)})
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
