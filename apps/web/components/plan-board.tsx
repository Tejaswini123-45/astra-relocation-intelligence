"use client";

import type {
  AssignmentResponse,
  CandidateSite,
  CounterfactualResponse,
  Habitation,
  PlanResponse,
  StudyArea,
  ZoneFeature,
} from "@astra/contracts";
import { useCallback, useMemo, useState } from "react";

import { type DrawnRoute, RiskMap } from "@/components/risk-map";
import {
  api,
  HAZARD_OVERLAY_URL,
  optimisePlan,
  ROADS_GEOJSON_URL,
  ROUTE_NETWORK_URL,
  TERRAIN_PREVIEW_URL,
} from "@/lib/api";

const PHASE_LABEL: Record<string, string> = {
  IMMEDIATE: "Immediate",
  SHORT_TERM: "Short-term",
  MEDIUM_TERM: "Medium-term",
};

const PHASE_COLOUR: Record<string, [number, number, number, number]> = {
  IMMEDIATE: [201, 66, 56, 235],
  SHORT_TERM: [206, 122, 58, 225],
  MEDIUM_TERM: [86, 190, 172, 215],
};

const PHASE_CSS: Record<string, string> = {
  IMMEDIATE: "var(--color-critical)",
  SHORT_TERM: "var(--color-warning)",
  MEDIUM_TERM: "var(--color-safe)",
};

