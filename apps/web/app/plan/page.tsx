import { Panel } from "@/components/primitives";
import { PlanBoard } from "@/components/plan-board";
import { api, API_BASE, tryFetch } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function PlanPage() {
  const [plan, summary, zones, habitations, sites] = await Promise.all([
    tryFetch(api.plan),
    tryFetch(api.riskSummary),
    tryFetch(api.riskZones),
    tryFetch(api.habitations),
    tryFetch(api.sites),
  ]);

  if (!plan || !summary || !zones || !habitations || !sites) {
    return (
      <div className="p-6">
        <Panel
          title="Optimiser unavailable"
          subtitle={`No response from ${API_BASE}. ASTRA renders no plan rather than a plausible one.`}
        >
          <div className="px-4 py-4 text-[12px] text-[var(--color-ink-muted)]">
            The plan is solved by OR-Tools CP-SAT over the capacity and route
            engines. Start the API and reload.
          </div>
        </Panel>
      </div>
    );
  }

  return (
    <PlanBoard
      plan={plan}
      studyArea={summary.study_area}
      zones={zones.features}
      habitations={habitations.habitations}
      sites={sites.sites}
    />
  );
}
