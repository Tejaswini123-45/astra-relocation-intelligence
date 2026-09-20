"use client";

import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiRefusedError } from "@/lib/api";

/**
 * Demo Mode (§8.5): an unattended walkthrough of the four capabilities.
 *
 * It is a UI sequence, not a slideshow. Each step navigates to the real screen
 * and asks the real API for what the caption says - a narration built from the
 * computed result, a counterfactual re-solve, a what-if run by clicking the What-If
 * screen's own controls. A caption shows "Asking the API" until the answer is
 * back, and says so plainly if the API refuses. Escape stops it at any point.
 */

export const DEMO_EVENT = "astra:demo-start";

export function startDemo() {
  window.dispatchEvent(new Event(DEMO_EVENT));
}

class Cancelled extends Error {}

type Token = { cancelled: boolean };

type Step = {
  route: string;
  title: string;
  source: string;
  dwellMs: number;
  narrate: (token: Token) => Promise<string>;
};

async function pause(ms: number, token: Token): Promise<void> {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (token.cancelled) throw new Cancelled();
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

async function waitFor(selector: string, token: Token, timeoutMs: number) {
  const until = Date.now() + timeoutMs;
  while (Date.now() < until) {
    if (token.cancelled) throw new Cancelled();
    const found = document.querySelector<HTMLElement>(selector);
    if (found) return found;
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  return null;
}

function buttonLabelled(label: string): HTMLButtonElement | null {
  return (
    Array.from(document.querySelectorAll<HTMLButtonElement>("button")).find(
      (button) => button.textContent?.trim() === label,
    ) ?? null
  );
}

const STEPS: Step[] = [
  {
    route: "/",
    title: "The situation",
    source: "GET /brief/preview",
    dwellMs: 7000,
    narrate: async () => {
      const brief = await api.briefPreview();
      return brief.basis === "LIVE"
        ? `${brief.situation.headline} ${brief.basis_note}`
        : brief.situation.headline;
    },
  },
  {
    route: "/risk",
    title: "C1 · Hazard-based red zones, decomposed",
    source: "GET /risk/summary · Engine 1",
    dwellMs: 7000,
    narrate: async () => {
      const summary = await api.riskSummary();
      const critical = summary.zone_summary.CRITICAL;
      if (!critical) return "The hazard engine classified no ground as Critical in this assessment.";
      return (
        `${critical.count} Critical zones covering ${critical.area_km2.toLocaleString("en-IN")} km2, ` +
        `intersecting ${critical.population_intersected.toLocaleString("en-IN")} residents. ` +
        "Every zone and cell decomposes into its dominant hazard and the weighted factors behind it."
      );
    },
  },
  {
    route: "/priority",
    title: "C3 · Who moves first, and why",
    source: "GET /narrate/habitation · Engine 2 and 3",
    dwellMs: 10000,
    narrate: async () => {
      const ranking = await api.priorityHabitations();
      const top = ranking.habitations[0];
      if (!top) return "No habitations were assessed.";
      return (await api.narrateHabitation(top.habitation_id)).text;
    },
  },
  {
    route: "/sites",
    title: "C2 · What a safer site can actually absorb",
    source: "GET /narrate/site · Engine 4",
    dwellMs: 10000,
    narrate: async () => {
      const capacity = await api.capacitySites();
      const site =
        capacity.sites.find((entry) => entry.suitable && entry.marginal_headline) ??
        capacity.sites.find((entry) => entry.suitable);
      if (!site) return "No candidate site passes every suitability gate.";
      return (await api.narrateSite(site.site_id)).text;
    },
  },
  {
    route: "/plan",
    title: "C4 · The optimised plan, and a real counterfactual",
    source: "GET /plan · GET /plan/why-not (CP-SAT re-solve)",
    dwellMs: 11000,
    narrate: async () => {
      const [plan, capacity] = await Promise.all([api.plan(), api.capacitySites()]);
      const moved = plan.assignments[0];
      if (!moved) return plan.headline;
      const used = new Set(
        plan.assignments
          .filter((entry) => entry.habitation_id === moved.habitation_id)
          .map((entry) => entry.site_id),
      );
      const alternative = capacity.sites.find((site) => site.suitable && !used.has(site.site_id));
      if (!alternative) return plan.headline;
      try {
        const counterfactual = await api.whyNot(moved.habitation_id, alternative.site_id);
        return (
          `${plan.headline} Why not ${alternative.site_id} for ${moved.habitation_id}? ` +
          `Re-solved with that assignment forced: ${counterfactual.headline}`
        );
      } catch {
        return `${plan.headline} The counterfactual for ${moved.habitation_id} → ${alternative.site_id} could not be run.`;
      }
    },
  },
  {
    route: "/simulate",
    title: "Conditions change: heavier monsoon, and the bridge the plan leans on",
    source: "What-If controls → POST /simulate",
    dwellMs: 9000,
    narrate: async (token) => {
      const runButton = await waitFor('[data-testid="run-scenario"]', token, 20_000);
      if (!runButton) return "The What-If screen did not load, so no scenario was run.";
      if (document.querySelector('[data-testid="scenario-diff"]')) buttonLabelled("Reset")?.click();
      await pause(300, token);
      buttonLabelled("Monsoon escalation")?.click();
      await pause(250, token);
      buttonLabelled("Bridge down")?.click();
      await pause(400, token);
      const ready = document.querySelector<HTMLButtonElement>('[data-testid="run-scenario"]');
      if (!ready || ready.disabled) return "The scenario controls did not accept the change.";
      ready.click();
      const diff = await waitFor('[data-testid="scenario-diff"]', token, 120_000);
      if (!diff) {
        const refused = document.querySelector('[data-testid="scenario-error"]');
        return refused?.textContent
          ? `The API refused the scenario: ${refused.textContent}`
          : "The scenario did not finish in time.";
      }
      diff.scrollIntoView({ behavior: "smooth", block: "start" });
      return (
        "The monsoon preset raised rainfall intensity and the bridge the plan depends on most was closed. " +
        "The whole chain re-ran - hazard, tiers, capacity, routes, optimiser - and the before → after diff on screen is that result."
      );
    },
  },
  {
    route: "/model",
    title: "Is the model any good?",
    source: "GET /validation",
    dwellMs: 10000,
    narrate: async () => {
      const validation = await api.validation();
      return `${validation.backtest.headline} ${validation.sensitivity.headline}`;
    },
  },
  {
    route: "/evidence",
    title: "People decide; the ledger records it",
    source: "GET /decisions",
    dwellMs: 7000,
    narrate: async () => {
      const ledger = await api.decisions();
      const latest = ledger.decisions[0];
      return (
        "Every run, simulation and brief writes a ledger row with its input hash" +
        (latest ? ` - the latest is ${latest.id}, ${latest.state.replace(/_/g, " ").toLowerCase()}. ` : ". ") +
        "An override needs a reason, and what it costs is re-solved, not asserted."
      );
    },
  },
  {
    route: "/",
    title: "Walkthrough complete",
    source: "Manual control",
    dwellMs: 5000,
    narrate: async () =>
      "You have manual control. Generate the Decision Brief to print this plan with its audit ID, or run the monsoon escalation to watch the live pipeline re-score.",
  },
];

export function DemoMode() {
  const router = useRouter();
  const pathname = usePathname();
  const pathRef = useRef(pathname);
  pathRef.current = pathname;
  const tokenRef = useRef<Token | null>(null);
  const [index, setIndex] = useState(0);
  const [caption, setCaption] = useState<{ title: string; body: string | null; source: string } | null>(
    null,
  );

  const stop = useCallback(() => {
    if (tokenRef.current) tokenRef.current.cancelled = true;
    tokenRef.current = null;
    setCaption(null);
  }, []);

  const run = useCallback(async () => {
    if (tokenRef.current) return;
    const token: Token = { cancelled: false };
    tokenRef.current = token;
    try {
      for (let step = 0; step < STEPS.length; step += 1) {
        const current = STEPS[step];
        setIndex(step);
        setCaption({ title: current.title, body: null, source: current.source });
        if (pathRef.current !== current.route) router.push(current.route);
        let body: string;
        try {
          body = await current.narrate(token);
        } catch (thrown) {
          if (thrown instanceof Cancelled) throw thrown;
          body =
            thrown instanceof ApiRefusedError
              ? `The API refused this step: ${thrown.detail}`
              : "The API did not answer this step. Nothing was substituted for it.";
        }
        if (token.cancelled) throw new Cancelled();
        setCaption({ title: current.title, body, source: current.source });
        await pause(current.dwellMs, token);
      }
    } catch (thrown) {
      if (!(thrown instanceof Cancelled)) throw thrown;
    } finally {
      if (tokenRef.current === token) {
        tokenRef.current = null;
        setCaption(null);
      }
    }
  }, [router]);

  useEffect(() => {
    const start = () => void run();
    window.addEventListener(DEMO_EVENT, start);
    return () => window.removeEventListener(DEMO_EVENT, start);
  }, [run]);

  useEffect(() => {
    if (!caption) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") stop();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [caption, stop]);

  if (!caption) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="demo-caption"
      className="fixed inset-x-0 bottom-4 z-40 mx-auto w-[min(780px,calc(100vw-32px))] rounded border bg-[var(--color-surface)] px-4 py-3 shadow-[0_10px_40px_rgba(0,0,0,0.55)] print:hidden"
      style={{ borderColor: "var(--color-signal)" }}
    >
      <div className="flex items-baseline justify-between gap-3">
        <p className="numeric text-[10px] uppercase tracking-[0.16em] text-[var(--color-ink-faint)]">
          Demo mode &middot; {index + 1} of {STEPS.length}
        </p>
        <button
          type="button"
          onClick={stop}
          className="text-[11px] text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
        >
          Exit (Esc)
        </button>
      </div>
      <h2 className="mt-1 text-[14px] font-semibold text-[var(--color-ink)]">{caption.title}</h2>
      <p className="mt-1 text-[12px] leading-relaxed text-[var(--color-ink)]" data-testid="demo-caption-body">
        {caption.body ?? "Asking the API…"}
      </p>
      <p className="numeric mt-1.5 text-[10px] text-[var(--color-ink-faint)]">Driven by {caption.source}</p>
      <div className="mt-2 h-0.5 bg-[var(--color-line)]" aria-hidden>
        <div
          className="h-full bg-[var(--color-signal)] transition-[width] duration-500"
          style={{ width: `${((index + 1) / STEPS.length) * 100}%` }}
        />
      </div>
    </div>
  );
}
