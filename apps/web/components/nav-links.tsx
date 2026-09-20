"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * The operational navigation, ordered as the decision is actually made: decide,
 * analyse, stress-test, then check why it should be trusted.
 */
const GROUPS: { label: string; items: [string, string][] }[] = [
  {
    label: "Decide",
    items: [
      ["/", "Command Centre"],
      ["/brief", "Decision Brief"],
    ],
  },
  {
    label: "Analyse",
    items: [
      ["/risk", "Risk Explorer"],
      ["/priority", "Habitation Priority"],
      ["/sites", "Relocation Sites"],
      ["/routes", "Access & Routes"],
      ["/plan", "Optimised Plan"],
    ],
  },
  {
    label: "Stress-test",
    items: [
      ["/simulate", "What-If Simulation"],
      ["/live", "Live Operations"],
    ],
  },
  {
    label: "Trust",
    items: [
      ["/evidence", "Evidence & Audit"],
      ["/model", "Model & Provenance"],
      ["/study-area", "Study Area & Data"],
    ],
  },
];

export function NavLinks() {
  const pathname = usePathname() ?? "/";
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 lg:flex-col lg:gap-3">
      {GROUPS.map((group) => (
        <div key={group.label}>
          <p className="hidden px-3 pb-1 text-[9px] uppercase tracking-[0.18em] text-[var(--color-ink-faint)] lg:block">
            {group.label}
          </p>
          <ul className="flex flex-wrap gap-1 lg:flex-col">
            {group.items.map(([href, label]) => {
              const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
              return (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className="block rounded border-l-2 px-3 py-1.5 text-[12px] hover:bg-[var(--color-surface-raised)]"
                    style={{
                      borderColor: active ? "var(--color-signal)" : "transparent",
                      background: active ? "var(--color-surface-raised)" : undefined,
                      color: active ? "var(--color-ink)" : "var(--color-ink-muted)",
                    }}
                  >
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </div>
  );
}
