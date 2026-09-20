import { EvidenceBoard } from "@/components/evidence-board";
import { Panel } from "@/components/primitives";
import { api, API_BASE, tryFetch } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function EvidencePage() {
  const [evidence, decisions, intents, narration] = await Promise.all([
    tryFetch(api.evidence),
    tryFetch(api.decisions),
    tryFetch(api.askIntents),
    tryFetch(api.narratePlan),
  ]);

  if (!evidence || !decisions || !intents) {
    return (
      <div className="p-6">
        <Panel
          title="Evidence ledger unavailable"
          subtitle={`No response from ${API_BASE}. ASTRA records decisions to an immutable ledger rather than memory.`}
        >
          <div className="px-4 py-4 text-[12px] text-[var(--color-ink-muted)]">
            Evidence and audit trails depend on the persistent store served by
            the API. Start the API and reload to access filed reports, decision
            records, and verifiable human overrides.
          </div>
        </Panel>
      </div>
    );
  }

  return (
    <EvidenceBoard
      initialEvidence={evidence}
      initialDecisions={decisions.decisions}
      intents={intents}
      narration={narration ?? null}
    />
  );
}
