"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiRefusedError, generateBrief } from "@/lib/api";

/**
 * Generating a brief is a deliberate act: it writes a decision-ledger row and
 * freezes the brief. So it is a button, never something a page does on load.
 */
export function GenerateBriefButton({ label = "Generate Decision Brief" }: { label?: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const generate = async () => {
    setBusy(true);
    setError(null);
    try {
      const brief = await generateBrief();
      router.push(`/brief/${encodeURIComponent(brief.id ?? "")}`);
    } catch (thrown) {
      setError(
        thrown instanceof ApiRefusedError
          ? thrown.detail
          : "The API did not answer. No brief was generated.",
      );
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-3">
      <button
        type="button"
        onClick={generate}
        disabled={busy}
        data-testid="generate-brief"
        className="rounded-sm border px-3 py-1.5 text-[12px] disabled:opacity-40"
        style={{ borderColor: "var(--color-signal)", color: "var(--color-ink)" }}
      >
        {busy ? "Generating brief…" : label}
      </button>
      {error ? (
        <span role="alert" className="text-[11px] text-[var(--color-critical)]">
          {error}
        </span>
      ) : null}
    </div>
  );
}

export function PrintButton() {
  return (
    <button
      type="button"
      onClick={() => window.print()}
      className="rounded-sm border px-3 py-1.5 text-[12px] print:hidden"
      style={{ borderColor: "var(--color-line-strong)", color: "var(--color-ink)" }}
    >
      Print brief
    </button>
  );
}
