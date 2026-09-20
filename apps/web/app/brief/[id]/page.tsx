import Link from "next/link";

import { PrintButton } from "@/components/brief-actions";
import { BriefDocument } from "@/components/brief-document";
import { Panel } from "@/components/primitives";
import { api, API_BASE, tryFetch } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function BriefPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const brief = await tryFetch(() => api.brief(id));

  if (!brief) {
    return (
      <div className="p-6">
        <Panel
          title="Brief not found"
          subtitle={`No brief '${id}' was returned by ${API_BASE}. ASTRA does not reconstruct a brief it did not freeze.`}
        >
          <div className="px-4 py-4 text-[12px]">
            <Link href="/brief" className="text-[var(--color-ink)] hover:underline">
              All briefs
            </Link>
          </div>
        </Panel>
      </div>
    );
  }

  const decisionId = brief.audit?.decision_id;
  const decision = decisionId ? await tryFetch(() => api.decision(decisionId)) : null;

  return (
    <div className="px-4 py-5 print:p-0">
      <div className="mx-auto mb-3 flex max-w-[1000px] flex-wrap items-center justify-between gap-3 print:hidden">
        <Link href="/brief" className="text-[12px] text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]">
          &larr; All briefs
        </Link>
        <div className="flex flex-wrap items-center gap-2">
          <Link
            href="/evidence"
            className="rounded-sm border border-[var(--color-line-strong)] px-3 py-1.5 text-[12px] text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
          >
            Approve or override
          </Link>
          <PrintButton />
        </div>
      </div>
      <BriefDocument brief={brief} decision={decision} />
    </div>
  );
}
