import { CommandCentre } from "@/components/command-centre";
import { Panel } from "@/components/primitives";
import { api, API_BASE, tryFetch } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * The Command Centre. It reads the standing state - the latest live run if
 * observations have been ingested, the baseline otherwise - so the screen a judge
 * lands on is the picture every other screen is explaining.
 */
export default async function CommandCentrePage() {
  const [brief, summary, zones, habitations, sites, plan, live, feed] = await Promise.all([
    tryFetch(api.briefPreview),
    tryFetch(api.riskSummary),
    tryFetch(api.liveZones),
    tryFetch(api.habitations),
    tryFetch(api.sites),
    tryFetch(api.livePlan),
    tryFetch(api.live),
    tryFetch(api.eventFeed),
  ]);

  if (!brief || !summary || !zones || !habitations || !sites || !plan || !live) {
    return (
      <div className="p-6">
        <Panel
          title="Command centre unavailable"
          subtitle={`No response from ${API_BASE}. ASTRA shows no situation rather than an assumed one.`}
        >
          <div className="px-4 py-4 text-[12px] text-[var(--color-ink-muted)]">
            The command centre is built from the hazard, priority, capacity, route and
            optimiser engines. Start the API and reload.
          </div>
        </Panel>
      </div>
    );
  }

  return (
    <CommandCentre
      initialBrief={brief}
      studyArea={summary.study_area}
      initialZones={zones.features}
      habitations={habitations.habitations}
      sites={sites.sites}
      initialPlan={plan}
      initialLive={live}
      feed={feed}
    />
  );
}
