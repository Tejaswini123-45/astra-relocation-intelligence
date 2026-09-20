import { Panel } from "@/components/primitives";
import { PriorityBoard } from "@/components/priority-board";
import { api, API_BASE, tryFetch } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function PriorityPage() {
  const [priority, summary, zones, sites, model] = await Promise.all([
    tryFetch(api.priorityHabitations),
    tryFetch(api.riskSummary),
    tryFetch(api.riskZones),
    tryFetch(api.sites),
    // Rank stability from the back-test. Absent until scripts/backtest.py has
    // run, and then no flag is shown - never an unearned "stable".
    tryFetch(api.validation),
  ]);

  if (!priority || !summary || !zones || !sites) {
    return (
      <div className="p-6">
        <Panel
          title="Priority engine unavailable"
          subtitle={`No response from ${API_BASE}. ASTRA ranks nothing rather than ranking on guesses.`}
        >
          <div className="px-4 py-4 text-[12px] text-[var(--color-ink-muted)]">
            Start the API and reload.
          </div>
        </Panel>
      </div>
    );
  }

  return (
    <PriorityBoard
      priority={priority}
      summary={summary}
      zones={zones.features}
      sites={sites.sites}
      stability={
        model
          ? Object.fromEntries(
              model.sensitivity.habitations.map((entry) => [
                entry.habitation_id,
                entry,
              ]),
            )
          : null
      }
    />
  );
}
