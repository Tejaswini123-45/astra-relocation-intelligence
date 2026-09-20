import { expect, test, type Page } from "@playwright/test";

/**
 * The exact judge journey from CLAUDE.md section 11:
 * load → run scenario → inspect top habitation → inspect site bottleneck →
 * run what-if with bridge closure → assert assignments actually changed →
 * open decision brief.
 *
 * Nothing is stubbed. Expected values are read from the real API first and then
 * required on screen, so the test proves the screen renders the computation
 * rather than something that merely looks like it.
 */

const API = process.env.ASTRA_API_URL ?? "http://127.0.0.1:8000";

function watchConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(String(error)));
  return errors;
}

test.describe("Judge journey", () => {
  test("situation, live escalation, priority, capacity, plan, what-if and brief", async ({
    page,
    request,
  }) => {
    test.setTimeout(480_000);
    const errors = watchConsole(page);
    await request.post(`${API}/live/reset`);
    const brief = await (await request.get(`${API}/brief/preview`)).json();

    // Load: the Command Centre states the situation the API computed.
    await page.goto("/?intro=0");
    await expect(page.getByTestId("command-centre")).toBeVisible();
    await expect(page.getByTestId("situation-headline")).toHaveText(brief.situation.headline);
    await expect(page.getByTestId("comparison-panel")).toContainText("Static hazard map");
    await expect(page.getByTestId("how-this-works")).toContainText("never to produce them");

    // Run the scenario: the escalation posts real observations and the picture moves.
    await page.getByTestId("run-monsoon-escalation").click();
    await expect(page.getByTestId("basis-chip")).toContainText("Live", { timeout: 180_000 });
    await expect(page.getByTestId("run-monsoon-escalation")).toHaveText(/escalation ingested/i, {
      timeout: 420_000,
    });
    const live = await (await request.get(`${API}/live`)).json();
    expect(live.events_ingested).toBeGreaterThan(0);
    await page.getByTestId("reset-live").click();
    await expect(page.getByTestId("basis-chip")).toContainText("Baseline", { timeout: 60_000 });

    // Inspect the top habitation.
    const top = brief.priorities[0];
    await page.goto("/priority");
    await page.getByRole("button", { name: new RegExp(top.name) }).first().click();
    await expect(page.getByRole("heading", { level: 2, name: top.name })).toBeVisible();
    await expect(page.getByText(top.priority_score.toFixed(1)).first()).toBeVisible();

    // Inspect a site's binding constraint.
    const site = brief.capacity.sites.find(
      (entry: { suitable: boolean; bottleneck: string | null }) => entry.suitable && entry.bottleneck,
    );
    expect(site, "at least one suitable site has a named bottleneck").toBeTruthy();
    await page.goto("/sites");
    await page.getByRole("button", { name: new RegExp(site.name) }).first().click();
    await expect(page.getByRole("heading", { level: 2, name: site.name })).toBeVisible();
    await expect(page.getByText(/bottleneck:/i).first()).toBeVisible();
    if (site.marginal_headline) {
      await expect(page.getByText(site.marginal_headline).first()).toBeVisible();
    }

    // The plan, and a real counterfactual re-solve.
    await page.goto("/plan");
    await page.getByTestId("assignment-row").first().click();
    await page.getByTestId("why-not-option").first().click();
    await expect(page.getByTestId("why-not-result")).toBeVisible({ timeout: 90_000 });

    // What-if with the bridge the plan depends on closed: assignments must change.
    await page.goto("/simulate");
    await page.getByRole("button", { name: "Bridge down" }).click();
    await page.getByTestId("run-scenario").click();
    const diff = page.getByTestId("scenario-diff");
    await expect(diff).toBeVisible({ timeout: 120_000 });
    await expect(diff.getByText("The plan is unchanged.")).toHaveCount(0);
    await expect(diff.getByText("No route reliability changes.")).toHaveCount(0);

    // The Decision Brief, frozen with its audit record.
    await page.goto("/?intro=0");
    await page.getByTestId("generate-brief").click();
    await expect(page).toHaveURL(/\/brief\/BRF-/, { timeout: 60_000 });
    await expect(page.getByTestId("decision-brief")).toBeVisible();
    await expect(page.getByTestId("brief-audit-id")).toHaveText(/^DEC-/);
    await expect(page.getByTestId("brief-authority")).toContainText("SDMA");
    await expect(page.getByTestId("brief-actions")).toContainText("Immediate");

    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("the cold open plays, then the study area resolves layer by layer", async ({ page }) => {
    await page.goto("/?intro=1");
    await expect(page.getByTestId("cold-open")).toBeVisible();
    await page.getByRole("button", { name: /enter the command centre/i }).click();
    await expect(page.getByTestId("cold-open")).toHaveCount(0);
    await expect(page.getByTestId("layer-resolve")).toBeVisible();
    await expect(page.getByTestId("layer-resolve")).toHaveCount(0, { timeout: 20_000 });
  });

  test("demo mode is driven by the API and stops on Escape", async ({ page, request }) => {
    await request.post(`${API}/live/reset`);
    const brief = await (await request.get(`${API}/brief/preview`)).json();
    await page.goto("/?intro=0");
    await page.getByTestId("start-demo").click();
    const body = page.getByTestId("demo-caption-body");
    await expect(body).toContainText(brief.situation.headline.slice(0, 40), { timeout: 30_000 });
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("demo-caption")).toHaveCount(0);
  });
});
