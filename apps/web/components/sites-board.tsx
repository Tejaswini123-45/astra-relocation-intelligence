"use client";

import type {
  GateResult,
  InterventionResponse,
  ServiceCapacity,
  SiteCapacityListResponse,
  SiteCapacityResponse,
} from "@astra/contracts";
import { useMemo, useState } from "react";

import { ProvenanceChip } from "@/components/primitives";

const SERVICE_LABEL: Record<string, string> = {
  LAND: "Land",
  SHELTER: "Shelter",
  WATER: "Water",
  SANITATION: "Sanitation",
  HEALTHCARE: "Healthcare",
  POWER: "Power",
  ACCESS: "Access",
};

const GATE_LABEL: Record<string, string> = {
  OUTSIDE_HAZARD_ZONES: "Outside hazard zones",
  SLOPE_BUILDABLE: "Build-safe slope",
  LANDCOVER_PERMITTED: "Land cover permits building",
  ABOVE_FLOOD_LEVEL: "Above flood level",
  ROAD_ACCESSIBLE: "Road accessible",
};

/**
 * Norms arrive as a list of inspectable constants. Read them by their stable
 * key, never by position: the sentence under the capacity bars must keep
 * naming the norm it actually quotes.
 */
function norm(
  norms: SiteCapacityListResponse["norms"],
  key: string,
): { value: number; unit: string | null } {
  const found = norms.find((entry) => entry.key === key);
  return { value: found?.value ?? 0, unit: found?.unit ?? null };
}

