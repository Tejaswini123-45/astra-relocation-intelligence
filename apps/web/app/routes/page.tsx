import { Panel } from "@/components/primitives";
import { RoutesBoard } from "@/components/routes-board";
import { api, API_BASE, tryFetch } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function RoutesPage() {
  const [assessment, summary, zones, habitations, sites] = await Promise.all([
    tryFetch(api.routes),
    tryFetch(api.riskSummary),
    tryFetch(api.riskZones),
    tryFetch(api.habitations),
    tryFetch(api.sites),
  ]);

  if (!assessment || !summary || !zones || !habitations || !sites) {
    return (
      <div className="p-6">
        <Panel
          title="Route engine unavailable"
          subtitle={`No response from ${API_BASE}. ASTRA reports no route rather than an assumed one.`}
        >
          <div className="px-4 py-4 text-[12px] text-[var(--color-ink-muted)]">
            The route graph is built from the vendored OpenStreetMap extract. Run
            scripts/ingest.py and start the API.
          </div>
        </Panel>
      </div>
    );
  }

  return (
    <RoutesBoard
      assessment={assessment}
      studyArea={summary.study_area}
      zones={zones.features}
      habitations={habitations.habitations}
      sites={sites.sites}
    />
  );
}
