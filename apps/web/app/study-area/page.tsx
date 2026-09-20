import type {
  CandidateSite,
  DerivedLayerSummary,
  Habitation,
  ServiceSupply,
} from "@astra/contracts";

import { Notice, Panel, ProvenanceChip, StatValue } from "@/components/primitives";
import { TerrainPlate } from "@/components/terrain-plate";
import { api, API_BASE, TERRAIN_PREVIEW_URL, tryFetch } from "@/lib/api";

export const dynamic = "force-dynamic";

function num(value: number, digits = 0): string {
  return value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function ApiDown() {
  return (
    <div className="p-6">
      <Panel
        title="API unreachable"
        subtitle={`No response from ${API_BASE}. ASTRA shows nothing rather than showing a plausible number.`}
      >
        <div className="px-4 py-4 text-[12px] text-[var(--color-ink-muted)]">
          Start the backend and reload.
        </div>
      </Panel>
    </div>
  );
}

export default async function StudyAreaPage() {
  const [data, habitationsPayload, sitesPayload] = await Promise.all([
    tryFetch(api.studyAreaData),
    tryFetch(api.habitations),
    tryFetch(api.sites),
  ]);

  if (!data || !habitationsPayload || !sitesPayload) {
    return <ApiDown />;
  }

  const { study_area: area, grid, methods, layers, generation } = data;
  const habitations = habitationsPayload.habitations;
  const sites = sitesPayload.sites;
  const elevationLayer = layers.find((layer) => layer.name === "terrain_preview");
  const slopeLayer = layers.find((layer) => layer.name === "slope_deg");
  const handLayer = layers.find((layer) => layer.name === "hand_m");
  const measured = (generation?.measured_from_real_data ?? []) as string[];
  const assumed = (generation?.assumed_not_measured ?? []) as string[];

  return (
    <div className="flex flex-col gap-5 p-5">
      <section className="rounded border border-[var(--color-line)] bg-[var(--color-surface)] px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="max-w-3xl">
            <p className="text-[10px] uppercase tracking-[0.18em] text-[var(--color-ink-faint)]">
              Study area
            </p>
            <h1 className="mt-1.5 text-[19px] font-semibold text-[var(--color-ink)]">
              {area.name}
            </h1>
            <p className="mt-1 text-[12px] text-[var(--color-ink-muted)]">
              {area.district}, {area.state} &middot; {area.description}
            </p>
          </div>
          <div className="flex flex-wrap gap-8">
            <StatValue
              value={`${num(grid.rows)} x ${num(grid.cols)}`}
              label="Analysis grid"
            />
            <StatValue value={`${num(grid.cell_x_m, 1)} x ${num(grid.cell_y_m, 1)} m`} label="Cell size" />
            <StatValue
              value={
                elevationLayer
                  ? `${num(elevationLayer.min)}-${num(elevationLayer.max)} m`
                  : "-"
              }
              label="Elevation range"
            />
            <StatValue value={num(habitationsPayload.total_population)} label="Residents in scope" />
          </div>
        </div>
        <div className="mt-4 flex flex-col gap-2 border-t border-[var(--color-line)] pt-3">
          <Notice tone="warning">{habitationsPayload.disclaimer}</Notice>
          <Notice>{sitesPayload.limitation}</Notice>
        </div>
      </section>

      <Panel
        title="Corridor"
        subtitle="Shaded relief computed from the vendored Copernicus DEM. Habitations and candidate sites are drawn at the coordinates the generator placed them at."
        actions={
          <span className="numeric text-[11px] text-[var(--color-ink-muted)]">
            {habitations.length} habitations &middot; {sites.length} candidate sites
          </span>
        }
      >
        <div className="p-4">
          <TerrainPlate
            imageUrl={TERRAIN_PREVIEW_URL}
            bbox={data.terrain_preview_bbox}
            habitations={habitations}
            sites={sites}
          />
        </div>
      </Panel>

      <div className="grid gap-5 xl:grid-cols-2">
        <Panel
          title="What was measured from real data"
          subtitle="Placement, size and extent come from the real surfaces, not from a random generator."
        >
          <ul className="divide-y divide-[var(--color-line)]">
            {measured.map((entry) => (
              <li
                key={entry}
                className="flex gap-3 px-4 py-2.5 text-[12px] leading-relaxed text-[var(--color-ink-muted)]"
              >
                <span aria-hidden style={{ color: "var(--color-safe)" }}>
                  &#10003;
                </span>
                {entry}
              </li>
            ))}
          </ul>
        </Panel>

        <Panel
          title="What was assumed, not measured"
          subtitle="Stated here before anyone has to ask. Each assumption is a DEMO_CONFIG constant in the model configuration."
        >
          <ul className="divide-y divide-[var(--color-line)]">
            {assumed.map((entry) => (
              <li
                key={entry}
                className="flex gap-3 px-4 py-2.5 text-[12px] leading-relaxed text-[var(--color-ink-muted)]"
              >
                <span aria-hidden style={{ color: "var(--color-warning)" }}>
                  !
                </span>
                {entry}
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <Panel
        title="Habitations"
        subtitle="Synthetic, fictional, terrain-calibrated. Hazard and priority arrive with their engines; this is the exposure layer they will read."
        actions={<ProvenanceChip provenance="SYNTHETIC_CALIBRATED" compact />}
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] border-collapse text-left text-[12px]">
            <thead>
              <tr className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
                <th className="px-4 py-2 font-medium">ID</th>
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="px-4 py-2 text-right font-medium">Population</th>
                <th className="px-4 py-2 text-right font-medium">Households</th>
                <th className="px-4 py-2 text-right font-medium">Elevation</th>
                <th className="px-4 py-2 text-right font-medium">60+</th>
                <th className="px-4 py-2 text-right font-medium">Under 5</th>
                <th className="px-4 py-2 text-right font-medium">Disability</th>
                <th className="px-4 py-2 text-right font-medium">Kutcha share</th>
                <th className="px-4 py-2 text-right font-medium">Facilities</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-line)] text-[var(--color-ink-muted)]">
              {habitations.map((habitation: Habitation) => (
                <tr key={habitation.id}>
                  <td className="numeric px-4 py-2 text-[var(--color-ink)]">
                    {habitation.id}
                  </td>
                  <td className="px-4 py-2 text-[var(--color-ink)]">{habitation.name}</td>
                  <td className="numeric px-4 py-2 text-right">
                    {num(habitation.population)}
                  </td>
                  <td className="numeric px-4 py-2 text-right">
                    {num(habitation.households)}
                  </td>
                  <td className="numeric px-4 py-2 text-right">
                    {habitation.elevation_m === null || habitation.elevation_m === undefined
                      ? "-"
                      : `${num(habitation.elevation_m)} m`}
                  </td>
                  <td className="numeric px-4 py-2 text-right">
                    {num(habitation.demographics.elderly_60_plus)}
                  </td>
                  <td className="numeric px-4 py-2 text-right">
                    {num(habitation.demographics.children_under_5)}
                  </td>
                  <td className="numeric px-4 py-2 text-right">
                    {num(habitation.demographics.persons_with_disability)}
                  </td>
                  <td className="numeric px-4 py-2 text-right">
                    {((habitation.structure_mix.KUTCHA ?? 0) * 100).toFixed(0)}%
                  </td>
                  <td className="numeric px-4 py-2 text-right">
                    {num((habitation.critical_facilities ?? []).length)}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-[var(--color-line-strong)] text-[var(--color-ink)]">
                <td className="px-4 py-2" colSpan={2}>
                  Total
                </td>
                <td className="numeric px-4 py-2 text-right">
                  {num(habitationsPayload.total_population)}
                </td>
                <td className="numeric px-4 py-2 text-right">
                  {num(habitationsPayload.total_households)}
                </td>
                <td colSpan={6} />
              </tr>
            </tfoot>
          </table>
        </div>
      </Panel>

      <Panel
        title="Candidate relocation sites"
        subtitle="Extent measured as contiguous buildable ground on the real land-cover and slope surfaces. Service supply is an assumption and is labelled as one."
        actions={<ProvenanceChip provenance="SYNTHETIC_CALIBRATED" compact />}
      >
        <ul className="grid gap-px bg-[var(--color-line)] lg:grid-cols-2 2xl:grid-cols-3">
          {sites.map((site: CandidateSite) => (
            <li key={site.id} className="bg-[var(--color-surface)] px-4 py-3">
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-[13px] text-[var(--color-ink)]">{site.name}</span>
                <span className="numeric text-[11px] text-[var(--color-ink-faint)]">
                  {site.id}
                </span>
              </div>
              <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-[var(--color-ink-muted)]">
                <dt>Buildable extent</dt>
                <dd className="numeric text-right text-[var(--color-ink)]">
                  {num(site.gross_area_m2 / 10000, 1)} ha
                </dd>
                <dt>Mean slope</dt>
                <dd className="numeric text-right">{num(site.mean_slope_deg, 1)}&deg;</dd>
                <dt>Elevation</dt>
                <dd className="numeric text-right">{num(site.elevation_m)} m</dd>
                <dt>Distance to road</dt>
                <dd className="numeric text-right">{num(site.distance_to_road_m)} m</dd>
                <dt>Existing shelter units</dt>
                <dd className="numeric text-right">{num(site.existing_shelter_units)}</dd>
                <dt>Constructable units</dt>
                <dd className="numeric text-right">{num(site.constructable_units)}</dd>
              </dl>
              <div className="mt-3 border-t border-[var(--color-line)] pt-2">
                <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
                  Assumed service supply
                </p>
                <ul className="mt-1 grid grid-cols-2 gap-x-4 text-[11px] text-[var(--color-ink-muted)]">
                  {(site.services ?? []).map((service: ServiceSupply) => (
                    <li key={service.service} className="flex justify-between gap-2">
                      <span>{service.service.toLowerCase()}</span>
                      <span className="numeric text-[var(--color-ink)]">
                        {num(service.supply, service.supply < 100 ? 1 : 0)} {service.unit}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel
        title="Derived surfaces"
        subtitle="Computed once, offline, from the vendored DEM and land cover. Method and observed range for each."
        actions={
          <span className="numeric text-[11px] text-[var(--color-ink-muted)]">
            {layers.length} layers &middot;{" "}
            {num(layers.reduce((total, layer) => total + layer.bytes, 0) / 1e6, 1)} MB
          </span>
        }
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] border-collapse text-left text-[12px]">
            <thead>
              <tr className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
                <th className="px-4 py-2 font-medium">Layer</th>
                <th className="px-4 py-2 font-medium">Unit</th>
                <th className="px-4 py-2 text-right font-medium">Min</th>
                <th className="px-4 py-2 text-right font-medium">Mean</th>
                <th className="px-4 py-2 text-right font-medium">Max</th>
                <th className="px-4 py-2 font-medium">How it was computed</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-line)] text-[var(--color-ink-muted)]">
              {layers.map((layer: DerivedLayerSummary) => (
                <tr key={layer.name} className="align-top">
                  <td className="numeric px-4 py-2 text-[var(--color-ink)]">{layer.name}</td>
                  <td className="px-4 py-2">{layer.unit}</td>
                  <td className="numeric px-4 py-2 text-right">{num(layer.min, 1)}</td>
                  <td className="numeric px-4 py-2 text-right">{num(layer.mean, 1)}</td>
                  <td className="numeric px-4 py-2 text-right">{num(layer.max, 1)}</td>
                  <td className="px-4 py-2">{layer.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="border-t border-[var(--color-line)] px-4 py-3">
          <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
            Methods
          </p>
          <ul className="mt-1 grid gap-1 text-[11px] text-[var(--color-ink-muted)] sm:grid-cols-2">
            {Object.entries(methods).map(([key, value]) => (
              <li key={key}>
                <span className="numeric text-[var(--color-ink)]">{key}</span>: {value}
              </li>
            ))}
          </ul>
          {slopeLayer && handLayer ? (
            <p className="mt-3 text-[11px] text-[var(--color-ink-faint)]">
              Median terrain in this corridor runs to {num(slopeLayer.mean, 1)}&deg; mean
              slope, with height above nearest drainage reaching {num(handLayer.max)} m.
              These are the surfaces the hazard engine will read.
            </p>
          ) : null}
        </div>
      </Panel>
    </div>
  );
}
