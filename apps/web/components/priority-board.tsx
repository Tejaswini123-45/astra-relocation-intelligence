"use client";

import type {
  CandidateSite,
  ComponentScoreResponse,
  FactorContribution,
  HabitationPriorityResponse,
  HabitationPriorityRow,
  HabitationStabilityResponse,
  RiskSummaryResponse,
  ZoneFeature,
} from "@astra/contracts";
import { useCallback, useMemo, useState } from "react";

import { RiskMap } from "@/components/risk-map";
import { API_BASE, HAZARD_OVERLAY_URL, ROADS_GEOJSON_URL } from "@/lib/api";

const PHASE_LABEL: Record<string, string> = {
  IMMEDIATE: "Immediate",
  SHORT_TERM: "Short-term",
  MEDIUM_TERM: "Medium-term",
  NOT_PRIORITISED: "Monitored",
  CAPACITY_BLOCKED: "Capacity blocked",
};

const PHASE_COLOUR: Record<string, string> = {
  IMMEDIATE: "var(--color-severity-critical)",
  SHORT_TERM: "var(--color-severity-elevated)",
  MEDIUM_TERM: "var(--color-severity-watch)",
  NOT_PRIORITISED: "var(--color-neutral)",
  CAPACITY_BLOCKED: "var(--color-prov-synthetic)",
};

const PHASE_RGBA: Record<string, [number, number, number, number]> = {
  IMMEDIATE: [192, 57, 47, 210],
  SHORT_TERM: [205, 117, 56, 200],
  MEDIUM_TERM: [185, 147, 64, 190],
  NOT_PRIORITISED: [86, 101, 125, 180],
  CAPACITY_BLOCKED: [155, 127, 212, 200],
};

const PHASE_ORDER = ["IMMEDIATE", "SHORT_TERM", "MEDIUM_TERM", "NOT_PRIORITISED"];

const COMPONENT_LABEL: Record<string, string> = {
  hazard: "Hazard",
  exposure: "Exposure",
  vulnerability: "Vulnerability",
  history: "History",
};

function fixed(value: number, digits = 1): string {
  return value.toFixed(digits);
}