const UNMET_LABEL: Record<string, string> = {
  NO_FEASIBLE_DESTINATION: "No destination",
  CAPACITY_EXHAUSTED: "Capacity exhausted",
  MOVE_COST_OUTWEIGHED_THE_BENEFIT: "Outweighed by disruption",
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

export function PlanBoard({
  plan: baseline,
  studyArea,
  zones,
  habitations,
  sites,
}: {
  plan: PlanResponse;
  studyArea: StudyArea;
  zones: ZoneFeature[];
  habitations: Habitation[];
  sites: CandidateSite[];
}) {
  const [plan, setPlan] = useState<PlanResponse>(baseline);
  const [selected, setSelected] = useState<AssignmentResponse | null>(
    baseline.assignments[0] ?? null,
  );
  const [whyNot, setWhyNot] = useState<CounterfactualResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [phaseFilter, setPhaseFilter] = useState<string | null>(null);

  const visible = useMemo(
    () =>
      phaseFilter
        ? plan.assignments.filter((a) => a.phase === phaseFilter)
        : plan.assignments,
    [plan.assignments, phaseFilter],
  );

  const drawnRoutes: DrawnRoute[] = useMemo(() => {
    const isSelected = (assignment: AssignmentResponse) =>
      Boolean(
        selected &&
          selected.habitation_id === assignment.habitation_id &&
          selected.site_id === assignment.site_id,
      );
    const draw = (assignment: AssignmentResponse): DrawnRoute => ({
      id: `${assignment.habitation_id}-${assignment.site_id}-${assignment.phase}`,
      geometry: assignment.route_geometry,
      colour: isSelected(assignment)
        ? PHASE_COLOUR[assignment.phase]
        : ([120, 132, 150, 90] as [number, number, number, number]),
      width: isSelected(assignment) ? 100 : 34,
    });
    // Drawn last means drawn on top, so the selected movement goes at the end.
    // Putting it first is how a highlighted route ends up buried under the
    // others it is supposed to stand out from.
    return [
      ...visible.filter((a) => !isSelected(a)).map(draw),
      ...visible.filter(isSelected).map(draw),
    ];
  }, [visible, selected]);

  const focusBounds = useMemo(() => {
    const points = selected?.route_geometry ?? [];
    if (points.length < 2) return null;
    const lons = points.map((p) => p[0]);
    const lats = points.map((p) => p[1]);
    return [
      [Math.min(...lons), Math.min(...lats)],
      [Math.max(...lons), Math.max(...lats)],
    ] as [[number, number], [number, number]];
  }, [selected]);

  const select = useCallback(
    async (assignment: AssignmentResponse) => {
      setSelected(assignment);
      setWhyNot(null);
    },
    [],
  );

  const askWhyNot = useCallback(
    async (habitationId: string, siteId: string) => {
      setBusy(true);
      setError(null);
      try {
        setWhyNot(await api.whyNot(habitationId, siteId));
      } catch {
        setWhyNot(null);
        setError("The API did not return a counterfactual.");
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const runFallback = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await optimisePlan({
        use_fallback: plan.status !== "FALLBACK",
      });
      setPlan(result);
      setSelected(result.assignments[0] ?? null);
      setWhyNot(null);
    } catch {
      setError("The API did not return a plan.");
    } finally {
      setBusy(false);
    }
  }, [plan.status]);

  return (
    <div className="flex h-[calc(100vh-49px)] flex-col">
      <header className="border-b border-[var(--color-line)] bg-[var(--color-surface)] px-5 py-3">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="max-w-2xl">
            <p className="text-[10px] uppercase tracking-[0.18em] text-[var(--color-ink-faint)]">
              Optimised relocation plan
            </p>
            <h1 className="mt-1 text-[17px] font-semibold text-[var(--color-ink)]">
              Who goes where, in what order
            </h1>
            <p className="mt-1 text-[11px] leading-relaxed text-[var(--color-ink)]">
              {plan.headline}
            </p>
          </div>
          <div className="flex flex-wrap items-start gap-7">
            <Stat
              value={`${num(plan.totals.population_assigned)}`}
              label="Residents placed"
              tone="var(--color-safe)"
            />
            <Stat
              value={`${num(plan.totals.population_unmet)}`}
              label="Not placed"
              tone={
                plan.totals.population_unmet > 0
                  ? "var(--color-critical)"
                  : "var(--color-safe)"
              }
            />
            <Stat
              value={`${num(plan.totals.mean_travel_time_min)} min`}
              label="Mean travel, weighted"
            />
            <Stat
              value={pct(plan.totals.mean_route_reliability)}
              label="Mean route reliability"
            />
            <div className="flex flex-col items-end gap-1.5">
              <span className="numeric rounded-sm border px-2 py-0.5 text-[10px] uppercase tracking-[0.1em]"
                style={{
                  borderColor:
                    plan.status === "FALLBACK"
                      ? "var(--color-warning)"
                      : "var(--color-line-strong)",
                  color:
                    plan.status === "FALLBACK"
                      ? "var(--color-warning)"
                      : "var(--color-ink-muted)",
                }}
              >
                {plan.status} · {num(plan.solve_ms, 1)} ms
              </span>
              <span className="text-[9px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)]">
                {plan.solver}
              </span>
              <button
                type="button"
                onClick={runFallback}
                disabled={busy}
                className="rounded-sm border px-2 py-0.5 text-[10px] disabled:opacity-40"
                style={{
                  borderColor: "var(--color-line-strong)",
                  color: "var(--color-ink-muted)",
                }}
              >
                {plan.status === "FALLBACK" ? "Back to the solver" : "Run the fallback"}
              </button>
            </div>
          </div>
        </div>
        {plan.notes.map((note) => (
          <p
            key={note}
            className="mt-2 border-l-2 pl-2 text-[10px] leading-relaxed"
            style={{ borderColor: "var(--color-warning)", color: "var(--color-ink-muted)" }}
          >
            {note}
          </p>
        ))}
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[320px_minmax(0,1fr)_370px]">
        <aside className="flex min-h-0 flex-col border-r border-[var(--color-line)] bg-[var(--color-surface)]">
          <div className="border-b border-[var(--color-line)] px-3 py-2">
            <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
              Movements by phase
            </p>
            <ul className="mt-2 flex flex-col gap-1">
              {plan.phases.map((phase) => (
                <li key={phase.phase}>
                  <button
                    type="button"
                    onClick={() =>
                      setPhaseFilter(phaseFilter === phase.phase ? null : phase.phase)
                    }
                    aria-pressed={phaseFilter === phase.phase}
                    className="grid w-full grid-cols-[1fr_auto] items-baseline gap-2 rounded-sm px-2 py-1 text-left hover:bg-[var(--color-surface-raised)]"
                    style={{
                      background:
                        phaseFilter === phase.phase
                          ? "var(--color-surface-raised)"
                          : undefined,
                    }}
                  >
                    <span
                      className="text-[11px]"
                      style={{ color: PHASE_CSS[phase.phase] }}
                    >
                      {PHASE_LABEL[phase.phase] ?? phase.phase}
                      <span className="numeric ml-1.5 text-[9px] text-[var(--color-ink-faint)]">
                        ≤{num(phase.travel_ceiling_min)} min ·{" "}
                        {pct(phase.capacity_share)} of capacity
                      </span>
                    </span>
                    <span className="numeric text-[12px] text-[var(--color-ink)]">
                      {num(phase.people_moved)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>

          <ul className="min-h-0 flex-1 overflow-y-auto divide-y divide-[var(--color-line)]">
            {visible.map((assignment) => {
              const active =
                selected?.habitation_id === assignment.habitation_id &&
                selected?.site_id === assignment.site_id &&
                selected?.phase === assignment.phase;
              return (
                <li key={`${assignment.habitation_id}-${assignment.site_id}-${assignment.phase}`}>
                  <button
                    type="button"
                    onClick={() => select(assignment)}
                    aria-pressed={active}
                    data-testid="assignment-row"
                    className="w-full px-3 py-2.5 text-left hover:bg-[var(--color-surface-raised)]"
                    style={{
                      background: active ? "var(--color-surface-raised)" : undefined,
                      borderLeft: `2px solid ${active ? PHASE_CSS[assignment.phase] : "transparent"}`,
                    }}
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-[12px] text-[var(--color-ink)]">
                        {assignment.habitation_name}
                        <span className="text-[var(--color-ink-faint)]"> → </span>
                        {assignment.site_name}
                      </span>
                      <span className="numeric text-[13px] text-[var(--color-ink)]">
                        {num(assignment.people)}
                      </span>
                    </div>
                    <div className="mt-0.5 flex items-center justify-between gap-2 text-[10px] text-[var(--color-ink-faint)]">
                      <span className="numeric">
                        {num(assignment.travel_time_min)} min ·{" "}
                        {pct(assignment.route_reliability)} reliable
                      </span>
                      <span style={{ color: PHASE_CSS[assignment.phase] }}>
                        {PHASE_LABEL[assignment.phase]}
                      </span>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
          <footer className="border-t border-[var(--color-line)] px-3 py-2 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
            {plan.assignments.length} movement(s) from {plan.options_offered} allowed
            options. {plan.rejected.length} pairings were removed by a hard constraint
            before the solver ran.
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
            hazardOpacity={0.3}
            selected={null}
            onSelectPoint={() => undefined}
            onSelectZone={() => undefined}
            highlightId={selected?.habitation_id ?? null}
            focusBounds={focusBounds}
          />
          <div className="pointer-events-none absolute left-2 top-2 z-10 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-sm border border-[var(--color-line)] bg-[var(--color-abyss)] px-2.5 py-1.5">
            <span className="text-[9px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
              Assignments
            </span>
            {plan.phases.map((phase) => (
              <span key={phase.phase} className="flex items-center gap-1.5">
                <span
                  className="inline-block h-1 w-5 rounded-full"
                  style={{ background: PHASE_CSS[phase.phase] }}
                />
                <span className="text-[10px] text-[var(--color-ink-muted)]">
                  {PHASE_LABEL[phase.phase]}
                </span>
              </span>
            ))}
            <span className="text-[10px] text-[var(--color-ink-faint)]">
              Each line is the route the solver planned against, not a straight line.
            </span>
          </div>
        </section>

        <aside className="flex min-h-0 flex-col overflow-y-auto border-l border-[var(--color-line)] bg-[var(--color-surface)]">
          {error ? (
            <p className="border-b border-[var(--color-line)] px-4 py-2 text-[11px] text-[var(--color-critical)]">
              {error}
            </p>
          ) : null}

          {selected ? (
            <AssignmentDetail
              assignment={selected}
              plan={plan}
              whyNot={whyNot}
              busy={busy}
              onAskWhyNot={askWhyNot}
            />
          ) : (
            <p className="px-4 py-3 text-[11px] text-[var(--color-ink-muted)]">
              This plan places nobody. Every pairing was removed by a hard constraint.
            </p>
          )}

          <div className="border-b border-[var(--color-line)] px-4 py-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
              Capacity consumed
            </p>
            <ul className="mt-2 flex flex-col gap-2">
              {plan.site_load.map((site) => (
                <li key={site.site_id}>
                  <div className="flex items-baseline justify-between gap-2 text-[11px]">
                    <span className="text-[var(--color-ink)]">{site.site_name}</span>
                    <span className="numeric text-[var(--color-ink-muted)]">
                      {num(site.assigned)} / {num(site.effective_capacity)}
                    </span>
                  </div>
                  <span className="relative mt-1 block h-2 rounded-sm bg-[var(--color-surface-inset)]">
                    <span
                      className="absolute inset-y-0 left-0 rounded-sm"
                      style={{
                        width: `${Math.min(site.utilisation * 100, 100)}%`,
                        background:
                          site.over_soft_capacity > 0
                            ? "var(--color-warning)"
                            : "var(--color-safe)",
                      }}
                    />
                    <span
                      className="absolute inset-y-0 w-px"
                      style={{
                        left: `${(site.soft_capacity / site.effective_capacity) * 100}%`,
                        background: "var(--color-ink-faint)",
                      }}
                      title="Soft capacity"
                    />
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
              The tick marks the soft capacity share. Filling past it is allowed and
              penalised, never forbidden.
            </p>
          </div>

          {plan.stranded_capacity.length > 0 ? (
            <div className="border-b border-[var(--color-line)] px-4 py-3">
              <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
                Capacity nobody waiting can reach
              </p>
              {plan.stranded_capacity.map((entry) => (
                <p
                  key={entry.site_id}
                  className="mt-2 border-l-2 pl-2 text-[11px] leading-relaxed text-[var(--color-ink-muted)]"
                  style={{ borderColor: "var(--color-warning)" }}
                >
                  {entry.detail}
                </p>
              ))}
            </div>
          ) : null}

          <div className="border-b border-[var(--color-line)] px-4 py-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
              Who is not placed, and why
            </p>
            {plan.unmet.length === 0 ? (
              <p className="mt-1.5 text-[11px] text-[var(--color-safe)]">
                Everyone assessed is placed.
              </p>
            ) : (
              <ul className="mt-2 flex flex-col gap-2">
                {plan.unmet.map((entry) => (
                  <li key={entry.habitation_id}>
                    <div className="flex items-baseline justify-between gap-2 text-[11px]">
                      <span className="text-[var(--color-ink)]">
                        {entry.habitation_name}
                        {plan.capacity_blocked.includes(entry.habitation_id) ? (
                          <span
                            className="ml-1.5 text-[9px] uppercase"
                            style={{ color: "var(--color-critical)" }}
                          >
                            capacity blocked
                          </span>
                        ) : null}
                      </span>
                      <span className="numeric text-[var(--color-critical)]">
                        {num(entry.people)}
                      </span>
                    </div>
                    <p className="mt-0.5 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
                      <span className="numeric">
                        {UNMET_LABEL[entry.reason] ?? entry.reason}
                      </span>
                      {" — "}
                      {entry.detail}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="border-b border-[var(--color-line)] px-4 py-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
              Objective, term by term
            </p>
            <table className="mt-2 w-full border-collapse text-left text-[10px] text-[var(--color-ink-muted)]">
              <tbody>
                {Object.entries(plan.objective_terms).map(([term, value]) => (
                  <tr key={term}>
                    <td className="py-0.5 pr-2">{term.replace(/_/g, " ")}</td>
                    <td className="numeric py-0.5 text-right text-[var(--color-ink)]">
                      {num(value)}
                    </td>
                  </tr>
                ))}
                <tr className="border-t border-[var(--color-line)]">
                  <td className="py-0.5 pr-2 text-[var(--color-ink)]">
                    objective value
                  </td>
                  <td className="numeric py-0.5 text-right text-[var(--color-ink)]">
                    {num(plan.objective_value)}
                  </td>
                </tr>
              </tbody>
            </table>
            <p className="mt-1.5 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
              Lower is better. Every weight is a DEMO_CONFIG constant served on this
              response and shown on the Model &amp; Provenance screen.
            </p>
          </div>

          <footer className="mt-auto px-4 py-3 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
            {plan.decision_authority}
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
      <span className="max-w-[150px] text-[9px] uppercase leading-tight tracking-[0.14em] text-[var(--color-ink-faint)]">
        {label}
      </span>
    </div>
  );
}

function AssignmentDetail({
  assignment,
  plan,
  whyNot,
  busy,
  onAskWhyNot,
}: {
  assignment: AssignmentResponse;
  plan: PlanResponse;
  whyNot: CounterfactualResponse | null;
  busy: boolean;
  onAskWhyNot: (habitationId: string, siteId: string) => void;
}) {
  const alternatives = plan.site_load.filter(
    (site) => site.site_id !== assignment.site_id,
  );
  const rejectedFor = plan.rejected.filter(
    (entry) => entry.habitation_id === assignment.habitation_id,
  );

  return (
    <div className="border-b border-[var(--color-line)] px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
            {assignment.habitation_id} &rarr; {assignment.site_id}
          </p>
          <h2 className="mt-0.5 text-[14px] font-semibold text-[var(--color-ink)]">
            {assignment.habitation_name} to {assignment.site_name}
          </h2>
        </div>
        <div className="text-right">
          <p className="numeric text-[22px] leading-none text-[var(--color-ink)]">
            {num(assignment.people)}
          </p>
          <p className="text-[9px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)]">
            residents · ~{num(assignment.households_equivalent)} households
          </p>
        </div>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-[10px] text-[var(--color-ink-muted)]">
        <dt>Phase</dt>
        <dd
          className="text-right"
          style={{ color: PHASE_CSS[assignment.phase] }}
        >
          {PHASE_LABEL[assignment.phase]}
        </dd>
        <dt>Journey</dt>
        <dd className="numeric text-right">
          {num(assignment.distance_km, 1)} km · {num(assignment.travel_time_min)} min
        </dd>
        <dt>Route reliability</dt>
        <dd className="numeric text-right">{pct(assignment.route_reliability)}</dd>
        <dt>Livelihood disruption</dt>
        <dd className="numeric text-right">{assignment.livelihood.percent}%</dd>
      </dl>

      <p className="mt-3 text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
        Livelihood disruption, component by component
      </p>
      <table className="mt-1.5 w-full border-collapse text-left text-[10px] text-[var(--color-ink-muted)]">
        <thead>
          <tr className="text-[9px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)]">
            <th className="pb-1 pr-2 font-medium">Component</th>
            <th className="pb-1 pr-2 text-right font-medium">Measured</th>
            <th className="pb-1 pr-2 text-right font-medium">Weight</th>
            <th className="pb-1 text-right font-medium">Contribution</th>
          </tr>
        </thead>
        <tbody>
          {assignment.livelihood.factors.map((factor) => (
            <tr key={factor.factor}>
              <td className="py-0.5 pr-2">{factor.factor.replace(/_/g, " ")}</td>
              <td className="numeric py-0.5 pr-2 text-right">
                {factor.raw_value === null || factor.raw_value === undefined
                  ? "—"
                  : `${num(factor.raw_value, 2)}${factor.unit ? " " + factor.unit : ""}`}
              </td>
              <td className="numeric py-0.5 pr-2 text-right">
                {factor.weight.toFixed(2)}
              </td>
              <td className="numeric py-0.5 text-right text-[var(--color-ink)]">
                {factor.contribution.toFixed(3)}
              </td>
            </tr>
          ))}
          <tr className="border-t border-[var(--color-line)]">
            <td className="py-0.5 pr-2 text-[var(--color-ink)]">total</td>
            <td colSpan={2} />
            <td className="numeric py-0.5 text-right text-[var(--color-ink)]">
              {assignment.livelihood.value.toFixed(3)}
            </td>
          </tr>
        </tbody>
      </table>
      <p className="mt-1.5 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
        Commute back to the livelihood centre is routed, not measured in a straight
        line, and there is no fixed kilometre rule anywhere in this figure.
      </p>

      <p className="mt-3 text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
        Why not somewhere else
      </p>
      <ul className="mt-1.5 flex flex-wrap gap-1">
        {alternatives.map((site) => (
          <li key={site.site_id}>
            <button
              type="button"
              disabled={busy}
              onClick={() => onAskWhyNot(assignment.habitation_id, site.site_id)}
              data-testid="why-not-option"
              className="numeric rounded-sm border px-1.5 py-0.5 text-[10px] disabled:opacity-40"
              style={{
                borderColor: "var(--color-line-strong)",
                color: "var(--color-ink-muted)",
              }}
            >
              {site.site_id}
            </button>
          </li>
        ))}
        {rejectedFor
          .filter(
            (entry, index, all) =>
              all.findIndex((other) => other.site_id === entry.site_id) === index &&
              !alternatives.some((site) => site.site_id === entry.site_id),
          )
          .map((entry) => (
            <li key={entry.site_id}>
              <button
                type="button"
                disabled={busy}
                onClick={() => onAskWhyNot(assignment.habitation_id, entry.site_id)}
                data-testid="why-not-option"
                className="numeric rounded-sm border border-dashed px-1.5 py-0.5 text-[10px] disabled:opacity-40"
                style={{
                  borderColor: "var(--color-critical)",
                  color: "var(--color-critical)",
                }}
              >
                {entry.site_id}
              </button>
            </li>
          ))}
      </ul>
      {busy ? (
        <p className="mt-2 text-[10px] text-[var(--color-ink-faint)]">Re-solving…</p>
      ) : null}
      {whyNot ? (
        <div
          data-testid="why-not-result"
          className="mt-2 rounded-sm border px-2.5 py-2"
          style={{
            borderColor: whyNot.feasible
              ? "var(--color-line-strong)"
              : "var(--color-critical)",
            background: whyNot.feasible
              ? "var(--color-surface-raised)"
              : "color-mix(in srgb, var(--color-critical) 10%, transparent)",
          }}
        >
          <p className="numeric text-[9px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)]">
            {whyNot.habitation_id} &rarr; {whyNot.site_id} · {whyNot.reason}
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-[var(--color-ink)]">
            {whyNot.headline}
          </p>
          {whyNot.feasible ? (
            <p className="numeric mt-1 text-[10px] text-[var(--color-ink-faint)]">
              objective {num(whyNot.objective_baseline)} &rarr;{" "}
              {num(whyNot.objective_forced ?? 0)} (
              {whyNot.objective_delta !== null &&
              whyNot.objective_delta !== undefined &&
              whyNot.objective_delta >= 0
                ? "+"
                : ""}
              {num(whyNot.objective_delta ?? 0)}) · re-solved with the assignment
              forced
            </p>
          ) : null}
        </div>
      ) : (
        <p className="mt-1.5 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
          Pick a site to force that assignment and re-solve. The answer is the
          outcome of the re-solve, not a description of one. Dashed sites were
          removed by a hard constraint before the solver ran.
        </p>
      )}
    </div>
  );
}
