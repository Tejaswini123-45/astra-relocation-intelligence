import type { ValidationResponse } from "@astra/contracts";

import { Notice, Panel } from "@/components/primitives";

/**
 * The credibility layer, rendered exactly as the back-test produced it.
 *
 * Nothing on this screen is chosen for how it reads. The uncross-validated AUC
 * sits beside the cross-validated one and is labelled as not independent; the
 * terrain-only figure is lower than both and is shown anyway, because it answers
 * the strictest form of the question. The sample size and the confidence
 * interval travel with every number, because an AUC from eighteen points without
 * an interval is a number pretending to be a measurement.
 */

function pct(value: number, digits = 0): string {
  return Number.isFinite(value) ? `${(value * 100).toFixed(digits)}%` : "—";
}

function fixed(value: number, digits = 2): string {
  return Number.isFinite(value) ? value.toFixed(digits) : "—";
}

/** Success-rate curve, drawn from the points the back-test computed. */
function SuccessCurve({
  variants,
}: {
  variants: ValidationResponse["backtest"]["variants"];
}) {
  const width = 260;
  const height = 150;
  const pad = { left: 30, right: 8, top: 8, bottom: 22 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const x = (share: number) => pad.left + share * plotW;
  const y = (share: number) => pad.top + (1 - share) * plotH;

  const COLOUR: Record<string, string> = {
    as_deployed: "var(--color-ink-faint)",
    spatial_cv: "var(--color-signal)",
    terrain_only: "var(--color-warning)",
  };

  return (
    <figure className="m-0 min-w-0">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Success-rate curve: share of recorded incidents captured against share of the corridor classified"
        className="w-full"
      >
        <line
          x1={x(0)}
          y1={y(0)}
          x2={x(1)}
          y2={y(1)}
          stroke="var(--color-line-strong)"
          strokeDasharray="3 3"
          strokeWidth={1}
        />
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
          <g key={tick}>
            <line
              x1={pad.left}
              y1={y(tick)}
              x2={width - pad.right}
              y2={y(tick)}
              stroke="var(--color-line)"
              strokeWidth={0.6}
            />
            <text
              x={pad.left - 5}
              y={y(tick) + 3}
              textAnchor="end"
              fontSize={7}
              fill="var(--color-ink-faint)"
            >
              {tick * 100}
            </text>
            <text
              x={x(tick)}
              y={height - 8}
              textAnchor="middle"
              fontSize={7}
              fill="var(--color-ink-faint)"
            >
              {tick * 100}
            </text>
          </g>
        ))}
        {variants.map((variant) => (
          <polyline
            key={variant.id}
            fill="none"
            stroke={COLOUR[variant.id] ?? "var(--color-ink-muted)"}
            strokeWidth={variant.id === "spatial_cv" ? 1.8 : 1.1}
            strokeDasharray={variant.independent ? undefined : "4 3"}
            points={variant.success_curve
              .map((point) => `${x(point.area_share)},${y(point.incident_share)}`)
              .join(" ")}
          />
        ))}
      </svg>
      <figcaption className="mt-1 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
        Share of recorded incidents captured (vertical) against share of the
        corridor classified as most susceptible (horizontal). The dashed diagonal
        is what random classification would achieve. A dashed curve is a variant
        that is not independent of the points it predicts.
      </figcaption>
    </figure>
  );
}

