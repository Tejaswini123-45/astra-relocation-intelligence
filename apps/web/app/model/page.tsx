import type { Constant, DatasetRecord, FormulaSpec, LayerDescriptor } from "@astra/contracts";
import { PROVENANCE_ORDER } from "@astra/contracts";

import { Notice, Panel, ProvenanceChip, StatValue } from "@/components/primitives";
import { ValidationPanel } from "@/components/validation-panel";
import { api, API_BASE, tryFetch } from "@/lib/api";

export const dynamic = "force-dynamic";

const CONSTANT_GROUPS: { prefixes: string[]; title: string; blurb: string }[] = [
  {
    title: "Hazard susceptibility",
    prefixes: ["hazard.", "w.landslide.", "w.flood.", "w.cloudburst.", "w.coastal."],
    blurb: "Factor weights per hazard sub-model, the composite rule and the zone thresholds.",
  },
  {
    title: "Exposure, vulnerability and phasing",
    prefixes: ["priority.", "exposure.", "vulnerability.", "history.", "tier."],
    blurb: "How consequence is separated from hazard, and how the three phases are drawn.",
  },
  {
    title: "Carrying capacity and site gates",
    prefixes: ["capacity.", "gate."],
    blurb:
      "Per-service norms and the hard suitability gates. Published minima carry their standard.",
  },
  {
    title: "Route reliability",
    prefixes: ["route.", "livelihood."],
    blurb: "Segment failure probabilities, the safest-route objective and the reliability floor.",
  },
  {
    title: "Optimisation",
    prefixes: ["opt."],
    blurb: "Objective weights, solver limits and the household-integrity floor.",
  },
  {
    title: "Confidence and validation",
    prefixes: ["confidence.", "validation."],
    blurb: "How evidence confidence is computed, and how the model is back-tested.",
  },
];

function groupOf(constant: Constant): string {
  for (const group of CONSTANT_GROUPS) {
    if (group.prefixes.some((prefix) => constant.key.startsWith(prefix))) {
      return group.title;
    }
  }
  return "Other";
}

function ApiDown() {
  return (
    <div className="p-6">
      <Panel
        title="API unreachable"
        subtitle={`No response from ${API_BASE}. ASTRA shows nothing rather than showing a plausible number.`}
      >
        <div className="px-4 py-4 text-[12px] leading-relaxed text-[var(--color-ink-muted)]">
          Start the backend and reload:
          <code className="mt-2 block rounded border border-[var(--color-line)] bg-[var(--color-surface-inset)] px-3 py-2 text-[var(--color-ink)]">
            uvicorn astra.main:app --app-dir apps/api --port 8000
          </code>
        </div>
      </Panel>
    </div>
  );
}

