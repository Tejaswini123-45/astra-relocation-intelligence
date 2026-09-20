import type { ProvenanceClass } from "@astra/contracts";
import { PROVENANCE_LABEL } from "@astra/contracts";
import type { ReactNode } from "react";

const PROVENANCE_COLOUR: Record<ProvenanceClass, string> = {
  REAL_OPEN: "var(--color-prov-real)",
  DERIVED: "var(--color-prov-derived)",
  SYNTHETIC_CALIBRATED: "var(--color-prov-synthetic)",
  DEMO_CONFIG: "var(--color-prov-demo)",
};

/**
 * The provenance chip. It appears next to every value whose origin matters, so
 * an ASTRA constant is never mistaken for a government rule (CLAUDE.md 4.2).
 * Colour is never the only signal: the class is always spelled out.
 */
export function ProvenanceChip({
  provenance,
  compact = false,
}: {
  provenance: ProvenanceClass;
  compact?: boolean;
}) {
  const colour = PROVENANCE_COLOUR[provenance];
  return (
    <span
      className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-sm border px-1.5 py-0.5 text-[10px] uppercase tracking-[0.08em]"
      style={{
        borderColor: `color-mix(in srgb, ${colour} 45%, transparent)`,
        backgroundColor: `color-mix(in srgb, ${colour} 12%, transparent)`,
        color: colour,
      }}
      title={PROVENANCE_LABEL[provenance]}
    >
      <span
        aria-hidden
        className="h-1.5 w-1.5 rounded-full"
        style={{ backgroundColor: colour }}
      />
      {compact ? provenance.replace("_", " ") : PROVENANCE_LABEL[provenance]}
    </span>
  );
}

export function Panel({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded border border-[var(--color-line)] bg-[var(--color-surface)]">
      <header className="flex items-start justify-between gap-4 border-b border-[var(--color-line)] px-4 py-3">
        <div>
          <h2 className="text-[13px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink)]">
            {title}
          </h2>
          {subtitle ? (
            <p className="mt-1 max-w-3xl text-[12px] leading-relaxed text-[var(--color-ink-muted)]">
              {subtitle}
            </p>
          ) : null}
        </div>
        {actions}
      </header>
      {children}
    </section>
  );
}

export function StatValue({
  value,
  label,
  tone = "neutral",
}: {
  value: string;
  label: string;
  tone?: "neutral" | "safe" | "warning" | "critical";
}) {
  const colour = {
    neutral: "var(--color-ink)",
    safe: "var(--color-safe)",
    warning: "var(--color-warning)",
    critical: "var(--color-critical)",
  }[tone];
  return (
    <div className="flex flex-col gap-1">
      <span className="numeric text-[22px] leading-none" style={{ color: colour }}>
        {value}
      </span>
      <span className="text-[10px] uppercase tracking-[0.14em] text-[var(--color-ink-faint)]">
        {label}
      </span>
    </div>
  );
}

/** A standing notice. The text always comes from the API, never from the client. */
export function Notice({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "warning" }) {
  const colour = tone === "warning" ? "var(--color-warning)" : "var(--color-neutral)";
  return (
    <p
      className="border-l-2 py-1 pl-3 text-[12px] leading-relaxed text-[var(--color-ink-muted)]"
      style={{ borderColor: colour }}
    >
      {children}
    </p>
  );
}