export function ValidationPanel({ validation }: { validation: ValidationResponse }) {
  const { backtest, sensitivity, confidence } = validation;
  const headline = backtest.variants.find((variant) => variant.id === "spatial_cv");

  return (
    <>
      <Panel
        title="Is the model any good? Back-test against the recorded inventory"
        subtitle="The composite susceptibility surface scored as a predictor of where landslides have actually happened, against sampled background points. Produced by scripts/backtest.py with a fixed seed; the API serves the artifact and never recomputes it per request."
        actions={
          <span
            className="numeric rounded-sm border px-2 py-1 text-[11px]"
            style={{
              borderColor: validation.stale
                ? "var(--color-warning)"
                : "var(--color-line-strong)",
              color: validation.stale
                ? "var(--color-warning)"
                : "var(--color-ink-muted)",
            }}
          >
            {validation.stale ? "STALE" : "CURRENT"} &middot; config{" "}
            {validation.model_config_version}
          </span>
        }
      >
        <div className="px-4 py-4">
          {validation.stale ? (
            <div className="mb-3">
              <Notice tone="warning">{validation.staleness_note}</Notice>
            </div>
          ) : null}

          <p
            className="text-[13px] leading-relaxed text-[var(--color-ink)]"
            data-testid="backtest-headline"
          >
            {backtest.headline}
          </p>

          <div className="mt-4 grid gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
            <div className="min-w-0 overflow-x-auto">
              <table className="w-full min-w-[440px] border-collapse text-left text-[11px]">
                <thead>
                  <tr className="text-[9px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)]">
                    <th className="pb-1.5 pr-3 font-medium">Variant</th>
                    <th className="pb-1.5 pr-3 text-right font-medium">ROC-AUC</th>
                    <th className="pb-1.5 pr-3 text-right font-medium">95% CI</th>
                    <th className="pb-1.5 pr-3 text-right font-medium">Top 10%</th>
                    <th className="pb-1.5 text-right font-medium">Top 20%</th>
                  </tr>
                </thead>
                <tbody className="align-top">
                  {backtest.variants.map((variant) => (
                    <tr
                      key={variant.id}
                      className="border-t border-[var(--color-line)]"
                      data-testid={`backtest-${variant.id}`}
                    >
                      <td className="py-2 pr-3">
                        <span className="text-[var(--color-ink)]">{variant.name}</span>
                        <span
                          className="ml-2 rounded-sm border px-1 py-px text-[9px] uppercase tracking-[0.08em]"
                          style={{
                            borderColor: variant.independent
                              ? "var(--color-safe)"
                              : "var(--color-warning)",
                            color: variant.independent
                              ? "var(--color-safe)"
                              : "var(--color-warning)",
                          }}
                        >
                          {variant.independent ? "independent" : "not independent"}
                        </span>
                        <p className="mt-1 max-w-lg text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
                          {variant.note}
                        </p>
                      </td>
                      <td className="numeric py-2 pr-3 text-right text-[13px] text-[var(--color-ink)]">
                        {fixed(variant.auc)}
                      </td>
                      <td className="numeric py-2 pr-3 text-right text-[var(--color-ink-muted)]">
                        {fixed(variant.auc_ci_low)}&ndash;{fixed(variant.auc_ci_high)}
                      </td>
                      <td className="numeric py-2 pr-3 text-right text-[var(--color-ink-muted)]">
                        {pct(variant.top_10pct_capture)}
                      </td>
                      <td className="numeric py-2 text-right text-[var(--color-ink-muted)]">
                        {pct(variant.top_20pct_capture)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-[10px] text-[var(--color-ink-muted)] sm:grid-cols-4">
                <div>
                  <dt className="text-[var(--color-ink-faint)]">Incidents used</dt>
                  <dd className="numeric">
                    {backtest.incidents_in_study_area} of {backtest.incidents_total}
                  </dd>
                </div>
                <div>
                  <dt className="text-[var(--color-ink-faint)]">Background points</dt>
                  <dd className="numeric">
                    {(headline?.background ?? 0).toLocaleString("en-IN")}
                  </dd>
                </div>
                <div>
                  <dt className="text-[var(--color-ink-faint)]">Exclusion radius</dt>
                  <dd className="numeric">{backtest.exclusion_radius_m} m</dd>
                </div>
                <div>
                  <dt className="text-[var(--color-ink-faint)]">Seed</dt>
                  <dd className="numeric">{backtest.seed}</dd>
                </div>
              </dl>

              {backtest.per_hazard.length > 0 ? (
                <p className="mt-3 text-[11px] text-[var(--color-ink-muted)]">
                  <span className="text-[var(--color-ink-faint)]">
                    Per sub-model, as deployed:{" "}
                  </span>
                  {backtest.per_hazard
                    .map((entry) => `${entry.hazard.toLowerCase()} ${fixed(entry.auc)}`)
                    .join(" · ")}
                </p>
              ) : null}
            </div>

            <SuccessCurve variants={backtest.variants} />
          </div>

          <div className="mt-4">
            <Notice tone="warning">{backtest.limitation}</Notice>
          </div>
        </div>
      </Panel>

      <Panel
        title="Are the weights arbitrary? Monte Carlo rank stability"
        subtitle="Every factor weight and every priority weight perturbed independently, then the whole ranking recomputed. The question this answers is not whether the weights are debatable - they are - but whether the decision they produce depends on winning that debate."
      >
        <div className="px-4 py-4">
          <p
            className="text-[13px] leading-relaxed text-[var(--color-ink)]"
            data-testid="sensitivity-headline"
          >
            {sensitivity.headline}
          </p>

          <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-[11px] sm:grid-cols-5">
            {[
              ["Runs", sensitivity.runs.toLocaleString("en-IN")],
              ["Weights perturbed", String(sensitivity.weights_perturbed)],
              ["Spearman, median", fixed(sensitivity.spearman_median, 3)],
              ["Spearman, worst run", fixed(sensitivity.spearman_min, 3)],
              [
                `Top ${sensitivity.top_k} unchanged`,
                pct(sensitivity.top_k_unchanged_share),
              ],
            ].map(([label, value]) => (
              <div key={label}>
                <dt className="text-[9px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
                  {label}
                </dt>
                <dd className="numeric mt-0.5 text-[15px] text-[var(--color-ink)]">
                  {value}
                </dd>
              </div>
            ))}
          </dl>

          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[520px] border-collapse text-left text-[11px]">
              <thead>
                <tr className="text-[9px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)]">
                  <th className="pb-1.5 pr-3 font-medium">Habitation</th>
                  <th className="pb-1.5 pr-3 text-right font-medium">Rank</th>
                  <th className="pb-1.5 pr-3 text-right font-medium">Range</th>
                  <th className="pb-1.5 pr-3 text-right font-medium">Priority</th>
                  <th className="pb-1.5 pr-3 text-right font-medium">5th&ndash;95th</th>
                  <th className="pb-1.5 font-medium">Stability</th>
                </tr>
              </thead>
              <tbody>
                {sensitivity.habitations.map((entry) => (
                  <tr
                    key={entry.habitation_id}
                    className="border-t border-[var(--color-line)]"
                    data-testid={`stability-${entry.habitation_id}`}
                  >
                    <td className="py-1.5 pr-3 text-[var(--color-ink)]">
                      <span className="numeric text-[var(--color-ink-faint)]">
                        {entry.habitation_id}
                      </span>{" "}
                      {entry.name}
                    </td>
                    <td className="numeric py-1.5 pr-3 text-right text-[var(--color-ink)]">
                      {entry.baseline_rank}
                    </td>
                    <td className="numeric py-1.5 pr-3 text-right text-[var(--color-ink-muted)]">
                      {entry.best_rank}&ndash;{entry.worst_rank}
                    </td>
                    <td className="numeric py-1.5 pr-3 text-right text-[var(--color-ink-muted)]">
                      {entry.baseline_priority.toFixed(1)}
                    </td>
                    <td className="numeric py-1.5 pr-3 text-right text-[var(--color-ink-muted)]">
                      {entry.priority_p05.toFixed(1)}&ndash;{entry.priority_p95.toFixed(1)}
                    </td>
                    <td className="py-1.5">
                      <span
                        className="rounded-sm border px-1.5 py-px text-[9px] uppercase tracking-[0.08em]"
                        style={{
                          borderColor: entry.stable
                            ? "var(--color-safe)"
                            : "var(--color-warning)",
                          color: entry.stable
                            ? "var(--color-safe)"
                            : "var(--color-warning)",
                        }}
                      >
                        {entry.stable ? "rank-stable" : "weight-sensitive"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="mt-3 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
            {sensitivity.method}
          </p>
        </div>
      </Panel>

      <Panel
        title="Where the evidence is thin: the confidence surface"
        subtitle="Computed separately from susceptibility and never multiplied into it. A cell can be highly susceptible on thin evidence, and an official has to be able to see that."
      >
        <div className="px-4 py-4">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-[11px] sm:grid-cols-4">
            {[
              ["Mean", fixed(confidence.mean, 2)],
              ["Median", fixed(confidence.median, 2)],
              ["Lowest cell", fixed(confidence.minimum, 2)],
              ["Highest cell", fixed(confidence.maximum, 2)],
            ].map(([label, value]) => (
              <div key={label}>
                <dt className="text-[9px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
                  {label}
                </dt>
                <dd className="numeric mt-0.5 text-[15px] text-[var(--color-ink)]">
                  {value}
                </dd>
              </div>
            ))}
          </dl>

          <table className="mt-4 w-full border-collapse text-left text-[11px]">
            <thead>
              <tr className="text-[9px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)]">
                <th className="pb-1.5 pr-3 font-medium">Band</th>
                <th className="pb-1.5 pr-3 text-right font-medium">Area</th>
                <th className="pb-1.5 pr-3 text-right font-medium">Share</th>
                <th className="pb-1.5 pr-3 text-right font-medium">Zones</th>
                <th className="pb-1.5 text-right font-medium">Habitations</th>
              </tr>
            </thead>
            <tbody>
              {confidence.bands.map((band) => (
                <tr key={band.band} className="border-t border-[var(--color-line)]">
                  <td className="py-1.5 pr-3 text-[var(--color-ink)]">{band.band}</td>
                  <td className="numeric py-1.5 pr-3 text-right text-[var(--color-ink-muted)]">
                    {band.area_km2.toLocaleString("en-IN", {
                      maximumFractionDigits: 0,
                    })}{" "}
                    km²
                  </td>
                  <td className="numeric py-1.5 pr-3 text-right text-[var(--color-ink-muted)]">
                    {pct(band.share, 1)}
                  </td>
                  <td className="numeric py-1.5 pr-3 text-right text-[var(--color-ink-muted)]">
                    {band.zones}
                  </td>
                  <td className="numeric py-1.5 text-right text-[var(--color-ink-muted)]">
                    {band.habitations}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <p className="mt-3 text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
            {confidence.note}
          </p>
        </div>
      </Panel>
    </>
  );
}
