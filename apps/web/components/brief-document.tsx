import type { BriefMovement, BriefResponse, DecisionResponse } from "@astra/contracts";
import { ZONE_CLASS_ORDER } from "@astra/contracts";
import Link from "next/link";
import type { ReactNode } from "react";

/**
 * The printable Decision Brief. A paper document, deliberately: dark ink on an
 * off-white page, tables that survive a black-and-white printer, and every figure
 * rendered exactly as the frozen brief carries it.
 */

const PHASE_LABEL: Record<string, string> = {
  IMMEDIATE: "Immediate",
  SHORT_TERM: "Short-term",
  MEDIUM_TERM: "Medium-term",
  CAPACITY_BLOCKED: "Capacity blocked",
  NOT_PRIORITISED: "Not prioritised",
};

/** Decision order. A frozen brief is stored with sorted keys, so never iterate the object. */
const PHASE_ORDER = ["IMMEDIATE", "SHORT_TERM", "MEDIUM_TERM", "CAPACITY_BLOCKED", "NOT_PRIORITISED"];

function fmt(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function label(value: string): string {
  return value.replace(/_/g, " ").toLowerCase();
}

function Section({ number, title, children }: { number: number; title: string; children: ReactNode }) {
  return (
    <section className="mt-7 break-inside-avoid-page">
      <h2 className="flex items-baseline gap-2 border-b border-[#bdb7a8] pb-1 text-[12px] font-semibold uppercase tracking-[0.14em]">
        <span className="numeric text-[#6b7686]">{String(number).padStart(2, "0")}</span>
        {title}
      </h2>
      <div className="mt-2.5">{children}</div>
    </section>
  );
}

function Table({ head, rows, testId }: { head: string[]; rows: ReactNode[][]; testId?: string }) {
  return (
    <div className="overflow-x-auto">
      <table data-testid={testId} className="w-full border-collapse text-left text-[11px]">
        <thead>
          <tr>
            {head.map((heading) => (
              <th
                key={heading}
                scope="col"
                className="border-b border-[#16202b] px-1.5 py-1 text-[9px] font-semibold uppercase tracking-[0.08em] text-[#4a5566]"
              >
                {heading}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className="border-b border-[#e0dbcf] align-top">
              {row.map((cell, column) => (
                <td key={column} className="px-1.5 py-1">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function N({ children }: { children: ReactNode }) {
  return <span className="numeric">{children}</span>;
}

function movementRows(movements: BriefMovement[]): ReactNode[][] {
  return movements.map((move) => [
    <N key="h">{move.habitation_id}</N>,
    move.habitation_name,
    <N key="s">{move.site_id}</N>,
    move.site_name,
    <N key="p">{fmt(move.people)}</N>,
    <N key="t">{fmt(move.travel_time_min, 1)}</N>,
    <N key="r">{move.route_reliability.toFixed(3)}</N>,
  ]);
}

export function BriefDocument({
  brief,
  decision,
}: {
  brief: BriefResponse;
  decision: DecisionResponse | null;
}) {
  const { situation, capacity, plan, routes, validation, confidence } = brief;
  return (
    <article
      data-testid="decision-brief"
      className="mx-auto w-full max-w-[1000px] rounded-sm bg-[#f6f4ef] px-5 py-6 text-[#16202b] shadow-[0_0_0_1px_#cfcabd] sm:px-9 sm:py-8 print:max-w-none print:rounded-none print:bg-white print:p-0 print:shadow-none"
    >
      <header className="flex flex-wrap items-start justify-between gap-4 border-b-2 border-[#16202b] pb-4">
        <div className="min-w-0">
          <p className="text-[10px] uppercase tracking-[0.2em] text-[#4a5566]">
            ASTRA &middot; Decision Brief &middot; SIH26191
          </p>
          <h1 className="mt-1 text-[22px] font-semibold leading-tight">
            Relocation decision support, Alaknanda valley corridor
          </h1>
          <p className="mt-1 text-[11px] text-[#4a5566]">
            {brief.scenario_name} &middot;{" "}
            {brief.basis === "LIVE" ? `live picture, run ${brief.run_id}` : "baseline assessment"}
          </p>
        </div>
        <dl className="grid grid-cols-[auto_auto] gap-x-3 gap-y-0.5 text-[10px]">
          <dt className="text-[#4a5566]">Brief</dt>
          <dd className="numeric" data-testid="brief-id">{brief.id}</dd>
          <dt className="text-[#4a5566]">Audit decision</dt>
          <dd className="numeric" data-testid="brief-audit-id">{brief.audit?.decision_id ?? "—"}</dd>
          <dt className="text-[#4a5566]">Generated</dt>
          <dd className="numeric">{brief.generated_at.replace("T", " ").slice(0, 19)} UTC</dd>
          <dt className="text-[#4a5566]">Input hash</dt>
          <dd className="numeric">{brief.audit?.input_summary_hash ?? "—"}</dd>
          <dt className="text-[#4a5566]">Engine / config</dt>
          <dd className="numeric">
            {brief.engine_version} / {brief.model_config_version}
          </dd>
        </dl>
      </header>

      <p
        className="mt-4 border-l-4 border-[#a32d24] bg-[#ece6da] px-3 py-2 text-[12px] font-medium"
        data-testid="brief-authority"
      >
        {brief.decision_authority}
      </p>
      <p className="mt-2 text-[10px] leading-relaxed text-[#4a5566]">
        {brief.scenario_disclaimer} Zones are an {brief.classification_label}, not a statutory
        designation. {brief.basis_note}
      </p>

      <Section number={1} title="Situation">
        <p className="text-[14px] leading-relaxed" data-testid="brief-situation">
          {situation.headline}
        </p>
        {situation.plan_requires_review ? (
          <p className="mt-2 text-[12px] font-medium text-[#a32d24]">
            Plan requires review: {situation.review_headline}
          </p>
        ) : null}
        <div className="mt-3 grid gap-5 md:grid-cols-2">
          <Table
            head={["Zone class", "Zones", "Area km2", "Residents intersected"]}
            rows={ZONE_CLASS_ORDER.filter((zone) => situation.zones[zone]).map((zone) => [
              label(zone),
              <N key="c">{fmt(situation.zones[zone].count)}</N>,
              <N key="a">{fmt(situation.zones[zone].area_km2, 2)}</N>,
              <N key="p">{fmt(situation.zones[zone].population_intersected)}</N>,
            ])}
          />
          <Table
            head={["Relocation tier", "Habitations", "Residents", "Households"]}
            rows={PHASE_ORDER.filter((phase) => situation.by_phase[phase]).map((phase) => [
              PHASE_LABEL[phase],
              <N key="h">{fmt(situation.by_phase[phase].habitations)}</N>,
              <N key="p">{fmt(situation.by_phase[phase].population)}</N>,
              <N key="hh">{fmt(situation.by_phase[phase].households)}</N>,
            ])}
          />
        </div>
      </Section>

      <Section number={2} title="Recommended actions">
        <div className="flex flex-col gap-5" data-testid="brief-actions">
          {brief.actions.map((action) => (
            <div key={action.phase} className="break-inside-avoid">
              <h3 className="text-[13px] font-semibold">
                {PHASE_LABEL[action.phase]} &middot; <N>{fmt(action.people_moved)}</N> residents
              </h3>
              <p className="text-[10px] text-[#4a5566]">{action.tier_rule}</p>
              <ul className="mt-1 list-disc pl-5 text-[12px] leading-relaxed">
                {action.steps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ul>
              {action.movements.length > 0 ? (
                <div className="mt-2">
                  <Table
                    head={["From", "Habitation", "To", "Site", "Residents", "Travel min", "Reliability"]}
                    rows={movementRows(action.movements)}
                  />
                </div>
              ) : null}
            </div>
          ))}
        </div>
        {plan.unmet.length > 0 ? (
          <div className="mt-4">
            <h3 className="text-[12px] font-semibold">Residents without a destination</h3>
            <Table
              head={["Habitation", "Residents", "Reason", "Detail"]}
              rows={plan.unmet.map((entry) => [
                <span key="h">
                  <N>{entry.habitation_id}</N> {entry.habitation_name}
                </span>,
                <N key="p">{fmt(entry.people)}</N>,
                label(entry.reason),
                entry.detail,
              ])}
            />
          </div>
        ) : null}
        {plan.capacity_blocked.length > 0 ? (
          <p className="mt-2 text-[11px]">
            <strong>Capacity blocked:</strong> <N>{plan.capacity_blocked.join(", ")}</N> - high risk
            with no feasible matched capacity.
          </p>
        ) : null}
      </Section>

      <Section number={3} title="Priority habitations">
        <Table
          testId="brief-priorities"
          head={["Rank", "Habitation", "Residents", "Priority", "Tier", "Zone", "Dominant hazard", "Confidence"]}
          rows={brief.priorities.slice(0, 8).map((row) => [
            <N key="r">{row.rank}</N>,
            <span key="h">
              <N>{row.habitation_id}</N> {row.name}
            </span>,
            <N key="p">{fmt(row.population)}</N>,
            <N key="s">{row.priority_score.toFixed(1)}</N>,
            PHASE_LABEL[row.phase] ?? label(row.phase),
            label(row.zone_class),
            label(row.dominant_hazard),
            label(row.confidence_band),
          ])}
        />
        <p className="mt-1.5 text-[10px] text-[#4a5566]">{brief.priority_note}</p>
        {brief.priorities
          .slice(0, 8)
          .filter((row) => row.rules_applied.length > 0)
          .map((row) => (
            <p key={row.habitation_id} className="mt-1 text-[10px] text-[#4a5566]">
              <N>{row.habitation_id}</N> override applied: {row.rules_applied.join("; ")}
            </p>
          ))}
      </Section>

      <Section number={4} title="Relocation capacity">
        <p className="text-[12px]">
          <N>{capacity.suitable_sites}</N> of <N>{capacity.candidate_sites}</N> candidate sites pass
          every suitability gate, with <N>{fmt(capacity.total_effective_capacity)}</N> effective
          places against <N>{fmt(capacity.total_theoretical_capacity)}</N> on land alone.
        </p>
        <div className="mt-2">
          <Table
            head={["Site", "Gates", "Theoretical", "Effective", "Bottleneck", "Assigned", "Remaining"]}
            rows={capacity.sites.map((site) => [
              <span key="s">
                <N>{site.site_id}</N> {site.name}
              </span>,
              site.suitable ? "pass" : `fails ${site.failed_gates.map(label).join(", ")}`,
              <N key="t">{fmt(site.theoretical_capacity)}</N>,
              <N key="e">{fmt(site.effective_capacity)}</N>,
              site.bottleneck ? label(site.bottleneck) : "—",
              <N key="a">{fmt(site.assigned)}</N>,
              <N key="r">{fmt(site.remaining)}</N>,
            ])}
          />
        </div>
        <ul className="mt-2 list-disc pl-5 text-[11px] leading-relaxed">
          {capacity.sites
            .filter((site) => site.suitable && site.marginal_headline)
            .map((site) => (
              <li key={site.site_id}>
                <N>{site.site_id}</N>: {site.marginal_headline}
              </li>
            ))}
        </ul>
        <p className="mt-1.5 text-[10px] text-[#4a5566]">{capacity.limitation}</p>
      </Section>

      <Section number={5} title="Route risks">
        <p className="text-[12px]">
          <N>{routes.feasible_pairs}</N> of <N>{routes.pairs_evaluated}</N> habitation-to-site routes
          clear the reliability threshold of <N>{routes.reliability_threshold}</N>.
          {routes.closed_segments.length > 0 ? (
            <>
              {" "}
              Closed: <N>{routes.closed_segments.join(", ")}</N>.
            </>
          ) : null}
          {routes.habitations_without_reachable_suitable_site.length > 0 ? (
            <>
              {" "}
              No reliable route to a suitable site:{" "}
              <N>{routes.habitations_without_reachable_suitable_site.join(", ")}</N>.
            </>
          ) : null}
        </p>
        {routes.dependencies.length > 0 ? (
          <div className="mt-2">
            <Table
              head={["Segment the plan depends on", "Bridge", "No alternative", "Residents crossing", "p(fail)"]}
              rows={routes.dependencies.map((segment) => [
                <span key="s">
                  <N>{segment.segment_id}</N>
                  {segment.name ? ` ${segment.name}` : ""}
                </span>,
                segment.is_bridge ? "yes" : "no",
                segment.no_alternative ? "yes" : "no",
                <N key="p">{fmt(segment.people_dependent)}</N>,
                <N key="f">{segment.p_fail.toFixed(4)}</N>,
              ])}
            />
            <p className="mt-1 text-[10px] text-[#4a5566]">{routes.dependencies[0].consequence}</p>
          </div>
        ) : null}
        {routes.weakest_movements.length > 0 ? (
          <div className="mt-3">
            <h3 className="text-[12px] font-semibold">Least reliable planned movements</h3>
            <Table
              head={["From", "Habitation", "To", "Site", "Residents", "Travel min", "Reliability"]}
              rows={movementRows(routes.weakest_movements)}
            />
          </div>
        ) : null}
      </Section>

      <Section number={6} title="Plan advisory">
        <p className="text-[12px] leading-relaxed">{brief.narration.text}</p>
        <p className="mt-1 text-[10px] text-[#4a5566]">
          {brief.narration.mode === "model" ? "Language-model narration, numbers verified." : "Template narration."}{" "}
          {brief.narration.note}
        </p>
        <p className="mt-2 text-[11px]">
          {plan.headline} Solver <N>{plan.solver}</N>, status <N>{plan.status}</N>, objective{" "}
          <N>{fmt(plan.objective_value)}</N>.
        </p>
      </Section>

      <Section number={7} title="Data confidence and validation">
        <p className="text-[12px]">
          Evidence confidence (modal band): <strong>{label(confidence.modal_band)}</strong> - high{" "}
          <N>{confidence.bands.HIGH ?? 0}</N>, medium <N>{confidence.bands.MEDIUM ?? 0}</N>, low{" "}
          <N>{confidence.bands.LOW ?? 0}</N> habitations. {confidence.note}
        </p>
        {validation.available ? (
          <ul className="mt-2 list-disc pl-5 text-[11px] leading-relaxed">
            <li>{validation.backtest_headline}</li>
            <li>{validation.sensitivity_headline}</li>
          </ul>
        ) : null}
        {validation.stale ? (
          <p className="mt-1 text-[11px] font-medium text-[#a32d24]">
            The validation figures were computed against a different model version; re-run the
            back-test before quoting them.
          </p>
        ) : null}
      </Section>

      <Section number={8} title="Static hazard map vs ASTRA decision mode">
        <Table
          head={["Question", "Static hazard map", "ASTRA decision mode"]}
          rows={brief.comparison.map((row) => [
            <strong key="q">{row.question}</strong>,
            row.static_map,
            row.astra,
          ])}
        />
      </Section>

      <Section number={9} title="Assumptions">
        <Table
          head={["Constant", "Value", "Provenance", "Meaning / source"]}
          rows={brief.assumptions.map((constant) => [
            <N key="k">{constant.key}</N>,
            <N key="v">
              {constant.value}
              {constant.unit ? ` ${constant.unit}` : ""}
            </N>,
            constant.provenance.replace(/_/g, " "),
            constant.citation ?? constant.description,
          ])}
        />
      </Section>

      <Section number={10} title="Limitations">
        <ul className="list-disc pl-5 text-[11px] leading-relaxed">
          {brief.limitations.map((limitation) => (
            <li key={limitation}>{limitation}</li>
          ))}
        </ul>
      </Section>

      <Section number={11} title="Audit and human decision">
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-0.5 text-[11px]">
          <dt className="text-[#4a5566]">Decision record</dt>
          <dd className="numeric">{brief.audit?.decision_id ?? "—"}</dd>
          <dt className="text-[#4a5566]">Solver status at generation</dt>
          <dd className="numeric">{brief.audit?.solver_status ?? "—"}</dd>
          <dt className="text-[#4a5566]">State at generation</dt>
          <dd>{brief.audit ? label(brief.audit.state) : "—"}</dd>
          <dt className="text-[#4a5566]">State in the ledger now</dt>
          <dd data-testid="brief-ledger-state">{decision ? label(decision.state) : "not available"}</dd>
        </dl>
        {decision && decision.overrides.length > 0 ? (
          <ul className="mt-2 list-disc pl-5 text-[11px] leading-relaxed">
            {decision.overrides.map((entry) => (
              <li key={entry.id}>
                {label(entry.action)} by {entry.actor}
                {entry.habitation_id ? ` (${entry.habitation_id} → ${entry.site_id})` : ""}: &ldquo;
                {entry.reason}&rdquo;
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-[11px] text-[#4a5566]">
            No approval, override or annotation has been recorded against this decision yet.
          </p>
        )}
        <p className="mt-2 text-[11px] print:hidden">
          <Link href="/evidence" className="underline underline-offset-2">
            Record an approval, override or annotation in Evidence &amp; Audit
          </Link>
        </p>
        <div className="mt-6 grid grid-cols-2 gap-8 text-[10px] text-[#4a5566]">
          <p className="border-t border-[#16202b] pt-1">Reviewed by (SDMA / District Authority)</p>
          <p className="border-t border-[#16202b] pt-1">Date and decision</p>
        </div>
      </Section>

      <footer className="mt-8 border-t border-[#bdb7a8] pt-3 text-[10px] leading-relaxed text-[#4a5566]">
        {brief.how_this_works}
      </footer>
    </article>
  );
}
