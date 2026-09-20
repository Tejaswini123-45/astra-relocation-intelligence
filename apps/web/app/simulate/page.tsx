import { Panel } from "@/components/primitives";
import { SimulateBoard } from "@/components/simulate-board";
import { api, API_BASE, tryFetch } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function SimulatePage() {
  const [summary, zones, habitations, sites, capacity, dependencies, plan] =
    await Promise.all([
      tryFetch(api.riskSummary),
      tryFetch(api.riskZones),
      tryFetch(api.habitations),
      tryFetch(api.sites),
      tryFetch(api.capacitySites),
      tryFetch(api.planDependencies),
      tryFetch(api.plan),
    ]);

  if (
    !summary ||
    !zones ||
    !habitations ||
    !sites ||
    !capacity ||
    !dependencies ||
    !plan
  ) {
    return (
      <div className="p-6">
        <Panel
          title="Scenario engine unavailable"
          subtitle={`No response from ${API_BASE}. ASTRA simulates nothing rather than showing a plausible difference.`}
        >
          <div className="px-4 py-4 text-[12px] text-[var(--color-ink-muted)]">
            A what-if runs the whole chain a second time - hazard, exposure,
            capacity, routes, optimiser - and compares it with the baseline the
            other screens are showing. Both halves of that comparison come from
            the API, so with the API down there is no honest before and no
            honest after. Start the API and reload.
          </div>
        </Panel>
      </div>
    );
  }

  return (
    <SimulateBoard
      studyArea={summary.study_area}
      zones={zones.features}
      habitations={habitations.habitations}
      sites={sites.sites}
      capacity={capacity}
      dependencies={dependencies}
      basePlan={plan}
    />
  );
}
