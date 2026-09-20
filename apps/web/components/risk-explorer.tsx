"use client";

import type {
  CandidateSite,
  FactorContribution,
  HabitationHazardResponse,
  HabitationHazardRow,
  HazardScore,
  RiskCellResponse,
  RiskSummaryResponse,
  ZoneFeature,
} from "@astra/contracts";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ProvenanceChip } from "@/components/primitives";
import { RiskMap, type LayerToggles } from "@/components/risk-map";
import {
  api,
  API_BASE,
  CONFIDENCE_OVERLAY_URL,
  HAZARD_OVERLAY_URL,
  ROADS_GEOJSON_URL,
} from "@/lib/api";

const SEVERITY_COLOUR: Record<string, string> = {
  CRITICAL: "var(--color-severity-critical)",
  ELEVATED: "var(--color-severity-elevated)",
  WATCH: "var(--color-severity-watch)",
  LOW: "var(--color-severity-low)",
};

const HAZARD_LABEL: Record<string, string> = {
  LANDSLIDE: "Landslide",
  FLOOD: "Flood",
  CLOUDBURST: "Cloudburst / flash flood",
  COASTAL_EROSION: "Coastal erosion",
};

/**
 * Display labels for factor identifiers. Presentation only: the identifier is
 * kept in the cell's title attribute so nothing is hidden, and no value changes.
 */
const FACTOR_LABEL: Record<string, string> = {
  extreme_rainfall_frequency: "heavy-rain days",
  confluence_density: "confluences",
  catchment_steepness: "catchment slope",
  drainage_distance: "channel distance",
  drainage_density: "channel density",
  incident_density: "incident density",
  rainfall_intensity: "rainfall intensity",
  upstream_area: "upstream area",
  landcover: "land cover",
  hand: "height above drainage",
};

function fixed(value: number, digits = 1): string {
  return value.toFixed(digits);
}

function FactorBar({ factor, maximum }: { factor: FactorContribution; maximum: number }) {
  // The bar sits behind the contribution figure rather than in its own column,
  // so the arithmetic stays readable in a narrow panel.
  const share = maximum > 0 ? Math.min(factor.contribution / maximum, 1) : 0;
  return (
    <tr className="align-middle">
      <td className="py-1 pr-2 text-[var(--color-ink)]" title={factor.factor}>
        {FACTOR_LABEL[factor.factor] ?? factor.factor}
        {factor.unit ? (
          <span className="ml-1 text-[9px] text-[var(--color-ink-faint)]">
            {factor.unit}
          </span>
        ) : null}
      </td>
      <td className="numeric whitespace-nowrap py-1 pr-2 text-right">
        {factor.raw_value === null || factor.raw_value === undefined
          ? "-"
          : fixed(factor.raw_value, 2)}
      </td>
      <td className="numeric py-1 pr-2 text-right">{fixed(factor.normalised_value, 3)}</td>
      <td className="numeric py-1 pr-2 text-right text-[var(--color-ink-faint)]">
        x{fixed(factor.weight, 2)}
      </td>
      <td className="py-1">
        <span
          className="numeric relative block rounded-sm px-1.5 py-0.5 text-right text-[var(--color-ink)]"
          style={{
            background: `linear-gradient(to left, color-mix(in srgb, var(--color-signal) 34%, transparent) ${(
              share * 100
            ).toFixed(0)}%, transparent ${(share * 100).toFixed(0)}%)`,
          }}
        >
          {fixed(factor.contribution, 3)}
        </span>
      </td>
    </tr>
  );
}

