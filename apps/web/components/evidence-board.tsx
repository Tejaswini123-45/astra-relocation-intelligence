"use client";

import type {
  AskResponse,
  DecisionResponse,
  EvidenceListResponse,
  EvidenceRecordResponse,
  IntentResponse,
  NarrationResponse,
} from "@astra/contracts";
import { useCallback, useMemo, useRef, useState } from "react";

import { Notice, Panel } from "@/components/primitives";
import {
  api,
  API_BASE,
  ApiRefusedError,
  askAstra,
  fileEvidence,
  overrideDecision,
  promoteEvidence,
  recordDecision,
} from "@/lib/api";

const KIND_COLOUR: Record<string, string> = {
  LANDSLIDE: "var(--color-critical)",
  FLOOD: "var(--color-signal)",
  GROUND_INSTABILITY: "var(--color-warning)",
  STRUCTURAL_DAMAGE: "var(--color-warning)",
  ROAD_BLOCKED: "var(--color-neutral)",
  RAINFALL: "var(--color-signal)",
  OTHER: "var(--color-ink-faint)",
};

const STATE_COLOUR: Record<string, string> = {
  COMPUTED: "var(--color-ink-muted)",
  UNDER_REVIEW: "var(--color-warning)",
  APPROVED: "var(--color-safe)",
  OVERRIDDEN: "var(--color-warning)",
  REQUIRES_REVIEW: "var(--color-critical)",
};

function when(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleString("en-IN", {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      });
}

/**
 * Evidence, the decision ledger and the ask layer.
 *
 * This is where ASTRA stops computing and a person takes over. Filing a report
 * changes nothing; promoting it to a live observation is a separate, deliberate
 * act, and the record says which report became which event. An override always
 * carries a reason, and what it costs is re-solved rather than asserted.
 */
