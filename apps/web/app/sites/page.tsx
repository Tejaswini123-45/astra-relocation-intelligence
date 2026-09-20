import { Panel } from "@/components/primitives";
import { SitesBoard } from "@/components/sites-board";
import { api, API_BASE, tryFetch } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function SitesPage() {
  const capacity = await tryFetch(api.capacitySites);

  if (!capacity) {
    return (
      <div className="p-6">
        <Panel
          title="Capacity engine unavailable"
          subtitle={`No response from ${API_BASE}. ASTRA reports no capacity rather than an assumed one.`}
        >
          <div className="px-4 py-4 text-[12px] text-[var(--color-ink-muted)]">
            Start the API and reload.
          </div>
        </Panel>
      </div>
    );
  }

  return <SitesBoard capacity={capacity} />;
}