function num(value: number, digits = 0): string {
  return value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/**
 * The capacity bar. Every service is drawn against the largest of them, so the
 * binding service is the shortest bar on the screen and impossible to miss.
 *
 * Two distinct states, deliberately drawn differently. Red is what binds
 * capacity now. When an intervention row is hovered, the service that *would*
 * bind after it is outlined in amber - the bars themselves do not move, because
 * the API returns the projected effective capacity and the next binding service,
 * not a projected per-service table, and this screen never computes a figure the
 * API did not send.
 */
function ServiceBars({
  services,
  bottleneck,
  projectedBottleneck,
}: {
  services: ServiceCapacity[];
  bottleneck: string | null | undefined;
  projectedBottleneck: string | null | undefined;
}) {
  const ceiling = Math.max(...services.map((s) => s.capacity_persons), 1);
  return (
    <ul className="flex flex-col gap-1.5">
      {services.map((service) => {
        const binding = service.service === bottleneck;
        const projected = !binding && service.service === projectedBottleneck;
        const width = (service.capacity_persons / ceiling) * 100;
        const accent = binding
          ? "var(--color-critical)"
          : projected
            ? "var(--color-warning)"
            : null;
        return (
          <li key={service.service} className="grid grid-cols-[92px_1fr_84px] items-center gap-2">
            <span
              className="text-[11px]"
              style={{ color: accent ?? "var(--color-ink-muted)" }}
            >
              {SERVICE_LABEL[service.service] ?? service.service}
            </span>
            <span
              className="relative block h-3 rounded-sm bg-[var(--color-surface-inset)]"
              style={
                projected
                  ? { outline: "1px dashed var(--color-warning)", outlineOffset: "1px" }
                  : undefined
              }
            >
              <span
                className="absolute inset-y-0 left-0 rounded-sm"
                style={{
                  width: `${Math.max(width, 1)}%`,
                  background:
                    accent ?? "color-mix(in srgb, var(--color-signal) 55%, transparent)",
                }}
              />
            </span>
            <span
              className="numeric text-right text-[11px]"
              style={{ color: accent ?? "var(--color-ink)" }}
            >
              {num(service.capacity_persons)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function GateList({ gates }: { gates: GateResult[] }) {
  return (
    <ul className="flex flex-col gap-1.5">
      {gates.map((gate) => (
        <li key={gate.gate} className="flex items-start gap-2 text-[11px]">
          <span
            aria-hidden
            className="mt-0.5 shrink-0"
            style={{
              color: gate.passed ? "var(--color-safe)" : "var(--color-critical)",
            }}
          >
            {gate.passed ? "✓" : "×"}
          </span>
          <span className="min-w-0">
            <span className="text-[var(--color-ink)]">
              {GATE_LABEL[gate.gate] ?? gate.gate}
            </span>
            <span className="numeric ml-2 text-[10px] text-[var(--color-ink-faint)]">
              {gate.observed === null || gate.observed === undefined
                ? ""
                : `${num(gate.observed, 1)}${gate.unit ? " " + gate.unit : ""}`}
              {gate.threshold === null || gate.threshold === undefined
                ? ""
                : ` vs ${num(gate.threshold, 0)}`}
            </span>
            <span className="mt-0.5 block text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
              {gate.detail}
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

function InterventionTable({
  interventions,
  onHover,
}: {
  interventions: InterventionResponse[];
  onHover: (intervention: InterventionResponse | null) => void;
}) {
  const best = Math.max(...interventions.map((i) => i.capacity_gain), 1);
  return (
    <table className="w-full border-collapse text-left text-[11px] text-[var(--color-ink-muted)]">
      <thead>
        <tr className="text-[9px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)]">
          <th className="pb-1 pr-2 font-medium">One unit of</th>
          <th className="pb-1 pr-2 text-right font-medium">Capacity</th>
          <th className="pb-1 pr-2 text-right font-medium">Gain</th>
          <th className="pb-1 font-medium">Next binding constraint</th>
        </tr>
      </thead>
      <tbody>
        {interventions.map((intervention) => (
          <tr
            key={intervention.service}
            onMouseEnter={() => onHover(intervention)}
            onMouseLeave={() => onHover(null)}
            className="align-top"
          >
            <td className="py-1 pr-2 text-[var(--color-ink)]">
              {SERVICE_LABEL[intervention.service] ?? intervention.service}
              <span className="numeric ml-1 text-[9px] text-[var(--color-ink-faint)]">
                +{num(intervention.unit_size, intervention.unit_size < 10 ? 2 : 0)}{" "}
                {intervention.unit}
              </span>
            </td>
            <td className="numeric py-1 pr-2 text-right">
              {num(intervention.capacity_before)} &rarr;{" "}
              <span className="text-[var(--color-ink)]">
                {num(intervention.capacity_after)}
              </span>
            </td>
            <td className="py-1 pr-2">
              <span
                className="numeric block rounded-sm px-1.5 py-0.5 text-right"
                style={{
                  background: `linear-gradient(to left, color-mix(in srgb, var(--color-safe) 40%, transparent) ${(
                    (intervention.capacity_gain / best) *
                    100
                  ).toFixed(0)}%, transparent ${(
                    (intervention.capacity_gain / best) *
                    100
                  ).toFixed(0)}%)`,
                  color: intervention.unlocks
                    ? "var(--color-ink)"
                    : "var(--color-ink-faint)",
                }}
              >
                {intervention.capacity_gain > 0 ? "+" : ""}
                {num(intervention.capacity_gain)}
              </span>
            </td>
            <td className="py-1 text-[10px]">
              {intervention.next_bottleneck
                ? `${SERVICE_LABEL[intervention.next_bottleneck] ?? intervention.next_bottleneck} at ${num(
                    intervention.next_bottleneck_capacity ?? 0,
                  )}`
                : "-"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function SitesBoard({ capacity }: { capacity: SiteCapacityListResponse }) {
  const [selectedId, setSelectedId] = useState<string>(
    capacity.sites[0]?.site_id ?? "",
  );
  const [hovered, setHovered] = useState<InterventionResponse | null>(null);

  const selected: SiteCapacityResponse | null = useMemo(
    () => capacity.sites.find((site) => site.site_id === selectedId) ?? null,
    [capacity.sites, selectedId],
  );

  return (
    <div className="flex flex-col gap-5 p-5">
      <section className="rounded border border-[var(--color-line)] bg-[var(--color-surface)] px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="max-w-2xl">
            <p className="text-[10px] uppercase tracking-[0.18em] text-[var(--color-ink-faint)]">
              Carrying capacity
            </p>
            <h1 className="mt-1.5 text-[19px] font-semibold text-[var(--color-ink)]">
              Candidate relocation sites
            </h1>
            <p className="mt-1 text-[12px] leading-relaxed text-[var(--color-ink-muted)]">
              A site holds as many people as its scarcest service supports, not as
              many as its land would fit. Effective capacity is the minimum across
              services; the service that sets it is the bottleneck.
            </p>
          </div>
          <div className="flex flex-wrap gap-8">
            <div className="flex flex-col gap-1">
              <span className="numeric text-[22px] leading-none text-[var(--color-ink)]">
                {capacity.suitable_sites} / {capacity.sites.length}
              </span>
              <span className="text-[10px] uppercase tracking-[0.14em] text-[var(--color-ink-faint)]">
                Sites passing every gate
              </span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="numeric text-[22px] leading-none text-[var(--color-safe)]">
                {num(capacity.total_effective_capacity)}
              </span>
              <span className="text-[10px] uppercase tracking-[0.14em] text-[var(--color-ink-faint)]">
                Effective capacity, people
              </span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="numeric text-[22px] leading-none text-[var(--color-ink)]">
                {num(capacity.population_needing_relocation)}
              </span>
              <span className="text-[10px] uppercase tracking-[0.14em] text-[var(--color-ink-faint)]">
                Residents assessed
              </span>
            </div>
            <div className="flex flex-col gap-1">
              <span
                className="numeric text-[22px] leading-none"
                style={{
                  color:
                    capacity.unmet_demand > 0
                      ? "var(--color-critical)"
                      : "var(--color-safe)",
                }}
              >
                {num(capacity.unmet_demand)}
              </span>
              <span className="text-[10px] uppercase tracking-[0.14em] text-[var(--color-ink-faint)]">
                Unmet demand, people
              </span>
            </div>
          </div>
        </div>
        <p className="mt-4 border-l-2 border-[var(--color-warning)] pl-3 text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
          {capacity.limitation}
        </p>
      </section>

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
        <section className="rounded border border-[var(--color-line)] bg-[var(--color-surface)]">
          <header className="border-b border-[var(--color-line)] px-4 py-3">
            <h2 className="text-[12px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink)]">
              Sites by availability and capacity
            </h2>
          </header>
          <ul className="divide-y divide-[var(--color-line)]">
            {capacity.sites.map((site) => (
              <li key={site.site_id}>
                <button
                  type="button"
                  onClick={() => setSelectedId(site.site_id)}
                  aria-pressed={site.site_id === selectedId}
                  className="w-full px-4 py-3 text-left hover:bg-[var(--color-surface-raised)]"
                  style={{
                    background:
                      site.site_id === selectedId
                        ? "var(--color-surface-raised)"
                        : undefined,
                  }}
                >
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-[13px] text-[var(--color-ink)]">
                      {site.name}
                      <span className="numeric ml-2 text-[10px] text-[var(--color-ink-faint)]">
                        {site.site_id}
                      </span>
                    </span>
                    <span className="flex items-baseline gap-2">
                      {!site.suitable ? (
                        <span
                          className="numeric rounded-sm border px-1 text-[9px] uppercase"
                          style={{
                            borderColor: "var(--color-critical)",
                            color: "var(--color-critical)",
                          }}
                        >
                          gate failed
                        </span>
                      ) : null}
                      <span
                        className="numeric text-[15px]"
                        style={{
                          color: site.suitable
                            ? "var(--color-ink)"
                            : "var(--color-ink-faint)",
                        }}
                      >
                        {num(site.effective_capacity)}
                      </span>
                    </span>
                  </div>
                  <div className="mt-1 flex items-center justify-between gap-3 text-[10px] text-[var(--color-ink-faint)]">
                    <span className="numeric">
                      theoretical {num(site.theoretical_capacity)} &middot; usable{" "}
                      {num(site.usable_area.usable_ha, 1)} ha
                    </span>
                    <span style={{ color: "var(--color-critical)" }}>
                      {!site.suitable
                        ? `fails ${site.failed_gates.length === 1 ? (GATE_LABEL[site.failed_gates[0]] ?? site.failed_gates[0]).toLowerCase() : `${site.failed_gates.length} gates`}`
                        : site.bottleneck
                          ? `bottleneck: ${(SERVICE_LABEL[site.bottleneck] ?? site.bottleneck).toLowerCase()}`
                          : ""}
                    </span>
                  </div>
                </button>
              </li>
            ))}
          </ul>
          <footer className="border-t border-[var(--color-line)] px-4 py-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
              What binds capacity across the district
            </p>
            <ul className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-[var(--color-ink-muted)]">
              {Object.entries(capacity.bottleneck_counts).map(([service, count]) => (
                <li key={service}>
                  {SERVICE_LABEL[service] ?? service}{" "}
                  <span className="numeric text-[var(--color-ink)]">{count}</span>
                </li>
              ))}
            </ul>
          </footer>
        </section>

        {selected ? (
          <section className="rounded border border-[var(--color-line)] bg-[var(--color-surface)]">
            <header className="border-b border-[var(--color-line)] px-4 py-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-[15px] font-semibold text-[var(--color-ink)]">
                    {selected.name}
                  </h2>
                  <p className="numeric mt-0.5 text-[10px] text-[var(--color-ink-faint)]">
                    {selected.site_id} &middot; {num(selected.elevation_m)} m &middot;{" "}
                    {num(selected.distance_to_road_m)} m to road
                  </p>
                </div>
                <div className="text-right">
                  <p className="numeric text-[26px] leading-none text-[var(--color-ink)]">
                    {num(selected.effective_capacity)}
                  </p>
                  <p className="text-[10px] text-[var(--color-ink-faint)]">
                    effective capacity, people
                  </p>
                </div>
              </div>
              {!selected.suitable ? (
                <p
                  className="mt-3 rounded-sm border px-3 py-2 text-[12px]"
                  style={{
                    borderColor: "var(--color-critical)",
                    background:
                      "color-mix(in srgb, var(--color-critical) 12%, transparent)",
                    color: "var(--color-ink)",
                  }}
                >
                  Not available for allocation. This site fails{" "}
                  {selected.failed_gates
                    .map((gate) => (GATE_LABEL[gate] ?? gate).toLowerCase())
                    .join(", ")}
                  . Gates are hard constraints, so its capacity is excluded from the
                  district total and from any allocation, whatever the figure below.
                </p>
              ) : null}
              {selected.bottleneck ? (
                <p
                  className="mt-3 rounded-sm border px-3 py-2 text-[12px]"
                  style={{
                    borderColor:
                      "color-mix(in srgb, var(--color-critical) 45%, transparent)",
                    background:
                      "color-mix(in srgb, var(--color-critical) 10%, transparent)",
                    color: "var(--color-ink)",
                  }}
                >
                  Binding constraint:{" "}
                  <strong>
                    {(SERVICE_LABEL[selected.bottleneck] ?? selected.bottleneck).toLowerCase()}
                  </strong>
                  . The land would hold {num(selected.theoretical_capacity)}; this
                  service supports {num(selected.effective_capacity)}.
                </p>
              ) : null}
            </header>

            <div className="border-b border-[var(--color-line)] px-4 py-3">
              <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
                Capacity by service, people
              </p>
              <div className="mt-2">
                <ServiceBars
                  services={selected.services}
                  bottleneck={selected.bottleneck}
                  projectedBottleneck={hovered ? hovered.next_bottleneck : null}
                />
              </div>
              {hovered ? (
                <p
                  className="mt-2 border-l-2 pl-2 text-[10px] leading-relaxed"
                  style={{
                    borderColor: "var(--color-warning)",
                    color: "var(--color-ink-muted)",
                  }}
                >
                  Projected: with{" "}
                  <span className="numeric">
                    +{num(hovered.unit_size, hovered.unit_size < 10 ? 2 : 0)}{" "}
                    {hovered.unit}
                  </span>{" "}
                  of {(SERVICE_LABEL[hovered.service] ?? hovered.service).toLowerCase()},
                  effective capacity{" "}
                  <span className="numeric">
                    {num(hovered.capacity_before)} &rarr; {num(hovered.capacity_after)}
                  </span>
                  {hovered.next_bottleneck
                    ? `, and ${(
                        SERVICE_LABEL[hovered.next_bottleneck] ?? hovered.next_bottleneck
                      ).toLowerCase()} becomes the binding constraint.`
                    : "."}{" "}
                  Bars show the assessment as it stands today.
                </p>
              ) : null}
              <p className="mt-2 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
                Norms are the published minima:{" "}
                {num(norm(capacity.norms, "capacity.water_lpcd").value)}{" "}
                {norm(capacity.norms, "capacity.water_lpcd").unit} for
                water, one latrine per{" "}
                {num(norm(capacity.norms, "capacity.persons_per_latrine").value)} people,{" "}
                {num(norm(capacity.norms, "capacity.site_area_m2_per_person").value)}{" "}
                {norm(capacity.norms, "capacity.site_area_m2_per_person").unit} of site
                area per person.
              </p>
            </div>

            <div className="border-b border-[var(--color-line)] px-4 py-3">
              <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
                Marginal intervention: what one unit unlocks
              </p>
              {selected.marginal_headline ? (
                <p className="mt-2 text-[12px] leading-relaxed text-[var(--color-ink)]">
                  {selected.marginal_headline}
                </p>
              ) : null}
              <div className="mt-2">
                <InterventionTable
                  interventions={selected.interventions}
                  onHover={setHovered}
                />
              </div>
            </div>

            <div className="border-b border-[var(--color-line)] px-4 py-3">
              <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
                Suitability gates
              </p>
              <div className="mt-2">
                <GateList gates={selected.gates} />
              </div>
            </div>

            <div className="px-4 py-3">
              <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
                Usable area
              </p>
              <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-[var(--color-ink-muted)]">
                <dt>Measured buildable footprint</dt>
                <dd className="numeric text-right text-[var(--color-ink)]">
                  {num(selected.usable_area.usable_ha, 2)} ha
                </dd>
                <dt>Search window</dt>
                <dd className="numeric text-right">
                  {num(selected.usable_area.radius_m)} m radius
                </dd>
                <dt>Buildable land cover in window</dt>
                <dd className="numeric text-right">
                  {(selected.usable_area.buildable_fraction * 100).toFixed(0)}%
                </dd>
                <dt>Within slope limit</dt>
                <dd className="numeric text-right">
                  {(selected.usable_area.slope_pass_fraction * 100).toFixed(0)}%
                </dd>
                <dt>Above flood level</dt>
                <dd className="numeric text-right">
                  {(selected.usable_area.flood_pass_fraction * 100).toFixed(0)}%
                </dd>
                <dt>Footprint mean slope</dt>
                <dd className="numeric text-right">
                  {num(selected.usable_area.footprint_mean_slope_deg, 1)}&deg;
                </dd>
              </dl>
              <p className="mt-2 flex flex-wrap items-center gap-2 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
                <ProvenanceChip provenance="REAL_OPEN" compact />
                {selected.usable_area.method}
              </p>
              {selected.usable_area.refinement_note ? (
                <p className="mt-2 border-l-2 border-[var(--color-signal)] pl-2 text-[10px] leading-relaxed text-[var(--color-ink-muted)]">
                  {selected.usable_area.refinement_note} Usable-area confidence:{" "}
                  <span className="numeric">{selected.usable_area.confidence}</span>.
                </p>
              ) : null}
              {selected.pending_constraints.map((pending) => (
                <p
                  key={pending}
                  className="mt-2 border-l-2 border-[var(--color-neutral)] pl-2 text-[10px] leading-relaxed text-[var(--color-ink-faint)]"
                >
                  {pending}
                </p>
              ))}
            </div>
          </section>
        ) : null}
      </div>

      <footer className="pb-4 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
        {capacity.decision_authority}
      </footer>
    </div>
  );
}