export function EvidenceBoard({
  initialEvidence,
  initialDecisions,
  intents,
  narration,
}: {
  initialEvidence: EvidenceListResponse;
  initialDecisions: DecisionResponse[];
  intents: IntentResponse[];
  narration: NarrationResponse | null;
}) {
  const [evidence, setEvidence] = useState(initialEvidence);
  const [decisions, setDecisions] = useState(initialDecisions);
  const [selectedId, setSelectedId] = useState<string | null>(
    initialDecisions[0]?.id ?? null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [question, setQuestion] = useState("");
  const formRef = useRef<HTMLFormElement | null>(null);

  const selected = useMemo(
    () => decisions.find((entry) => entry.id === selectedId) ?? null,
    [decisions, selectedId],
  );

  const refresh = useCallback(async () => {
    const [nextEvidence, nextDecisions] = await Promise.all([
      api.evidence(),
      api.decisions(),
    ]);
    setEvidence(nextEvidence);
    setDecisions(nextDecisions.decisions);
    if (!nextDecisions.decisions.some((entry) => entry.id === selectedId)) {
      setSelectedId(nextDecisions.decisions[0]?.id ?? null);
    }
  }, [selectedId]);

  const guard = useCallback(
    async (work: () => Promise<string | null>) => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        setNotice(await work());
        await refresh();
      } catch (thrown) {
        setNotice(null);
        setError(
          thrown instanceof ApiRefusedError
            ? thrown.detail
            : "The API did not answer. Nothing was recorded.",
        );
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  const submitEvidence = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      // Blank optional fields are omitted rather than sent empty: an empty
      // coordinate is not a coordinate at zero.
      for (const [key, value] of [...form.entries()]) {
        if (typeof value === "string" && !value.trim()) form.delete(key);
        if (value instanceof File && value.size === 0) form.delete(key);
      }
      await guard(async () => {
        const record = await fileEvidence(form);
        formRef.current?.reset();
        return `Filed ${record.id}: classified ${record.kind
          .replace(/_/g, " ")
          .toLowerCase()} at severity ${record.severity?.toFixed(2)}.`;
      });
    },
    [guard],
  );

  const promote = useCallback(
    (record: EvidenceRecordResponse) =>
      guard(async () => {
        const result = await promoteEvidence(record.id);
        return `${record.id} is now live observation ${result.event_id}; pipeline run ${result.run_id} is executing on it.`;
      }),
    [guard],
  );

  const capture = useCallback(
    () =>
      guard(async () => {
        const record = await recordDecision({ trigger: "manual" });
        setSelectedId(record.id);
        return `Recorded ${record.id} against input hash ${record.input_summary_hash}.`;
      }),
    [guard],
  );

  const decide = useCallback(
    (
      decisionId: string,
      body: Parameters<typeof overrideDecision>[1],
    ) =>
      guard(async () => {
        const record = await overrideDecision(decisionId, body);
        const latest = record.overrides[record.overrides.length - 1];
        const consequence = latest?.consequence as
          | { headline?: string }
          | undefined;
        return (
          `${record.id} is now ${record.state.replace(/_/g, " ").toLowerCase()}.` +
          (consequence?.headline ? ` ${consequence.headline}` : "")
        );
      }),
    [guard],
  );

  const submitQuestion = useCallback(
    async (text: string, intentId?: string) => {
      setBusy(true);
      setError(null);
      try {
        setAnswer(await askAstra({ question: text, intent_id: intentId ?? null }));
      } catch (thrown) {
        setAnswer(null);
        setError(
          thrown instanceof ApiRefusedError
            ? thrown.detail
            : "The API did not answer that question.",
        );
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  return (
    <div className="flex flex-col gap-5 p-5">
      <section className="rounded border border-[var(--color-line)] bg-[var(--color-surface)] px-5 py-4">
        <p className="text-[10px] uppercase tracking-[0.18em] text-[var(--color-ink-faint)]">
          Evidence &amp; audit
        </p>
        <h1 className="mt-1 text-[19px] font-semibold text-[var(--color-ink)]">
          What was reported, what was decided, and by whom
        </h1>
        <p className="mt-2 max-w-3xl text-[12px] leading-relaxed text-[var(--color-ink-muted)]">
          Filing a report changes no assessment. Promoting it to a live
          observation is a separate act, and the record says which report became
          which event. Every override carries a reason, and what it costs is
          re-solved through the same optimiser rather than asserted.
        </p>
        {error ? (
          <p
            className="mt-3 text-[12px] text-[var(--color-critical)]"
            data-testid="board-error"
          >
            {error}
          </p>
        ) : null}
        {notice ? (
          <p
            className="mt-3 text-[12px] text-[var(--color-safe)]"
            data-testid="board-notice"
          >
            {notice}
          </p>
        ) : null}
      </section>

      <div className="grid gap-5 xl:grid-cols-2">
        <Panel
          title="File a field report"
          subtitle="Classified by documented keyword and pattern rules that run with no language model configured."
        >
          <form
            ref={formRef}
            onSubmit={submitEvidence}
            className="flex flex-col gap-3 px-4 py-4"
            data-testid="evidence-form"
          >
            <Field label="What was observed" htmlFor="ev-text">
              <textarea
                id="ev-text"
                name="text"
                required
                minLength={3}
                rows={4}
                placeholder="Fresh tension cracks and seepage on the slope above the settlement, widening since yesterday."
                className="w-full rounded-sm border bg-[var(--color-surface-inset)] px-2 py-1.5 text-[12px] leading-relaxed text-[var(--color-ink)]"
                style={{ borderColor: "var(--color-line-strong)" }}
              />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Reported by" htmlFor="ev-reporter">
                <input
                  id="ev-reporter"
                  name="reporter"
                  required
                  placeholder="R. Bisht"
                  className="w-full rounded-sm border bg-[var(--color-surface-inset)] px-2 py-1.5 text-[12px] text-[var(--color-ink)]"
                  style={{ borderColor: "var(--color-line-strong)" }}
                />
              </Field>
              <Field label="Role" htmlFor="ev-role">
                <input
                  id="ev-role"
                  name="role"
                  placeholder="District field officer"
                  className="w-full rounded-sm border bg-[var(--color-surface-inset)] px-2 py-1.5 text-[12px] text-[var(--color-ink)]"
                  style={{ borderColor: "var(--color-line-strong)" }}
                />
              </Field>
              <Field label="Longitude" htmlFor="ev-lon">
                <input
                  id="ev-lon"
                  name="lon"
                  type="number"
                  step="0.0001"
                  placeholder="79.3174"
                  className="numeric w-full rounded-sm border bg-[var(--color-surface-inset)] px-2 py-1.5 text-[12px] text-[var(--color-ink)]"
                  style={{ borderColor: "var(--color-line-strong)" }}
                />
              </Field>
              <Field label="Latitude" htmlFor="ev-lat">
                <input
                  id="ev-lat"
                  name="lat"
                  type="number"
                  step="0.0001"
                  placeholder="30.4324"
                  className="numeric w-full rounded-sm border bg-[var(--color-surface-inset)] px-2 py-1.5 text-[12px] text-[var(--color-ink)]"
                  style={{ borderColor: "var(--color-line-strong)" }}
                />
              </Field>
            </div>
            <Field label="Photograph (optional)" htmlFor="ev-photo">
              <input
                id="ev-photo"
                name="photo"
                type="file"
                accept="image/jpeg,image/png,image/webp"
                className="w-full text-[11px] text-[var(--color-ink-muted)] file:mr-2 file:rounded-sm file:border file:border-[var(--color-line-strong)] file:bg-transparent file:px-2 file:py-1 file:text-[11px] file:text-[var(--color-ink-muted)]"
              />
            </Field>
            <Field label="Analyst note (optional)" htmlFor="ev-note">
              <input
                id="ev-note"
                name="analyst_note"
                placeholder="Cross-checked against the 2013 inventory."
                className="w-full rounded-sm border bg-[var(--color-surface-inset)] px-2 py-1.5 text-[12px] text-[var(--color-ink)]"
                style={{ borderColor: "var(--color-line-strong)" }}
              />
            </Field>
            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={busy}
                data-testid="evidence-submit"
                className="rounded-sm border px-3 py-1.5 text-[12px] disabled:opacity-40"
                style={{ borderColor: "var(--color-signal)", color: "var(--color-ink)" }}
              >
                {busy ? "Filing…" : "File report"}
              </button>
              <span className="text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
                Filing records the report. It does not change any assessment.
              </span>
            </div>
          </form>
        </Panel>

        <Panel
          title={`Filed reports (${evidence.total})`}
          subtitle={evidence.extraction_note}
        >
          {evidence.evidence.length === 0 ? (
            <p className="px-4 py-4 text-[12px] text-[var(--color-ink-muted)]">
              Nothing filed yet.
            </p>
          ) : (
            <ul
              className="max-h-[520px] divide-y divide-[var(--color-line)] overflow-y-auto"
              data-testid="evidence-list"
            >
              {evidence.evidence.map((record) => (
                <li key={record.id} className="px-4 py-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span
                      className="text-[10px] uppercase tracking-[0.1em]"
                      style={{ color: KIND_COLOUR[record.kind] }}
                    >
                      {record.kind.replace(/_/g, " ")}
                    </span>
                    <span className="numeric text-[10px] text-[var(--color-ink-faint)]">
                      {record.id} · {when(record.received_at)}
                    </span>
                  </div>
                  <p className="mt-1 text-[12px] leading-relaxed text-[var(--color-ink)]">
                    {record.text}
                  </p>
                  <p className="mt-1 text-[10px] text-[var(--color-ink-muted)]">
                    {record.reporter}
                    {record.role ? ` · ${record.role}` : ""}
                    {record.lon !== null && record.lat !== null
                      ? ` · ${record.lat.toFixed(4)}, ${record.lon.toFixed(4)}`
                      : " · no location given"}
                  </p>
                  {record.photo_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={`${API_BASE}${record.photo_url}`}
                      alt={`Photograph filed with ${record.id}`}
                      className="mt-2 max-h-40 rounded-sm border border-[var(--color-line)]"
                    />
                  ) : null}
                  <p className="mt-1.5 border-l-2 border-[var(--color-line-strong)] pl-2 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
                    <span className="uppercase tracking-[0.08em]">
                      {record.extraction_mode === "model"
                        ? "Rules, refined by model"
                        : "Rules"}
                    </span>{" "}
                    · severity{" "}
                    <span className="numeric">{record.severity?.toFixed(2)}</span> ·
                    confidence {record.confidence.toLowerCase()} —{" "}
                    {String(
                      (record.extracted as Record<string, unknown>).rationale ?? "",
                    )}
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {record.ingested_event_id ? (
                      <span className="numeric rounded-sm border border-[var(--color-safe)] px-1.5 py-0.5 text-[10px] text-[var(--color-safe)]">
                        ingested as {record.ingested_event_id}
                      </span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => promote(record)}
                        disabled={busy || !record.extracted}
                        data-testid="evidence-promote"
                        className="rounded-sm border px-2 py-0.5 text-[11px] disabled:opacity-40"
                        style={{
                          borderColor: "var(--color-warning)",
                          color: "var(--color-warning)",
                        }}
                      >
                        Promote to live observation
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <Panel
        title={`Decision ledger (${decisions.length})`}
        subtitle="Every run that could inform a decision leaves a row: the scenario, the versions, the layers, a hash of the inputs, the score components and the solver status."
        actions={
          <button
            type="button"
            onClick={capture}
            disabled={busy}
            data-testid="record-decision"
            className="rounded-sm border px-2.5 py-1 text-[11px] disabled:opacity-40"
            style={{ borderColor: "var(--color-signal)", color: "var(--color-ink)" }}
          >
            Record the current plan
          </button>
        }
      >
        {decisions.length === 0 ? (
          <p className="px-4 py-4 text-[12px] text-[var(--color-ink-muted)]">
            The ledger is empty. Record the current plan, or run the live pipeline
            or a what-if - both write a row.
          </p>
        ) : (
          <div className="grid gap-0 lg:grid-cols-[320px_minmax(0,1fr)]">
            <ul
              className="max-h-[460px] divide-y divide-[var(--color-line)] overflow-y-auto border-b border-[var(--color-line)] lg:border-b-0 lg:border-r"
              data-testid="decision-list"
            >
              {decisions.map((record) => (
                <li key={record.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(record.id)}
                    aria-pressed={record.id === selectedId}
                    className="w-full px-4 py-2.5 text-left hover:bg-[var(--color-surface-raised)]"
                    style={{
                      background:
                        record.id === selectedId
                          ? "var(--color-surface-raised)"
                          : undefined,
                    }}
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="numeric text-[11px] text-[var(--color-ink)]">
                        {record.id}
                      </span>
                      <span
                        className="text-[9px] uppercase tracking-[0.1em]"
                        style={{ color: STATE_COLOUR[record.state] }}
                      >
                        {record.state.replace(/_/g, " ")}
                      </span>
                    </div>
                    <p className="numeric mt-0.5 text-[10px] text-[var(--color-ink-faint)]">
                      {when(record.created_at)} · {record.trigger} ·{" "}
                      {record.solver_status}
                      {record.overrides.length
                        ? ` · ${record.overrides.length} override${record.overrides.length === 1 ? "" : "s"}`
                        : ""}
                    </p>
                  </button>
                </li>
              ))}
            </ul>

            {selected ? (
              <DecisionDetail
                record={selected}
                busy={busy}
                onDecide={(body) => decide(selected.id, body)}
              />
            ) : null}
          </div>
        )}
      </Panel>

      <div className="grid gap-5 xl:grid-cols-2">
        <Panel
          title="Ask ASTRA"
          subtitle="A fixed set of questions, each answered by running the same engine accessors the screens use. There is no query language here, and no path to a filesystem, a shell or a database."
        >
          <div className="px-4 py-4">
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void submitQuestion(question);
              }}
              className="flex gap-2"
            >
              <input
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="Which habitations are the highest priority?"
                data-testid="ask-input"
                className="min-w-0 flex-1 rounded-sm border bg-[var(--color-surface-inset)] px-2 py-1.5 text-[12px] text-[var(--color-ink)]"
                style={{ borderColor: "var(--color-line-strong)" }}
              />
              <button
                type="submit"
                disabled={busy || !question.trim()}
                data-testid="ask-submit"
                className="rounded-sm border px-3 py-1.5 text-[12px] disabled:opacity-40"
                style={{ borderColor: "var(--color-signal)", color: "var(--color-ink)" }}
              >
                Ask
              </button>
            </form>

            <p className="mt-3 text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
              Everything it can answer
            </p>
            <ul className="mt-1.5 flex flex-wrap gap-1">
              {intents.map((intent) => (
                <li key={intent.id}>
                  <button
                    type="button"
                    onClick={() => {
                      const filled = intent.needs === "habitation"
                        ? intent.question
                        : intent.needs === "site"
                          ? intent.question
                          : intent.question;
                      setQuestion(filled);
                      void submitQuestion(filled, intent.id);
                    }}
                    className="rounded-sm border border-[var(--color-line-strong)] px-1.5 py-0.5 text-[10px] text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
                  >
                    {intent.question}
                  </button>
                </li>
              ))}
            </ul>

            {answer ? (
              <div className="mt-4" data-testid="ask-answer">
                <p className="text-[12px] leading-relaxed text-[var(--color-ink)]">
                  {answer.text}
                </p>
                <p className="mt-1.5 text-[10px] text-[var(--color-ink-faint)]">
                  Intent <span className="numeric">{answer.intent}</span> ·{" "}
                  {answer.matched_on}
                </p>
                <details className="mt-2">
                  <summary className="cursor-pointer text-[10px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)]">
                    The structured result this was written from
                  </summary>
                  <pre className="numeric mt-1.5 max-h-56 overflow-auto rounded-sm border border-[var(--color-line)] bg-[var(--color-surface-inset)] px-2 py-1.5 text-[10px] leading-relaxed text-[var(--color-ink-muted)]">
                    {JSON.stringify(answer.data, null, 2)}
                  </pre>
                </details>
                <p className="mt-2 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
                  {answer.note}
                </p>
              </div>
            ) : null}
          </div>
        </Panel>

        <Panel
          title="Plan advisory"
          subtitle="The computed plan turned into prose for an official who is not a GIS analyst. The model never computes; where one is configured, every number it writes is checked against the computed result before it is shown."
          actions={
            narration ? (
              <span
                className="numeric rounded-sm border px-2 py-1 text-[11px]"
                style={{
                  borderColor: "var(--color-line-strong)",
                  color: "var(--color-ink-muted)",
                }}
                data-testid="narration-mode"
              >
                {narration.mode === "model" ? "MODEL" : "TEMPLATE MODE"}
              </span>
            ) : null
          }
        >
          <div className="px-4 py-4">
            {narration ? (
              <>
                <p
                  className="text-[13px] leading-relaxed text-[var(--color-ink)]"
                  data-testid="narration-text"
                >
                  {narration.text}
                </p>
                <p className="mt-3 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
                  {narration.note}
                </p>
              </>
            ) : (
              <p className="text-[12px] text-[var(--color-ink-muted)]">
                No advisory available; the plan endpoint did not answer.
              </p>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label
        htmlFor={htmlFor}
        className="block text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]"
      >
        {label}
      </label>
      <div className="mt-1">{children}</div>
    </div>
  );
}

function DecisionDetail({
  record,
  busy,
  onDecide,
}: {
  record: DecisionResponse;
  busy: boolean;
  onDecide: (body: Parameters<typeof overrideDecision>[1]) => void;
}) {
  const [actor, setActor] = useState("SDMA duty officer");
  const [reason, setReason] = useState("");
  const [habitation, setHabitation] = useState("");
  const [site, setSite] = useState("");
  const totals =
    ((record.score_components as Record<string, unknown>).totals as Record<
      string,
      number
    >) ?? {};

  return (
    <div className="px-4 py-3" data-testid="decision-detail">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="numeric text-[13px] text-[var(--color-ink)]">
          {record.id}
        </span>
        <span
          className="text-[10px] uppercase tracking-[0.1em]"
          style={{ color: STATE_COLOUR[record.state] }}
        >
          {record.state.replace(/_/g, " ")}
        </span>
      </div>

      <dl className="mt-2 grid grid-cols-2 gap-x-5 gap-y-1 text-[10px] sm:grid-cols-3">
        {[
          ["Scenario", record.scenario_id],
          ["Trigger", record.trigger],
          ["Run", record.run_id ?? "—"],
          ["Engine", record.engine_version],
          ["Model config", record.model_config_version],
          ["Input hash", record.input_summary_hash],
          ["Solver", record.solver_status],
          [
            "Objective",
            record.objective_value === null
              ? "—"
              : record.objective_value.toLocaleString("en-IN", {
                  maximumFractionDigits: 0,
                }),
          ],
          ["Confidence", record.confidence],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="text-[var(--color-ink-faint)]">{label}</dt>
            <dd className="numeric text-[var(--color-ink-muted)]">{value}</dd>
          </div>
        ))}
      </dl>

      <p className="mt-2 text-[11px] text-[var(--color-ink-muted)]">
        Placed{" "}
        <span className="numeric text-[var(--color-ink)]">
          {(totals.population_assigned ?? 0).toLocaleString("en-IN")}
        </span>{" "}
        of{" "}
        <span className="numeric">
          {(totals.population_assessed ?? 0).toLocaleString("en-IN")}
        </span>{" "}
        across{" "}
        <span className="numeric">{totals.sites_used ?? 0}</span> site(s) ·{" "}
        {record.source_layer_ids.length} source layers
      </p>

      {record.overrides.length > 0 ? (
        <div className="mt-3">
          <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
            Override trail
          </p>
          <ul className="mt-1.5 flex flex-col gap-2" data-testid="override-trail">
            {record.overrides.map((entry) => {
              const consequence = entry.consequence as Record<string, unknown>;
              return (
                <li
                  key={entry.id}
                  className="border-l-2 pl-2"
                  style={{ borderColor: "var(--color-warning)" }}
                >
                  <p className="text-[11px] text-[var(--color-ink)]">
                    <span className="uppercase tracking-[0.08em]">
                      {entry.action.replace(/_/g, " ")}
                    </span>{" "}
                    by {entry.actor}
                    {entry.habitation_id
                      ? ` · ${entry.habitation_id} → ${entry.site_id}`
                      : ""}
                  </p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
                    “{entry.reason}”
                  </p>
                  {consequence?.headline ? (
                    <p
                      className="mt-1 text-[10px] leading-relaxed"
                      style={{
                        color: consequence.feasible
                          ? "var(--color-ink-faint)"
                          : "var(--color-critical)",
                      }}
                    >
                      Consequence, re-solved: {String(consequence.headline)}
                    </p>
                  ) : null}
                  <p className="numeric mt-0.5 text-[9px] text-[var(--color-ink-faint)]">
                    {when(entry.created_at)} · {entry.id}
                  </p>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      <div className="mt-3 border-t border-[var(--color-line)] pt-3">
        <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-faint)]">
          Record a decision
        </p>
        <div className="mt-1.5 grid gap-2 sm:grid-cols-2">
          <input
            value={actor}
            onChange={(event) => setActor(event.target.value)}
            aria-label="Deciding officer"
            className="rounded-sm border bg-[var(--color-surface-inset)] px-2 py-1 text-[11px] text-[var(--color-ink)]"
            style={{ borderColor: "var(--color-line-strong)" }}
          />
          <input
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            aria-label="Reason"
            data-testid="override-reason"
            placeholder="Reason (required)"
            className="rounded-sm border bg-[var(--color-surface-inset)] px-2 py-1 text-[11px] text-[var(--color-ink)]"
            style={{ borderColor: "var(--color-line-strong)" }}
          />
          <input
            value={habitation}
            onChange={(event) => setHabitation(event.target.value.toUpperCase())}
            aria-label="Habitation to redirect"
            placeholder="H-04"
            className="numeric rounded-sm border bg-[var(--color-surface-inset)] px-2 py-1 text-[11px] text-[var(--color-ink)]"
            style={{ borderColor: "var(--color-line-strong)" }}
          />
          <input
            value={site}
            onChange={(event) => setSite(event.target.value.toUpperCase())}
            aria-label="Destination site"
            placeholder="S-01"
            className="numeric rounded-sm border bg-[var(--color-surface-inset)] px-2 py-1 text-[11px] text-[var(--color-ink)]"
            style={{ borderColor: "var(--color-line-strong)" }}
          />
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy || !reason.trim()}
            data-testid="approve-decision"
            onClick={() =>
              onDecide({ actor, action: "APPROVE", reason, habitation_id: null, site_id: null, people: null })
            }
            className="rounded-sm border px-2.5 py-1 text-[11px] disabled:opacity-40"
            style={{ borderColor: "var(--color-safe)", color: "var(--color-safe)" }}
          >
            Approve
          </button>
          <button
            type="button"
            disabled={busy || !reason.trim() || !habitation || !site}
            data-testid="force-assignment"
            onClick={() =>
              onDecide({
                actor,
                action: "FORCE_ASSIGNMENT",
                reason,
                habitation_id: habitation,
                site_id: site,
                people: null,
              })
            }
            className="rounded-sm border px-2.5 py-1 text-[11px] disabled:opacity-40"
            style={{
              borderColor: "var(--color-warning)",
              color: "var(--color-warning)",
            }}
          >
            Override: send {habitation || "…"} to {site || "…"}
          </button>
          <button
            type="button"
            disabled={busy || !reason.trim()}
            onClick={() =>
              onDecide({ actor, action: "ANNOTATE", reason, habitation_id: null, site_id: null, people: null })
            }
            className="rounded-sm px-2 py-1 text-[11px] text-[var(--color-ink-faint)] hover:text-[var(--color-ink)] disabled:opacity-40"
          >
            Annotate
          </button>
        </div>
        <div className="mt-2">
          <Notice>{record.decision_authority}</Notice>
        </div>
      </div>
    </div>
  );
}
