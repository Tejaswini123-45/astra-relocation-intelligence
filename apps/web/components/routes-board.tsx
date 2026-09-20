"use client";

import type {
  CandidateSite,
  ClosureImpactResponse,
  Habitation,
  RouteAssessmentResponse,
  RouteMatrixRow,
  RoutePairResponse,
  StudyArea,
  ZoneFeature,
} from "@astra/contracts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type DrawnRoute, RiskMap } from "@/components/risk-map";
import {
  api,
  evaluateClosure,
  HAZARD_OVERLAY_URL,
  ROADS_GEOJSON_URL,
  ROUTE_NETWORK_URL,
  TERRAIN_PREVIEW_URL,
} from "@/lib/api";

const FASTEST_COLOUR: [number, number, number, number] = [231, 238, 247, 235];
const SAFEST_COLOUR: [number, number, number, number] = [86, 190, 172, 240];

function pct(value: number): string {
  return `${(value * 100).toFixed(0)}%`;
}

function num(value: number, digits = 0): string {
  return value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** Reliability to colour, on the same breaks the map uses. */
function reliabilityColour(reliability: number, threshold: number): string {
  if (reliability < threshold) return "var(--color-critical)";
  if (reliability < threshold + 0.15) return "var(--color-warning)";
  return "var(--color-safe)";
}

export function RoutesBoard({
  assessment,
  studyArea,
  zones,
  habitations,
  sites,
}: {
  assessment: RouteAssessmentResponse;
  studyArea: StudyArea;
  zones: ZoneFeature[];
  habitations: Habitation[];
  sites: CandidateSite[];
}) {
  const [closed, setClosed] = useState<string[]>([]);
  const [impact, setImpact] = useState<ClosureImpactResponse | null>(null);

  // One assessment feeds the header, the list and the site options. When a
  // closure has been evaluated that is the closed-network assessment; otherwise
  // it is the baseline. Mixing the two would put a badge from one beside a
  // number from the other.
  const shown = impact?.assessment ?? assessment;
  const threshold = shown.reliability_threshold;
  const byHabitation = useMemo(() => {
    const grouped = new Map<string, RouteMatrixRow[]>();
    for (const row of shown.rows) {
      const rows = grouped.get(row.habitation_id) ?? [];
      rows.push(row);
      grouped.set(row.habitation_id, rows);
    }
    return grouped;
  }, [shown.rows]);

  const ordered = useMemo(() => {
    const ids = Array.from(byHabitation.keys());
    return ids.sort((a, b) => {
      const bestA = byHabitation.get(a)![0].reliability;
      const bestB = byHabitation.get(b)![0].reliability;
      return bestA - bestB;
    });
  }, [byHabitation]);

  const [habitationId, setHabitationId] = useState(ordered[0] ?? "");
  const rows = byHabitation.get(habitationId) ?? [];
  const [siteId, setSiteId] = useState(rows[0]?.site_id ?? "");
  const [pair, setPair] = useState<RoutePairResponse | null>(null);
  const [loadingPair, setLoadingPair] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadPair = useCallback(async (habitation: string, site: string) => {
    setLoadingPair(true);
    setError(null);
    try {
      setPair(await api.routePair(habitation, site));
    } catch {
      setPair(null);
      setError(`The API did not return a route for ${habitation} to ${site}.`);
    } finally {
      setLoadingPair(false);
    }
  }, []);

  const selectHabitation = useCallback(
    (id: string) => {
      setHabitationId(id);
      const first = (byHabitation.get(id) ?? [])[0];
      if (first) {
        setSiteId(first.site_id);
        void loadPair(id, first.site_id);
      }
    },
    [byHabitation, loadPair],
  );

  const selectSite = useCallback(
    (id: string) => {
      setSiteId(id);
      void loadPair(habitationId, id);
    },
    [habitationId, loadPair],
  );

  const toggleClosed = useCallback((segmentId: string) => {
    setImpact(null);
    setClosed((current) =>
      current.includes(segmentId)
        ? current.filter((id) => id !== segmentId)
        : [...current, segmentId],
    );
  }, []);

  const runClosure = useCallback(async () => {
    setEvaluating(true);
    setError(null);
    try {
      setImpact(await evaluateClosure(closed));
    } catch {
      setImpact(null);
      setError("The API did not return a closure assessment.");
    } finally {
      setEvaluating(false);
    }
  }, [closed]);

  const openedWith = useRef<string | null>(null);
  useEffect(() => {
    if (!habitationId || !siteId) return;
    const key = `${habitationId}/${siteId}`;
    if (openedWith.current === key) return;
    openedWith.current = key;
    void loadPair(habitationId, siteId);
  }, [habitationId, siteId, loadPair]);

  const drawnRoutes: DrawnRoute[] = useMemo(() => {
    if (!pair) return [];
    const routes: DrawnRoute[] = [];
    if (pair.profiles_differ) {
      routes.push({
        id: "fastest",
        geometry: pair.fastest.geometry,
        colour: FASTEST_COLOUR,
        width: 55,
      });
    }
    routes.push({
      id: "safest",
      geometry: pair.safest.geometry,
      colour: SAFEST_COLOUR,
      width: 75,
    });
    return routes;
  }, [pair]);

  const blocked = new Set(shown.route_blocked_habitations);

  const focusBounds = useMemo(() => {
    const points = pair?.safest.geometry ?? [];
    if (points.length < 2) return null;
    const lons = points.map((point) => point[0]);
    const lats = points.map((point) => point[1]);
    return [
      [Math.min(...lons), Math.min(...lats)],
      [Math.max(...lons), Math.max(...lats)],
    ] as [[number, number], [number, number]];
  }, [pair]);

  return (
    <div className="flex h-[calc(100vh-49px)] flex-col">
      <header className="flex flex-wrap items-start justify-between gap-6 border-b border-[var(--color-line)] bg-[var(--color-surface)] px-5 py-3">
        <div className="max-w-xl">
          <p className="text-[10px] uppercase tracking-[0.18em] text-[var(--color-ink-faint)]">
            Access &amp; route survivability
          </p>
          <h1 className="mt-1 text-[17px] font-semibold text-[var(--color-ink)]">
            Can these people reach that site
          </h1>
          <p className="mt-1 text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
            Route reliability is the product of every segment surviving. It is a
            survivability figure, not a confidence in the estimate. Below{" "}
            <span className="numeric">{pct(threshold)}</span> a site is treated as
            unreachable from a habitation rather than merely penalised.
          </p>
        </div>
        <div className="flex flex-wrap gap-7">
          <Stat
            value={`${shown.feasible_pairs} / ${shown.pairs_evaluated}`}
            label="Routes above threshold"
          />
          <Stat
            value={`${shown.habitations_with_a_reachable_suitable_site} / ${ordered.length}`}
            label="Habitations with a reachable, suitable site"
          />
          <Stat
            value={num(shown.network.total_length_km)}
            label="km of routed road"
          />
          <Stat
            value={pct(shown.network.share_without_alternative)}
            label="Road with no alternative"
            tone="var(--color-warning)"
          />
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[300px_minmax(0,1fr)_360px]">
        <aside className="flex min-h-0 flex-col border-r border-[var(--color-line)] bg-[var(--color-surface)]">
          <div className="border-b border-[var(--color-line)] px-3 py-2">
            <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
              Habitations, worst access first
            </p>
          </div>
          <ul className="min-h-0 flex-1 overflow-y-auto divide-y divide-[var(--color-line)]">
            {ordered.map((id) => {
              const options = byHabitation.get(id)!;
              const best = options[0];
              const isBlocked = blocked.has(id);
              return (
                <li key={id}>
                  <button
                    type="button"
                    onClick={() => selectHabitation(id)}
                    aria-pressed={id === habitationId}
                    className="w-full px-3 py-2.5 text-left hover:bg-[var(--color-surface-raised)]"
                    style={{
                      background:
                        id === habitationId ? "var(--color-surface-raised)" : undefined,
                    }}
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-[12px] text-[var(--color-ink)]">
                        {best.habitation_name}
                      </span>
                      <span
                        className="numeric text-[12px]"
                        style={{ color: reliabilityColour(best.reliability, threshold) }}
                      >
                        {pct(best.reliability)}
                      </span>
                    </div>
                    <div className="mt-0.5 flex items-center justify-between gap-2 text-[10px] text-[var(--color-ink-faint)]">
                      <span className="numeric">
                        {id} &middot; {num(best.population)} residents
                      </span>
                      {isBlocked ? (
                        <span style={{ color: "var(--color-critical)" }}>
                          route blocked
                        </span>
                      ) : (
                        <span className="numeric">
                          best {num(best.travel_time_min)} min
                        </span>
                      )}
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
          <footer className="border-t border-[var(--color-line)] px-3 py-2 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
            &ldquo;Route blocked&rdquo; means no site that passes its suitability
            gates can be reached above the reliability threshold. It is a finding,
            not a missing result.
          </footer>
        </aside>

        <section className="relative min-h-0">
          <RiskMap
            studyArea={studyArea}
            terrainUrl={TERRAIN_PREVIEW_URL}
            overlayUrl={HAZARD_OVERLAY_URL}
            roadsUrl={ROADS_GEOJSON_URL}
            networkUrl={ROUTE_NETWORK_URL}
            drawnRoutes={drawnRoutes}
            closedSegments={closed}
            onSelectSegment={toggleClosed}
            zones={zones}
            habitations={habitations}
            sites={sites}
            toggles={{
              hazard: true,
              zones: false,
              habitations: true,
              sites: true,
              roads: false,
              network: true,
            }}
            hazardOpacity={0.32}
            selected={null}
            onSelectPoint={() => undefined}
            onSelectZone={() => undefined}
            highlightId={habitationId}
            focusBounds={focusBounds}
          />
          <div className="pointer-events-none absolute left-2 top-2 z-10 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-sm border border-[var(--color-line)] bg-[var(--color-abyss)] px-2.5 py-1.5">
            <span className="text-[9px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
              Segment failure probability
            </span>
            {[
              { colour: "#569e94", label: "<1.5%" },
              { colour: "#c1a54f", label: "1.5-4%" },
              { colour: "#ce7a3a", label: "4-8%" },
              { colour: "#c94238", label: "8%+" },
            ].map((entry) => (
              <span key={entry.label} className="flex items-center gap-1.5">
                <span
                  className="inline-block h-0.5 w-5"
                  style={{ background: entry.colour }}
                />
                <span className="numeric text-[10px] text-[var(--color-ink-muted)]">
                  {entry.label}
                </span>
              </span>
            ))}
            <span className="flex items-center gap-1.5">
              <span
                className="inline-block h-1 w-5 rounded-full"
                style={{ background: "rgb(86,190,172)" }}
              />
              <span className="text-[10px] text-[var(--color-ink-muted)]">safest</span>
            </span>
            {pair?.profiles_differ ? (
              <span className="flex items-center gap-1.5">
                <span
                  className="inline-block h-1 w-5 rounded-full"
                  style={{ background: "rgb(231,238,247)" }}
                />
                <span className="text-[10px] text-[var(--color-ink-muted)]">
                  fastest
                </span>
              </span>
            ) : null}
            <span className="text-[10px] text-[var(--color-ink-faint)]">
              Thicker lines carry a bridge. Click a segment to close it.
            </span>
          </div>
        </section>

        <aside className="flex min-h-0 flex-col overflow-y-auto border-l border-[var(--color-line)] bg-[var(--color-surface)]">
          <div className="border-b border-[var(--color-line)] px-4 py-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
              Sites reachable from this habitation
            </p>
            <ul className="mt-2 flex flex-col gap-1">
              {rows.map((row) => (
                <li key={row.site_id}>
                  <button
                    type="button"
                    onClick={() => selectSite(row.site_id)}
                    aria-pressed={row.site_id === siteId}
                    className="grid w-full grid-cols-[1fr_auto_auto] items-baseline gap-2 rounded-sm px-2 py-1.5 text-left hover:bg-[var(--color-surface-raised)]"
                    style={{
                      background:
                        row.site_id === siteId
                          ? "var(--color-surface-raised)"
                          : undefined,
                    }}
                  >
                    <span className="text-[11px] text-[var(--color-ink)]">
                      {row.site_name}
                      {!row.site_suitable ? (
                        <span
                          className="ml-1.5 text-[9px] uppercase"
                          style={{ color: "var(--color-critical)" }}
                        >
                          gate failed
                        </span>
                      ) : null}
                    </span>
                    <span className="numeric text-[10px] text-[var(--color-ink-faint)]">
                      {num(row.travel_time_min)} min
                    </span>
                    <span
                      className="numeric w-10 text-right text-[11px]"
                      style={{ color: reliabilityColour(row.reliability, threshold) }}
                    >
                      {pct(row.reliability)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>

          {error ? (
            <p className="border-b border-[var(--color-line)] px-4 py-3 text-[11px] text-[var(--color-critical)]">
              {error}
            </p>
          ) : null}

          {loadingPair ? (
            <p className="px-4 py-3 text-[11px] text-[var(--color-ink-faint)]">
              Routing&hellip;
            </p>
          ) : pair ? (
            <RouteDetail
              pair={pair}
              threshold={threshold}
              onClose={toggleClosed}
              closed={closed}
            />
          ) : (
            <p className="px-4 py-3 text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
              Select a habitation to route it to every candidate site.
            </p>
          )}

          <ClosurePanel
            closed={closed}
            impact={impact}
            evaluating={evaluating}
            onRun={runClosure}
            onClear={() => {
              setClosed([]);
              setImpact(null);
            }}
            onRemove={toggleClosed}
          />

          <footer className="mt-auto border-t border-[var(--color-line)] px-4 py-3 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
            {shown.network.redundancy_note}
            <span className="mt-2 block">{shown.decision_authority}</span>
          </footer>
        </aside>
      </div>
    </div>
  );
}

function Stat({
  value,
  label,
  tone,
}: {
  value: string;
  label: string;
  tone?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span
        className="numeric text-[19px] leading-none"
        style={{ color: tone ?? "var(--color-ink)" }}
      >
        {value}
      </span>
      <span className="max-w-[170px] text-[9px] uppercase leading-tight tracking-[0.14em] text-[var(--color-ink-faint)]">
        {label}
      </span>
    </div>
  );
}

function RouteDetail({
  pair,
  threshold,
  onClose,
  closed,
}: {
  pair: RoutePairResponse;
  threshold: number;
  onClose: (segmentId: string) => void;
  closed: string[];
}) {
  const route = pair.safest;
  return (
    <div className="border-b border-[var(--color-line)] px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
            {pair.origin_id} &rarr; {pair.destination_id}
            {closed.length > 0 ? (
              <span
                className="ml-2 normal-case tracking-normal"
                style={{ color: "var(--color-warning)" }}
              >
                open network
              </span>
            ) : null}
          </p>
          <p className="numeric mt-1 text-[11px] text-[var(--color-ink-muted)]">
            {num(route.distance_km, 1)} km &middot; {num(route.travel_time_min)} min
            &middot; {route.legs.length} segments
          </p>
        </div>
        <div className="text-right">
          <p
            className="numeric text-[24px] leading-none"
            style={{ color: reliabilityColour(route.reliability, threshold) }}
          >
            {pct(route.reliability)}
          </p>
          <p className="text-[9px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)]">
            reliability
          </p>
        </div>
      </div>

      {route.infeasible_reason ? (
        <p
          className="mt-2 rounded-sm border px-2.5 py-2 text-[11px] leading-relaxed"
          style={{
            borderColor: "var(--color-critical)",
            background: "color-mix(in srgb, var(--color-critical) 12%, transparent)",
            color: "var(--color-ink)",
          }}
        >
          {route.infeasible_reason}
        </p>
      ) : null}

      <p className="mt-2 text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
        {pair.tradeoff}
      </p>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-[10px] text-[var(--color-ink-muted)]">
        <dt>Walk to and from the road</dt>
        <dd className="numeric text-right">
          {num(route.off_network_m)} m &middot; {num(route.off_network_min)} min
        </dd>
        <dt>Hazard-exposed road</dt>
        <dd className="numeric text-right">{num(route.hazard_exposed_km, 1)} km</dd>
        <dt>Longest continuous exposure</dt>
        <dd className="numeric text-right">
          {num(route.longest_hazard_run_km, 1)} km
        </dd>
        <dt>Bridges and culverts crossed</dt>
        <dd className="numeric text-right">{route.bridges_crossed}</dd>
      </dl>

      {route.scored_share < 0.995 ? (
        <p className="mt-2 border-l-2 border-[var(--color-neutral)] pl-2 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
          <span className="numeric">{pct(route.scored_share)}</span> of this route
          runs over ground ASTRA scored. The remainder leaves the study area, and
          its risk was estimated from the part inside it.
        </p>
      ) : null}

      <p className="mt-3 text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
        Points of failure on this route
      </p>
      {route.points_of_failure.length === 0 ? (
        <p className="mt-1 text-[10px] text-[var(--color-ink-muted)]">
          None above the concern threshold.
        </p>
      ) : (
        <ul className="mt-1.5 flex flex-col gap-1">
          {route.points_of_failure.slice(0, 6).map((point) => (
            <li key={point.segment_id}>
              <button
                type="button"
                onClick={() => onClose(point.segment_id)}
                className="w-full rounded-sm px-1.5 py-1 text-left hover:bg-[var(--color-surface-raised)]"
                style={{
                  background: closed.includes(point.segment_id)
                    ? "color-mix(in srgb, var(--color-warning) 18%, transparent)"
                    : undefined,
                }}
              >
                <span className="flex items-baseline justify-between gap-2">
                  <span className="numeric text-[10px] text-[var(--color-ink)]">
                    {point.name ?? point.segment_id}
                  </span>
                  <span
                    className="numeric text-[10px]"
                    style={{ color: "var(--color-warning)" }}
                  >
                    {(point.p_fail * 100).toFixed(1)}%
                  </span>
                </span>
                <span className="mt-0.5 block text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
                  {point.reason}
                  {closed.includes(point.segment_id) ? " — closed in what-if" : ""}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ClosurePanel({
  closed,
  impact,
  evaluating,
  onRun,
  onClear,
  onRemove,
}: {
  closed: string[];
  impact: ClosureImpactResponse | null;
  evaluating: boolean;
  onRun: () => void;
  onClear: () => void;
  onRemove: (segmentId: string) => void;
}) {
  return (
    <div className="border-b border-[var(--color-line)] px-4 py-3">
      <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
        What if these roads close
      </p>
      {closed.length === 0 ? (
        <p className="mt-1.5 text-[10px] leading-relaxed text-[var(--color-ink-muted)]">
          Click a road on the map, or a point of failure above, to close it. The
          corridor is re-routed against the open network and the difference is
          reported.
        </p>
      ) : (
        <ul className="mt-1.5 flex flex-wrap gap-1">
          {closed.map((segmentId) => (
            <li key={segmentId}>
              <button
                type="button"
                onClick={() => onRemove(segmentId)}
                className="numeric rounded-sm border px-1.5 py-0.5 text-[10px]"
                style={{
                  borderColor: "var(--color-warning)",
                  color: "var(--color-warning)",
                }}
                title="Reopen this segment"
              >
                {segmentId} &times;
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-2.5 flex gap-2">
        <button
          type="button"
          onClick={onRun}
          disabled={closed.length === 0 || evaluating}
          className="rounded-sm border px-2.5 py-1 text-[11px] disabled:opacity-40"
          style={{ borderColor: "var(--color-line-strong)", color: "var(--color-ink)" }}
        >
          {evaluating ? "Re-routing…" : "Evaluate closure"}
        </button>
        {closed.length > 0 ? (
          <button
            type="button"
            onClick={onClear}
            className="rounded-sm px-2 py-1 text-[11px] text-[var(--color-ink-faint)] hover:text-[var(--color-ink)]"
          >
            Reopen all
          </button>
        ) : null}
      </div>

      {impact ? (
        <div className="mt-3">
          <p className="text-[11px] leading-relaxed text-[var(--color-ink)]">
            {impact.headline}
          </p>
          {impact.changed.length > 0 ? (
            <table className="mt-2 w-full border-collapse text-left text-[10px] text-[var(--color-ink-muted)]">
              <thead>
                <tr className="text-[9px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)]">
                  <th className="pb-1 pr-2 font-medium">Pair</th>
                  <th className="pb-1 pr-2 text-right font-medium">Reliability</th>
                  <th className="pb-1 text-right font-medium">Minutes</th>
                </tr>
              </thead>
              <tbody>
                {impact.changed.slice(0, 8).map((row) => (
                  <tr key={`${row.habitation_id}-${row.site_id}`}>
                    <td className="numeric py-0.5 pr-2 text-[var(--color-ink)]">
                      {row.habitation_id} &rarr; {row.site_id}
                    </td>
                    <td className="numeric py-0.5 pr-2 text-right">
                      {pct(row.reliability_before)} &rarr;{" "}
                      <span
                        style={{
                          color:
                            row.feasible_before && !row.feasible_after
                              ? "var(--color-critical)"
                              : "var(--color-ink)",
                        }}
                      >
                        {pct(row.reliability_after)}
                      </span>
                    </td>
                    <td className="numeric py-0.5 text-right">
                      {num(row.travel_time_before_min)} &rarr;{" "}
                      {num(row.travel_time_after_min)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {impact.changed.length > 8 ? (
            <p className="mt-1 text-[10px] text-[var(--color-ink-faint)]">
              {impact.changed.length - 8} further routes changed.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
