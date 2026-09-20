import { Panel } from "@/components/primitives";
import { RiskExplorer } from "@/components/risk-explorer";
import { api, API_BASE, tryFetch } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function RiskExplorerPage() {
  const [summary, zones, habitationHazards, sites] = await Promise.all([
    tryFetch(api.riskSummary),
    tryFetch(api.riskZones),
    tryFetch(api.riskHabitations),
    tryFetch(api.sites),
  ]);

  if (!summary || !zones || !habitationHazards || !sites) {
    return (
      <div className="p-6">
        <Panel
          title="Hazard engine unavailable"
          subtitle={`No response from ${API_BASE}. Run scripts/build_hazard.py and start the API; ASTRA renders nothing rather than a plausible surface.`}
        >
          <div className="px-4 py-4 text-[12px] text-[var(--color-ink-muted)]">
            The map draws only what the engine computed.
          </div>
        </Panel>
      </div>
    );
  }

  return (
    <RiskExplorer
      summary={summary}
      zones={zones.features}
      habitationHazards={habitationHazards}
      sites={sites.sites}
    />
  );
}
