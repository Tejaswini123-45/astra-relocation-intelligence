import { Panel } from "@/components/primitives";
import { LiveBoard } from "@/components/live-board";
import { api, API_BASE, tryFetch } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function LivePage() {
  const [summary, baselineZones, habitations, sites, state, zones, plan, feed] =
    await Promise.all([
      tryFetch(api.riskSummary),
      tryFetch(api.riskZones),
      tryFetch(api.habitations),
      tryFetch(api.sites),
      tryFetch(api.live),
      tryFetch(api.liveZones),
      tryFetch(api.livePlan),
      tryFetch(api.eventFeed),
    ]);

  if (!summary || !baselineZones || !habitations || !sites || !state || !zones || !plan) {
    return (
      <div className="p-6">
        <Panel
          title="Live pipeline unavailable"
          subtitle={`No response from ${API_BASE}. ASTRA shows no live state rather than an assumed one.`}
        >
          <div className="px-4 py-4 text-[12px] text-[var(--color-ink-muted)]">
            Live operations reads the standing picture from the API: baseline
            plus every observation ingested so far. With the API down there is
            no picture to stand on, and inventing one would be the opposite of
            what this screen is for. Start the API and reload.
          </div>
        </Panel>
      </div>
    );
  }

  return (
    <LiveBoard
      studyArea={summary.study_area}
      baselineZones={baselineZones.features}
      habitations={habitations.habitations}
      sites={sites.sites}
      initialState={state}
      initialZones={zones.features}
      initialPlan={plan}
      feed={feed}
    />
  );
}