function HazardBlock({ score }: { score: HazardScore }) {
  const maximum = Math.max(...score.factors.map((factor) => factor.contribution), 0.0001);
  return (
    <div className="border-t border-[var(--color-line)] px-4 py-3">
      <div className="flex items-baseline justify-between">
        <span className="text-[12px] text-[var(--color-ink)]">
          {HAZARD_LABEL[score.hazard] ?? score.hazard}
        </span>
        <span className="numeric text-[15px] text-[var(--color-ink)]">
          {fixed(score.score, 1)}
          <span className="ml-1 text-[10px] text-[var(--color-ink-faint)]">/ 100</span>
        </span>
      </div>
      <table className="mt-2 w-full border-collapse text-left text-[11px] text-[var(--color-ink-muted)]">
        <thead>
          <tr className="text-[9px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)]">
            <th className="pb-1 pr-2 font-medium">Factor</th>
            <th className="pb-1 pr-2 text-right font-medium">Measured</th>
            <th className="pb-1 pr-2 text-right font-medium">Norm.</th>
            <th className="pb-1 pr-2 text-right font-medium">Weight</th>
            <th className="pb-1 text-right font-medium">Contribution</th>
          </tr>
        </thead>
        <tbody>
          {score.factors.map((factor) => (
            <FactorBar key={factor.factor} factor={factor} maximum={maximum} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CellPanel({ cell }: { cell: RiskCellResponse }) {
  const hazard = cell.hazard;
  return (
    <div className="flex flex-col">
      <div className="px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-[10px] uppercase tracking-[0.14em] text-[var(--color-ink-faint)]">
              {hazard.classification_label}
            </p>
            <p className="mt-1 flex items-baseline gap-2">
              <span
                className="numeric text-[30px] leading-none"
                style={{ color: SEVERITY_COLOUR[hazard.zone_class] }}
              >
                {fixed(hazard.composite, 1)}
              </span>
              <span className="text-[11px] text-[var(--color-ink-faint)]">composite / 100</span>
            </p>
            <p className="mt-2 text-[12px] text-[var(--color-ink-muted)]">
              Dominant hazard:{" "}
              <span className="text-[var(--color-ink)]">
                {HAZARD_LABEL[hazard.dominant_hazard] ?? hazard.dominant_hazard}
              </span>
              {hazard.second_hazard ? (
                <>
                  {" "}
                  &middot; second:{" "}
                  <span className="text-[var(--color-ink)]">
                    {HAZARD_LABEL[hazard.second_hazard] ?? hazard.second_hazard}
                  </span>
                </>
              ) : null}
            </p>
          </div>
          <div className="text-right">
            <span
              className="numeric inline-block rounded-sm border px-2 py-1 text-[10px] uppercase tracking-[0.1em]"
              style={{
                borderColor: SEVERITY_COLOUR[hazard.zone_class],
                color: SEVERITY_COLOUR[hazard.zone_class],
              }}
            >
              {hazard.zone_class}
            </span>
            <p className="mt-2 text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
              Evidence confidence
            </p>
            <p className="numeric text-[13px] text-[var(--color-ink)]">
              {cell.confidence.band} &middot; {fixed(cell.confidence.value, 2)}
            </p>
          </div>
        </div>
        <p className="mt-3 border-l-2 border-[var(--color-neutral)] pl-3 text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
          {cell.confidence.note}
        </p>
        <p className="numeric mt-3 text-[10px] text-[var(--color-ink-faint)]">
          {cell.lat.toFixed(5)}, {cell.lon.toFixed(5)} &middot; cell r{cell.row} c{cell.col}
          {cell.zone_id ? ` · zone ${cell.zone_id}` : " · outside any published zone"}
        </p>
      </div>

      <div className="border-t border-[var(--color-line)] bg-[var(--color-surface-inset)] px-4 py-2">
        <p className="numeric text-[10px] text-[var(--color-ink-faint)]">
          {cell.composite_formula.expression}
        </p>
        <p className="numeric mt-1 text-[10px] text-[var(--color-ink-faint)]">
          {cell.formula.formula_id} v{cell.formula.version} &middot; engine{" "}
          {cell.engine_version} &middot; config {cell.model_config_version}
        </p>
      </div>

      {hazard.per_hazard.map((score) => (
        <HazardBlock key={score.hazard} score={score} />
      ))}
    </div>
  );
}

export function RiskExplorer({
  summary,
  zones,
  habitationHazards,
  sites,
}: {
  summary: RiskSummaryResponse;
  zones: ZoneFeature[];
  habitationHazards: HabitationHazardResponse;
  sites: CandidateSite[];
}) {
  const [toggles, setToggles] = useState<LayerToggles>({
    hazard: true,
    zones: true,
    habitations: true,
    sites: true,
    roads: true,
    confidence: false,
  });
  const [hazardOpacity, setHazardOpacity] = useState(0.55);
  const [selected, setSelected] = useState<{ lon: number; lat: number } | null>(null);
  const [cell, setCell] = useState<RiskCellResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  const habitations = useMemo(
    () =>
      habitationHazards.habitations.map((row) => ({
        id: row.habitation_id,
        name: row.name,
        centroid: row.centroid,
        population: row.population,
      })),
    [habitationHazards],
  );

  const onSelectPoint = useCallback((lon: number, lat: number) => {
    setSelected({ lon, lat });
  }, []);

  const onSelectZone = useCallback((zoneId: string | null) => {
    if (!zoneId) return;
  }, []);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setLoading(true);
    setFailed(false);
    api
      .riskCell(selected.lon, selected.lat)
      .then((response) => {
        if (!cancelled) setCell(response);
      })
      .catch(() => {
        if (!cancelled) {
          setCell(null);
          setFailed(true);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  const classShare = summary.class_share_percent;

  return (
    <div className="flex h-[calc(100vh-64px)] flex-col xl:flex-row">
      <aside className="w-full shrink-0 overflow-y-auto border-b border-[var(--color-line)] bg-[var(--color-surface)] xl:w-64 xl:border-b-0 xl:border-r">
        <div className="border-b border-[var(--color-line)] px-4 py-3">
          <h2 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink)]">
            Layers
          </h2>
          <ul className="mt-2 flex flex-col gap-1.5">
            {(
              [
                ["hazard", "Multi-hazard composite"],
                ["zones", "Red zones"],
                ["habitations", "Habitations"],
                ["sites", "Candidate sites"],
                ["roads", "Roads and waterways"],
                ["confidence", "Evidence confidence (hatched)"],
              ] as [keyof LayerToggles, string][]
            ).map(([key, label]) => (
              <li key={key}>
                <label className="flex cursor-pointer items-center gap-2 text-[12px] text-[var(--color-ink-muted)]">
                  <input
                    type="checkbox"
                    checked={toggles[key]}
                    onChange={(event) =>
                      setToggles((current) => ({ ...current, [key]: event.target.checked }))
                    }
                    className="h-3.5 w-3.5 accent-[var(--color-signal)]"
                  />
                  {label}
                </label>
              </li>
            ))}
          </ul>
          <label className="mt-3 block text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
            Hazard opacity
            <input
              type="range"
              min={0}
              max={100}
              value={Math.round(hazardOpacity * 100)}
              onChange={(event) => setHazardOpacity(Number(event.target.value) / 100)}
              className="mt-1 w-full accent-[var(--color-signal)]"
            />
          </label>
        </div>

        <div className="border-b border-[var(--color-line)] px-4 py-3">
          <h2 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink)]">
            Corridor classification
          </h2>
          <ul className="mt-2 flex flex-col gap-1.5 text-[11px]">
            {(["CRITICAL", "ELEVATED", "WATCH", "LOW"] as const).map((zoneClass) => (
              <li key={zoneClass} className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-2 text-[var(--color-ink-muted)]">
                  <span
                    aria-hidden
                    className="inline-block h-2 w-2 rounded-[1px]"
                    style={{ background: SEVERITY_COLOUR[zoneClass] }}
                  />
                  {zoneClass}
                </span>
                <span className="numeric text-[var(--color-ink)]">
                  {fixed(classShare[zoneClass] ?? 0, 1)}%
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
            Thresholds: Critical {summary.zone_thresholds.CRITICAL}, Elevated{" "}
            {summary.zone_thresholds.ELEVATED}, Watch {summary.zone_thresholds.WATCH}.
            Composite adds {summary.composite_lambda} of the second hazard.
          </p>
        </div>

        <div className="px-4 py-3">
          <h2 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink)]">
            Habitations by hazard
          </h2>
          <ul className="mt-2 flex flex-col">
            {habitationHazards.habitations.map((row: HabitationHazardRow) => (
              <li key={row.habitation_id}>
                <button
                  type="button"
                  onClick={() => onSelectPoint(row.centroid.lon, row.centroid.lat)}
                  className="flex w-full items-center justify-between gap-2 rounded px-1.5 py-1 text-left text-[11px] text-[var(--color-ink-muted)] hover:bg-[var(--color-surface-raised)]"
                >
                  <span className="truncate">
                    <span className="numeric text-[var(--color-ink-faint)]">
                      {row.habitation_id}
                    </span>{" "}
                    {row.name}
                  </span>
                  <span
                    className="numeric shrink-0"
                    style={{ color: SEVERITY_COLOUR[row.hazard.zone_class] }}
                  >
                    {fixed(row.hazard.composite, 0)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </aside>

      <div className="relative min-h-[420px] flex-1">
        <RiskMap
          studyArea={summary.study_area}
          terrainUrl={`${API_BASE}${summary.terrain_url}`}
          overlayUrl={HAZARD_OVERLAY_URL}
          roadsUrl={ROADS_GEOJSON_URL}
          confidenceUrl={CONFIDENCE_OVERLAY_URL}
          zones={toggles.zones ? zones : []}
          habitations={habitations as never}
          sites={sites}
          toggles={toggles}
          hazardOpacity={hazardOpacity}
          selected={selected}
          onSelectPoint={onSelectPoint}
          onSelectZone={onSelectZone}
        />
      </div>

      <aside className="w-full shrink-0 overflow-y-auto border-t border-[var(--color-line)] bg-[var(--color-surface)] xl:w-[430px] xl:border-l xl:border-t-0">
        <header className="border-b border-[var(--color-line)] px-4 py-3">
          <h2 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink)]">
            Decision intelligence
          </h2>
          <p className="mt-1 text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
            Click anywhere on the map to take that location apart: every hazard, every
            factor, every weight.
          </p>
        </header>

        {!selected ? (
          <div className="px-4 py-4">
            <p className="text-[12px] text-[var(--color-ink-muted)]">
              Nothing selected. The corridor summary below is computed over{" "}
              <span className="numeric">{summary.grid_rows * summary.grid_cols}</span> cells
              of {fixed(summary.cell_x_m, 0)} x {fixed(summary.cell_y_m, 0)} m.
            </p>
            <table className="mt-3 w-full border-collapse text-left text-[11px] text-[var(--color-ink-muted)]">
              <thead>
                <tr className="text-[9px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)]">
                  <th className="pb-1 pr-3 font-medium">Surface</th>
                  <th className="pb-1 pr-3 text-right font-medium">Mean</th>
                  <th className="pb-1 pr-3 text-right font-medium">p95</th>
                  <th className="pb-1 text-right font-medium">Max</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(summary.hazard_statistics).map(([key, stats]) => (
                  <tr key={key}>
                    <td className="py-1 pr-3 text-[var(--color-ink)]">
                      {HAZARD_LABEL[key] ?? key}
                    </td>
                    <td className="numeric py-1 pr-3 text-right">{fixed(stats.mean, 1)}</td>
                    <td className="numeric py-1 pr-3 text-right">{fixed(stats.p95, 1)}</td>
                    <td className="numeric py-1 text-right">{fixed(stats.max, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <dl className="mt-4 grid grid-cols-2 gap-y-1 text-[11px] text-[var(--color-ink-muted)]">
              <dt>Incidents used</dt>
              <dd className="numeric text-right text-[var(--color-ink)]">
                {summary.incidents_used}
              </dd>
              <dt>Excluded, location too coarse</dt>
              <dd className="numeric text-right">{summary.incidents_excluded}</dd>
              <dt>Rainfall grid points</dt>
              <dd className="numeric text-right">{summary.rainfall_points}</dd>
              <dt>Computed in</dt>
              <dd className="numeric text-right">{fixed(summary.computed_ms, 0)} ms</dd>
            </dl>
            <div className="mt-4 flex flex-wrap gap-2">
              <ProvenanceChip provenance="REAL_OPEN" compact />
              <ProvenanceChip provenance="DERIVED" compact />
              <ProvenanceChip provenance="DEMO_CONFIG" compact />
            </div>
          </div>
        ) : loading ? (
          <p className="px-4 py-4 text-[12px] text-[var(--color-ink-muted)]">
            Computing decomposition...
          </p>
        ) : failed || !cell ? (
          <p className="px-4 py-4 text-[12px]" style={{ color: "var(--color-critical)" }}>
            That point falls outside the analysis grid, or the API did not respond. No
            value is shown rather than a guessed one.
          </p>
        ) : (
          <CellPanel cell={cell} />
        )}

        <footer className="border-t border-[var(--color-line)] px-4 py-3 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
          {habitationHazards.decision_authority}
          <br />
          {habitationHazards.scenario_disclaimer}
        </footer>
      </aside>
    </div>
  );
}