function ComponentBlock({
  title,
  component,
  weight,
}: {
  title: string;
  component: ComponentScoreResponse;
  weight?: number;
}) {
  const maximum = Math.max(
    ...component.factors.map((factor) => factor.contribution),
    0.0001,
  );
  return (
    <section className="border-t border-[var(--color-line)] px-4 py-3">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-[12px] text-[var(--color-ink)]">{title}</h3>
        <span className="numeric text-[13px] text-[var(--color-ink)]">
          {fixed(component.value, 3)}
          {weight !== undefined ? (
            <span className="ml-2 text-[10px] text-[var(--color-ink-faint)]">
              weight {fixed(weight, 2)}
            </span>
          ) : null}
        </span>
      </div>
      <table className="mt-2 w-full border-collapse text-left text-[11px] text-[var(--color-ink-muted)]">
        <tbody>
          {component.factors.map((factor: FactorContribution) => (
            <tr key={factor.factor}>
              <td className="py-1 pr-2 text-[var(--color-ink)]">
                {factor.factor.replace(/_/g, " ")}
                {factor.unit ? (
                  <span className="ml-1 text-[9px] text-[var(--color-ink-faint)]">
                    {factor.unit}
                  </span>
                ) : null}
              </td>
              <td className="numeric py-1 pr-2 text-right">
                {factor.raw_value === null || factor.raw_value === undefined
                  ? "-"
                  : fixed(factor.raw_value, 2)}
              </td>
              <td className="numeric py-1 pr-2 text-right">
                {fixed(factor.normalised_value, 3)}
              </td>
              <td className="numeric py-1 pr-2 text-right text-[var(--color-ink-faint)]">
                x{fixed(factor.weight, 2)}
              </td>
              <td className="py-1">
                <span
                  className="numeric block rounded-sm px-1.5 py-0.5 text-right text-[var(--color-ink)]"
                  style={{
                    background: `linear-gradient(to left, color-mix(in srgb, var(--color-signal) 32%, transparent) ${(
                      (factor.contribution / maximum) *
                      100
                    ).toFixed(0)}%, transparent ${(
                      (factor.contribution / maximum) *
                      100
                    ).toFixed(0)}%)`,
                  }}
                >
                  {fixed(factor.contribution, 3)}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {component.note ? (
        <p className="mt-2 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
          {component.note}
        </p>
      ) : null}
    </section>
  );
}

function ReasoningDrawer({
  row,
  weights,
  stability,
}: {
  row: HabitationPriorityRow;
  weights: Record<string, number>;
  stability: HabitationStabilityResponse | null;
}) {
  const maximum = Math.max(
    ...row.priority_factors.map((factor) => factor.contribution),
    0.0001,
  );
  return (
    <div className="flex flex-col">
      <header className="px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-[10px] uppercase tracking-[0.14em] text-[var(--color-ink-faint)]">
              Rank {row.rank} &middot; {row.habitation_id}
            </p>
            <h2 className="mt-1 text-[17px] font-semibold text-[var(--color-ink)]">
              {row.name}
            </h2>
            <p className="mt-1 text-[11px] text-[var(--color-ink-muted)]">
              {row.population.toLocaleString("en-IN")} residents &middot;{" "}
              {row.households.toLocaleString("en-IN")} households
              {row.elevation_m ? ` · ${fixed(row.elevation_m, 0)} m` : ""}
            </p>
          </div>
          <div className="text-right">
            <span
              className="numeric text-[30px] leading-none"
              style={{ color: PHASE_COLOUR[row.phase.phase] }}
            >
              {fixed(row.priority_score, 1)}
            </span>
            <p className="text-[10px] text-[var(--color-ink-faint)]">priority / 100</p>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span
            className="numeric rounded-sm border px-2 py-1 text-[10px] uppercase tracking-[0.1em]"
            style={{
              borderColor: PHASE_COLOUR[row.phase.phase],
              color: PHASE_COLOUR[row.phase.phase],
            }}
          >
            {PHASE_LABEL[row.phase.phase] ?? row.phase.phase}
          </span>
          <span className="numeric rounded-sm border border-[var(--color-line-strong)] px-2 py-1 text-[10px] uppercase tracking-[0.1em] text-[var(--color-ink-muted)]">
            zone {row.zone_class}
          </span>
          <span className="numeric rounded-sm border border-[var(--color-line-strong)] px-2 py-1 text-[10px] uppercase tracking-[0.1em] text-[var(--color-ink-muted)]">
            confidence {row.confidence.band} {fixed(row.confidence.value, 2)}
          </span>
          {stability ? (
            <span
              className="numeric rounded-sm border px-2 py-1 text-[10px] uppercase tracking-[0.1em]"
              data-testid="rank-stability"
              style={{
                borderColor: stability.stable
                  ? "var(--color-safe)"
                  : "var(--color-warning)",
                color: stability.stable
                  ? "var(--color-safe)"
                  : "var(--color-warning)",
              }}
              title={stability.note}
            >
              rank {stability.best_rank}&ndash;{stability.worst_rank} under &plusmn;20%
              weights
            </span>
          ) : null}
        </div>

        <p className="mt-3 text-[12px] leading-relaxed text-[var(--color-ink-muted)]">
          {row.phase.reason}
        </p>
        {(row.phase.rules_applied ?? []).length > 0 ? (
          <ul className="mt-2 flex flex-col gap-1">
            {(row.phase.rules_applied ?? []).map((rule) => (
              <li
                key={rule}
                className="border-l-2 pl-2 text-[11px] leading-relaxed"
                style={{ borderColor: "var(--color-critical)", color: "var(--color-ink)" }}
              >
                {rule}
              </li>
            ))}
          </ul>
        ) : null}
        {(row.phase.pending_checks ?? []).length > 0 ? (
          <p className="mt-2 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
            Not yet applied to this decision: {(row.phase.pending_checks ?? []).join(", ")}.
          </p>
        ) : null}
      </header>

      <section className="border-t border-[var(--color-line)] bg-[var(--color-surface-inset)] px-4 py-3">
        <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
          How the score was assembled
        </p>
        <table className="mt-2 w-full border-collapse text-left text-[11px] text-[var(--color-ink-muted)]">
          <tbody>
            {row.priority_factors.map((factor) => (
              <tr key={factor.factor}>
                <td className="py-1 pr-2 text-[var(--color-ink)]">
                  {COMPONENT_LABEL[factor.factor] ?? factor.factor}
                </td>
                <td className="numeric py-1 pr-2 text-right">
                  {fixed(factor.normalised_value, 3)}
                </td>
                <td className="numeric py-1 pr-2 text-right text-[var(--color-ink-faint)]">
                  x{fixed(factor.weight, 2)}
                </td>
                <td className="py-1">
                  <span
                    className="numeric block rounded-sm px-1.5 py-0.5 text-right text-[var(--color-ink)]"
                    style={{
                      background: `linear-gradient(to left, color-mix(in srgb, var(--color-signal) 38%, transparent) ${(
                        (factor.contribution / maximum) *
                        100
                      ).toFixed(0)}%, transparent ${(
                        (factor.contribution / maximum) *
                        100
                      ).toFixed(0)}%)`,
                    }}
                  >
                    {fixed(factor.contribution, 3)}
                  </span>
                </td>
              </tr>
            ))}
            <tr className="border-t border-[var(--color-line)]">
              <td className="py-1.5 pr-2 text-[var(--color-ink)]">Priority</td>
              <td colSpan={2} />
              <td className="numeric py-1.5 text-right text-[var(--color-ink)]">
                {fixed(row.priority_score, 1)}
              </td>
            </tr>
          </tbody>
        </table>
        <p className="mt-2 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
          {row.confidence.note}
        </p>
      </section>

      <ComponentBlock
        title="Hazard over the footprint"
        component={row.hazard_component}
        weight={weights.hazard}
      />
      <div className="px-4 pb-2 text-[10px] text-[var(--color-ink-faint)]">
        Dominant hazard {row.hazard.dominant_hazard.toLowerCase()} at composite{" "}
        {fixed(row.hazard.composite, 1)}; footprint mean{" "}
        {fixed(row.footprint_mean_composite, 1)}, max{" "}
        {fixed(row.footprint_max_composite, 1)} over a{" "}
        {fixed(row.footprint_radius_m, 0)} m radius.
      </div>
      <ComponentBlock
        title="Exposure"
        component={row.exposure}
        weight={weights.exposure}
      />
      <ComponentBlock
        title="Vulnerability"
        component={row.vulnerability}
        weight={weights.vulnerability}
      />
      <ComponentBlock title="History" component={row.history} weight={weights.history} />
    </div>
  );
}

export function PriorityBoard({
  priority,
  summary,
  zones,
  sites,
  stability,
}: {
  priority: HabitationPriorityResponse;
  summary: RiskSummaryResponse;
  zones: ZoneFeature[];
  sites: CandidateSite[];
  /**
   * Rank stability from the Monte Carlo back-test, by habitation id. Absent
   * until scripts/backtest.py has been run - in which case no flag is shown at
   * all, rather than an unearned "stable".
   */
  stability: Record<string, HabitationStabilityResponse> | null;
}) {
  const [selectedId, setSelectedId] = useState<string>(
    priority.habitations[0]?.habitation_id ?? "",
  );

  const selected = useMemo(
    () => priority.habitations.find((row) => row.habitation_id === selectedId) ?? null,
    [priority.habitations, selectedId],
  );

  const stabilityOf = useCallback(
    (habitationId: string) => stability?.[habitationId] ?? null,
    [stability],
  );

  const phaseById = useMemo(() => {
    const map = new Map<string, string>();
    for (const row of priority.habitations) map.set(row.habitation_id, row.phase.phase);
    return map;
  }, [priority.habitations]);

  const mapHabitations = useMemo(
    () =>
      priority.habitations.map((row) => ({
        id: row.habitation_id,
        name: row.name,
        centroid: row.centroid,
        population: row.population,
      })),
    [priority.habitations],
  );

  const habitationColour = useCallback(
    (habitation: { id: string }): [number, number, number, number] =>
      PHASE_RGBA[phaseById.get(habitation.id) ?? "NOT_PRIORITISED"] ??
      PHASE_RGBA.NOT_PRIORITISED,
    [phaseById],
  );

  const grouped = useMemo(
    () =>
      PHASE_ORDER.map((phase) => ({
        phase,
        rows: priority.habitations.filter((row) => row.phase.phase === phase),
      })).filter((group) => group.rows.length > 0),
    [priority.habitations],
  );

  return (
    <div className="flex h-[calc(100vh-64px)] flex-col xl:flex-row">
      <aside className="w-full shrink-0 overflow-y-auto border-b border-[var(--color-line)] bg-[var(--color-surface)] xl:w-[340px] xl:border-b-0 xl:border-r">
        <header className="border-b border-[var(--color-line)] px-4 py-3">
          <h1 className="text-[13px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink)]">
            Relocation priority
          </h1>
          <p className="mt-1 text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
            {priority.total_population_assessed.toLocaleString("en-IN")} residents
            assessed across {priority.habitations.length} habitations. Weights: hazard{" "}
            {fixed(priority.weights.hazard, 2)}, exposure{" "}
            {fixed(priority.weights.exposure, 2)}, vulnerability{" "}
            {fixed(priority.weights.vulnerability, 2)}, history{" "}
            {fixed(priority.weights.history, 2)}.
          </p>
          <p className="mt-2 border-l-2 border-[var(--color-neutral)] pl-2 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
            {priority.priority_note}
          </p>
        </header>

        <div className="border-b border-[var(--color-line)] px-4 py-3">
          <ul className="flex flex-col gap-1.5 text-[11px]">
            {PHASE_ORDER.filter((phase) => priority.totals_by_phase[phase]).map((phase) => {
              const totals = priority.totals_by_phase[phase];
              return (
                <li key={phase} className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-2 text-[var(--color-ink-muted)]">
                    <span
                      aria-hidden
                      className="inline-block h-2 w-2 rounded-[1px]"
                      style={{ background: PHASE_COLOUR[phase] }}
                    />
                    {PHASE_LABEL[phase]}
                  </span>
                  <span className="numeric text-[var(--color-ink)]">
                    {totals.habitations} &middot; {totals.population.toLocaleString("en-IN")}{" "}
                    <span className="text-[var(--color-ink-faint)]">people</span>
                  </span>
                </li>
              );
            })}
          </ul>
          <p className="mt-2 text-[10px] text-[var(--color-ink-faint)]">
            Thresholds: Immediate {priority.tier_thresholds.IMMEDIATE}, Short-term{" "}
            {priority.tier_thresholds.SHORT_TERM}, Medium-term{" "}
            {priority.tier_thresholds.MEDIUM_TERM}. Override rules can escalate a
            habitation regardless of score.
          </p>
        </div>

        {grouped.map((group) => (
          <div key={group.phase} className="border-b border-[var(--color-line)] py-2">
            <p
              className="px-4 py-1 text-[10px] uppercase tracking-[0.14em]"
              style={{ color: PHASE_COLOUR[group.phase] }}
            >
              {PHASE_LABEL[group.phase]}
            </p>
            <ul>
              {group.rows.map((row) => (
                <li key={row.habitation_id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(row.habitation_id)}
                    aria-pressed={row.habitation_id === selectedId}
                    className="flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-[var(--color-surface-raised)]"
                    style={{
                      background:
                        row.habitation_id === selectedId
                          ? "var(--color-surface-raised)"
                          : undefined,
                    }}
                  >
                    <span className="numeric w-5 text-right text-[11px] text-[var(--color-ink-faint)]">
                      {row.rank}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[12px] text-[var(--color-ink)]">
                        {row.name}
                      </span>
                      <span className="numeric block text-[10px] text-[var(--color-ink-faint)]">
                        {row.habitation_id} &middot;{" "}
                        {row.population.toLocaleString("en-IN")} residents &middot; zone{" "}
                        {row.zone_class.toLowerCase()}
                      </span>
                    </span>
                    {stabilityOf(row.habitation_id)?.stable === false ? (
                      <span
                        className="numeric shrink-0 rounded-sm border px-1 text-[9px] uppercase"
                        style={{
                          borderColor: "var(--color-warning)",
                          color: "var(--color-warning)",
                        }}
                        title={stabilityOf(row.habitation_id)?.note}
                      >
                        sensitive
                      </span>
                    ) : null}
                    {(row.phase.rules_applied ?? []).length > 0 ? (
                      <span
                        className="numeric shrink-0 rounded-sm border px-1 text-[9px] uppercase"
                        style={{
                          borderColor: "var(--color-critical)",
                          color: "var(--color-critical)",
                        }}
                        title={(row.phase.rules_applied ?? []).join("; ")}
                      >
                        override
                      </span>
                    ) : null}
                    <span
                      className="numeric shrink-0 text-[13px]"
                      style={{ color: PHASE_COLOUR[row.phase.phase] }}
                    >
                      {fixed(row.priority_score, 0)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </aside>

      <div className="relative min-h-[380px] flex-1">
        <RiskMap
          studyArea={summary.study_area}
          terrainUrl={`${API_BASE}${summary.terrain_url}`}
          overlayUrl={HAZARD_OVERLAY_URL}
          roadsUrl={ROADS_GEOJSON_URL}
          zones={zones}
          habitations={mapHabitations as never}
          sites={sites}
          toggles={{
            hazard: true,
            zones: true,
            habitations: true,
            sites: true,
            roads: true,
          }}
          hazardOpacity={0.4}
          selected={selected ? { lon: selected.centroid.lon, lat: selected.centroid.lat } : null}
          onSelectPoint={() => undefined}
          onSelectZone={() => undefined}
          habitationColour={habitationColour as never}
          highlightId={selectedId}
        />
        <div className="pointer-events-none absolute left-2 top-2 rounded-sm bg-[var(--color-abyss)]/80 px-2 py-1.5 text-[10px] text-[var(--color-ink-muted)]">
          Habitations coloured by relocation phase
        </div>
      </div>

      <aside className="w-full shrink-0 overflow-y-auto border-t border-[var(--color-line)] bg-[var(--color-surface)] xl:w-[440px] xl:border-l xl:border-t-0">
        {selected ? (
          <ReasoningDrawer
            row={selected}
            weights={priority.weights}
            stability={stabilityOf(selected.habitation_id)}
          />
        ) : (
          <p className="px-4 py-4 text-[12px] text-[var(--color-ink-muted)]">
            Select a habitation to see why it is ranked where it is.
          </p>
        )}
        <footer className="border-t border-[var(--color-line)] px-4 py-3 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
          {priority.decision_authority}
          <br />
          {priority.scenario_disclaimer}
          <br />
          {priority.history_note}
        </footer>
      </aside>
    </div>
  );
}
