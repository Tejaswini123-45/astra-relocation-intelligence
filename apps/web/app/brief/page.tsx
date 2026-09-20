import Link from "next/link";

import { GenerateBriefButton } from "@/components/brief-actions";
import { Panel } from "@/components/primitives";
import { api, API_BASE, tryFetch } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function BriefIndexPage() {
  const listing = await tryFetch(api.briefs);

  return (
    <div className="flex flex-col gap-5 p-5">
      <section className="rounded border border-[var(--color-line)] bg-[var(--color-surface)] px-5 py-4">
        <p className="text-[10px] uppercase tracking-[0.18em] text-[var(--color-ink-faint)]">
          Decision brief
        </p>
        <h1 className="mt-1 text-[19px] font-semibold text-[var(--color-ink)]">
          The plan on one printable page, with its audit ID
        </h1>
        <p className="mt-2 max-w-3xl text-[12px] leading-relaxed text-[var(--color-ink-muted)]">
          A brief is the standing state of every engine - situation, priority queue,
          effective capacity, immediate, short-term and medium-term actions, route
          risks, confidence, assumptions and limitations - read off the same results
          the screens show. Generating one writes a decision-ledger row and freezes the
          brief exactly as generated, so a printed copy can always be traced to the
          state that produced it.
        </p>
        <div className="mt-3">
          <GenerateBriefButton />
        </div>
      </section>

      <Panel
        title={`Generated briefs (${listing?.total ?? 0})`}
        subtitle={listing?.decision_authority ?? `No response from ${API_BASE}.`}
      >
        {!listing ? (
          <p className="px-4 py-4 text-[12px] text-[var(--color-ink-muted)]">
            The API did not answer, so no briefs can be listed.
          </p>
        ) : listing.briefs.length === 0 ? (
          <p className="px-4 py-4 text-[12px] text-[var(--color-ink-muted)]">
            No brief has been generated yet.
          </p>
        ) : (
          <ul className="divide-y divide-[var(--color-line)]" data-testid="brief-list">
            {listing.briefs.map((entry) => (
              <li key={entry.id}>
                <Link
                  href={`/brief/${encodeURIComponent(entry.id)}`}
                  className="block px-4 py-3 hover:bg-[var(--color-surface-raised)]"
                >
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="numeric text-[12px] text-[var(--color-ink)]">{entry.id}</span>
                    <span className="numeric text-[10px] uppercase tracking-[0.1em] text-[var(--color-ink-faint)]">
                      {entry.basis} &middot; {entry.decision_id}
                    </span>
                  </div>
                  <p className="mt-1 text-[11px] leading-relaxed text-[var(--color-ink-muted)]">
                    {entry.headline}
                  </p>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
