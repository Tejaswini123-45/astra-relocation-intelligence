import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import Link from "next/link";
import { cache } from "react";

import { DemoMode } from "@/components/demo-mode";
import { NavLinks } from "@/components/nav-links";
import { Wordmark } from "@/components/wordmark";
import { api, tryFetch } from "@/lib/api";

import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-mono-face",
});

export const metadata: Metadata = {
  title: "ASTRA - Proactive Settlement Risk & Relocation Intelligence",
  description:
    "GIS decision support for multi-hazard red zones, carrying capacity assessment and phased relocation prioritisation. Decision-support output; final decisions rest with the SDMA.",
};

/** One health read per request, shared by the status strip and the notice. */
const getHealth = cache(() => tryFetch(api.health));

async function HowThisWorks() {
  const health = await getHealth();
  if (!health) return null;
  return (
    <div className="mt-5 hidden border-t border-[var(--color-line)] px-3 pt-3 lg:block">
      <p className="text-[9px] uppercase tracking-[0.18em] text-[var(--color-ink-faint)]">
        How this works
      </p>
      <p className="mt-1 text-[10px] leading-relaxed text-[var(--color-ink-muted)]">
        {health.how_this_works}
      </p>
      <p className="mt-2 text-[10px] leading-relaxed text-[var(--color-ink-faint)]">
        {health.decision_authority}
      </p>
    </div>
  );
}

async function StatusStrip() {
  const health = await getHealth();
  if (!health) {
    return (
      <span className="numeric text-[11px] text-[var(--color-critical)]">
        API UNREACHABLE
      </span>
    );
  }
  const ok = health.status === "ok" && health.fixtures_valid;
  return (
    <div className="flex items-center gap-5 text-[11px]">
      <span className="flex items-center gap-2">
        <span
          aria-hidden
          className="h-1.5 w-1.5 rounded-full"
          style={{
            backgroundColor: ok ? "var(--color-safe)" : "var(--color-warning)",
          }}
        />
        <span className="uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
          {ok ? "System nominal" : "Degraded"}
        </span>
      </span>
      <span className="text-[var(--color-ink-faint)]">
        Engine <span className="numeric text-[var(--color-ink-muted)]">{health.engine_version}</span>
        <span className="mx-2 text-[var(--color-line-strong)]">|</span>
        Config{" "}
        <span className="numeric text-[var(--color-ink-muted)]">
          {health.model_config_version}
        </span>
        <span className="mx-2 text-[var(--color-line-strong)]">|</span>
        AI explanation{" "}
        <span className="uppercase text-[var(--color-ink-muted)]">
          {health.llm_mode === "connected" ? "Connected" : "Template mode"}
        </span>
      </span>
    </div>
  );
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body className="min-h-screen bg-[var(--color-abyss)] print:bg-white">
        <header className="sticky top-0 z-20 border-b border-[var(--color-line)] bg-[var(--color-surface)]/95 backdrop-blur print:hidden">
          <div className="flex flex-wrap items-center justify-between gap-4 px-5 py-3">
            <Link href="/" className="rounded-sm">
              <Wordmark />
            </Link>
            <StatusStrip />
          </div>
        </header>
        <div className="flex min-h-[calc(100vh-64px)] flex-col lg:flex-row">
          <nav
            aria-label="Operational navigation"
            className="border-b border-[var(--color-line)] bg-[var(--color-surface-inset)] px-3 py-3 lg:w-56 lg:shrink-0 lg:border-b-0 lg:border-r print:hidden"
          >
            <NavLinks />
            <HowThisWorks />
          </nav>
          <main className="min-w-0 flex-1">{children}</main>
        </div>
        <DemoMode />
      </body>
    </html>
  );
}