export default async function ModelAndProvenancePage() {
  const [config, provenance, layers, validation, scenarios, model] =
    await Promise.all([
      tryFetch(api.modelConfig),
      tryFetch(api.provenance),
      tryFetch(api.layers),
      tryFetch(api.fixtureValidation),
      tryFetch(api.scenarios),
      // The back-test artifact. Absent until scripts/backtest.py has been run,
      // and the screen says so rather than leaving a gap where evidence should
      // be.
      tryFetch(api.validation),
    ]);

  if (!config || !provenance || !layers || !validation || !scenarios) {
    return <ApiDown />;
  }

  const notices = config.notices;
  const scenario = scenarios.scenarios[0];
  const studyArea = scenarios.study_areas[0];
  const cited = config.constants.filter((c) => c.citation);

  return (
    <div className="flex flex-col gap-5 p-5">
      <section className="rounded border border-[var(--color-line)] bg-[var(--color-surface)] px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="max-w-3xl">
            <p className="text-[10px] uppercase tracking-[0.18em] text-[var(--color-ink-faint)]">
              Active scenario
            </p>
            <h1 className="mt-1.5 text-[19px] font-semibold text-[var(--color-ink)]">
              {scenario.name}
            </h1>
            <p className="mt-1 text-[12px] text-[var(--color-ink-muted)]">
              {studyArea.name} &middot; {studyArea.district}, {studyArea.state}
            </p>
            <p className="mt-3 text-[12px] leading-relaxed text-[var(--color-ink-muted)]">
              {notices.how_this_works}
            </p>
          </div>
          <div className="flex gap-8">
            <StatValue
              value={String(config.constants.length)}
              label="Model constants"
            />
            <StatValue value={String(config.formulas.length)} label="Documented formulas" />
            <StatValue
              value={String(provenance.datasets.length)}
              label="Registered datasets"
            />
            <StatValue
              value={validation.ok ? "PASS" : "FAIL"}
              label="Integrity gate"
              tone={validation.ok ? "safe" : "critical"}
            />
          </div>
        </div>
        <div className="mt-4 flex flex-col gap-2 border-t border-[var(--color-line)] pt-3">
          <Notice tone="warning">{scenario.disclaimer}</Notice>
          <Notice>{notices.decision_authority}</Notice>
        </div>
      </section>

      {model ? (
        <ValidationPanel validation={model} />
      ) : (
        <Panel
          title="Model validation"
          subtitle="Back-test, weight sensitivity and the confidence surface."
        >
          <div className="px-4 py-4 text-[12px] leading-relaxed text-[var(--color-ink-muted)]">
            The validation artifact has not been built in this deployment. ASTRA
            shows no back-test figures rather than plausible ones. Produce them
            with{" "}
            <code className="rounded border border-[var(--color-line)] bg-[var(--color-surface-inset)] px-1.5 py-0.5 text-[var(--color-ink)]">
              python scripts/backtest.py
            </code>
            .
          </div>
        </Panel>
      )}

      <Panel
        title="Fixture integrity gate"
        subtitle="Runs in CI and again on API startup. The API refuses to start on invalid fixtures, so no screen can render a number computed from a broken dataset."
        actions={
          <span
            className="numeric rounded-sm border px-2 py-1 text-[11px]"
            style={{
              borderColor: validation.ok ? "var(--color-safe)" : "var(--color-critical)",
              color: validation.ok ? "var(--color-safe)" : "var(--color-critical)",
            }}
          >
            {validation.summary}
          </span>
        }
      >
        <ul className="divide-y divide-[var(--color-line)]">
          {validation.checks.map((check) => (
            <li
              key={check}
              className="flex items-center gap-3 px-4 py-2 text-[12px] text-[var(--color-ink-muted)]"
            >
              <span aria-hidden style={{ color: "var(--color-safe)" }}>
                &#10003;
              </span>
              {check}
            </li>
          ))}
          {validation.warnings.map((warning) => (
            <li
              key={warning}
              className="flex items-center gap-3 px-4 py-2 text-[12px]"
              style={{ color: "var(--color-warning)" }}
            >
              <span aria-hidden>!</span>
              {warning}
            </li>
          ))}
          {validation.errors.map((error) => (
            <li
              key={error}
              className="flex items-center gap-3 px-4 py-2 text-[12px]"
              style={{ color: "var(--color-critical)" }}
            >
              <span aria-hidden>&times;</span>
              {error}
            </li>
          ))}
        </ul>
      </Panel>

      <Panel
        title="Data provenance"
        subtitle={provenance.note}
        actions={
          <div className="flex flex-wrap gap-2">
            {PROVENANCE_ORDER.map((klass) => (
              <span key={klass} className="flex items-center gap-1.5">
                <ProvenanceChip provenance={klass} compact />
                <span className="numeric text-[11px] text-[var(--color-ink-muted)]">
                  {provenance.counts_by_class[klass] ?? 0}
                </span>
              </span>
            ))}
          </div>
        }
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] border-collapse text-left text-[12px]">
            <thead>
              <tr className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
                <th className="px-4 py-2 font-medium">Dataset</th>
                <th className="px-4 py-2 font-medium">Class</th>
                <th className="px-4 py-2 font-medium">Source</th>
                <th className="px-4 py-2 font-medium">Processing</th>
                <th className="px-4 py-2 font-medium">Resolution</th>
                <th className="px-4 py-2 font-medium">Coverage</th>
                <th className="px-4 py-2 font-medium">Licence</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-line)] text-[var(--color-ink-muted)]">
              {provenance.datasets.map((dataset: DatasetRecord) => (
                <tr key={dataset.id} className="align-top">
                  <td className="px-4 py-3">
                    <div className="text-[var(--color-ink)]">{dataset.name}</div>
                    <div className="numeric text-[10px] text-[var(--color-ink-faint)]">
                      {dataset.id}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <ProvenanceChip provenance={dataset.provenance} compact />
                  </td>
                  <td className="px-4 py-3">
                    {dataset.source_url ? (
                      <a
                        className="underline decoration-dotted underline-offset-2 hover:text-[var(--color-ink)]"
                        href={dataset.source_url}
                        rel="noreferrer noopener"
                        target="_blank"
                      >
                        {dataset.source}
                      </a>
                    ) : (
                      dataset.source
                    )}
                  </td>
                  <td className="max-w-[320px] px-4 py-3">{dataset.processing}</td>
                  <td className="numeric px-4 py-3">{dataset.resolution}</td>
                  <td className="numeric px-4 py-3">{dataset.temporal_coverage}</td>
                  <td className="px-4 py-3">{dataset.licence}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel
        title="Layer catalogue"
        subtitle="Layers are declared with their backing datasets and an honest availability flag. A layer whose engine has not been built is listed but disabled - never rendered empty."
        actions={
          <span className="numeric text-[11px] text-[var(--color-ink-muted)]">
            {layers.available_count} / {layers.declared_count} available
          </span>
        }
      >
        <ul className="grid gap-px bg-[var(--color-line)] sm:grid-cols-2 xl:grid-cols-3">
          {layers.layers.map((layer: LayerDescriptor) => (
            <li
              key={layer.id}
              className="bg-[var(--color-surface)] px-4 py-3"
              style={{ opacity: layer.available ? 1 : 0.55 }}
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-[12px] text-[var(--color-ink)]">{layer.title}</div>
                  <div className="numeric text-[10px] text-[var(--color-ink-faint)]">
                    {layer.id}
                  </div>
                </div>
                <span
                  className="numeric shrink-0 rounded-sm border px-1.5 py-0.5 text-[9px] uppercase tracking-[0.1em]"
                  style={{
                    borderColor: layer.available
                      ? "color-mix(in srgb, var(--color-safe) 45%, transparent)"
                      : "var(--color-line-strong)",
                    color: layer.available ? "var(--color-safe)" : "var(--color-ink-faint)",
                  }}
                >
                  {layer.available ? "Live" : "Not yet built"}
                </span>
              </div>
              <p className="mt-2 text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
                {layer.description}
              </p>
              <div className="mt-2">
                <ProvenanceChip provenance={layer.provenance} compact />
              </div>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel
        title="Model configuration"
        subtitle={config.config.disclaimer}
        actions={
          <span className="numeric text-[11px] text-[var(--color-ink-muted)]">
            v{config.config.version} &middot; {cited.length} standard-derived
          </span>
        }
      >
        <div className="divide-y divide-[var(--color-line)]">
          {CONSTANT_GROUPS.map((group) => {
            const rows = config.constants.filter((c) => groupOf(c) === group.title);
            if (rows.length === 0) return null;
            return (
              <div key={group.title} className="px-4 py-4">
                <h3 className="text-[12px] font-semibold text-[var(--color-ink)]">
                  {group.title}
                </h3>
                <p className="mt-1 text-[11px] text-[var(--color-ink-muted)]">{group.blurb}</p>
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full min-w-[760px] border-collapse text-left text-[12px]">
                    <thead>
                      <tr className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
                        <th className="py-2 pr-4 font-medium">Key</th>
                        <th className="py-2 pr-4 text-right font-medium">Value</th>
                        <th className="py-2 pr-4 font-medium">Unit</th>
                        <th className="py-2 pr-4 font-medium">Class</th>
                        <th className="py-2 font-medium">Meaning</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[var(--color-line)] text-[var(--color-ink-muted)]">
                      {rows.map((constant: Constant) => (
                        <tr key={constant.key} className="align-top">
                          <td className="numeric py-2 pr-4 text-[var(--color-ink)]">
                            {constant.key}
                          </td>
                          <td className="numeric py-2 pr-4 text-right text-[var(--color-ink)]">
                            {constant.value}
                          </td>
                          <td className="numeric py-2 pr-4">{constant.unit ?? "-"}</td>
                          <td className="py-2 pr-4">
                            <ProvenanceChip provenance={constant.provenance} compact />
                          </td>
                          <td className="py-2">
                            {constant.description}
                            {constant.citation ? (
                              <span className="mt-1 block text-[11px] italic text-[var(--color-ink-faint)]">
                                {constant.citation}
                              </span>
                            ) : null}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })}
        </div>
      </Panel>

      <Panel
        title="Formula registry"
        subtitle="Every computed value in ASTRA names one of these. That is what makes a number traceable to its arithmetic in a single hop."
      >
        <ul className="divide-y divide-[var(--color-line)]">
          {config.formulas.map((formula: FormulaSpec) => (
            <li key={formula.formula_id} className="px-4 py-3">
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <span className="text-[12px] text-[var(--color-ink)]">{formula.title}</span>
                <span className="numeric text-[10px] text-[var(--color-ink-faint)]">
                  {formula.formula_id} &middot; v{formula.version} &middot; {formula.engine}
                </span>
              </div>
              <code className="mt-2 block overflow-x-auto rounded border border-[var(--color-line)] bg-[var(--color-surface-inset)] px-3 py-2 text-[11px] text-[var(--color-ink)]">
                {formula.expression}
              </code>
              <p className="mt-2 text-[11px] text-[var(--color-ink-muted)]">
                <span className="text-[var(--color-ink-faint)]">Inputs: </span>
                {formula.inputs.join(", ")}
              </p>
              {formula.notes ? (
                <p className="mt-1 text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
                  {formula.notes}
                </p>
              ) : null}
              {formula.citation ? (
                <p className="mt-1 text-[11px] italic text-[var(--color-ink-faint)]">
                  {formula.citation}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      </Panel>

      <footer className="flex flex-col gap-2 pb-4 text-[11px] text-[var(--color-ink-faint)]">
        <span>{notices.priority_not_probability}</span>
        <span>{notices.site_tenure_limitation}</span>
      </footer>
    </div>
  );
}
