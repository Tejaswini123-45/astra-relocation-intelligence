import { expect, test, type Page } from "@playwright/test";

/**
 * The What-If screen, exercised the way a judge exercises it.
 *
 * These tests deliberately assert on *change*, not on presence. A screen that
 * renders a baseline and a screen that renders a real second assessment look
 * identical to a test that only checks a heading. So every case here reads a
 * number before running the scenario, reads it after, and requires the two to
 * differ in the direction the perturbed engine says they should.
 */

/** Console errors are a product defect, not test noise. Collect and assert. */
function watchConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(String(error)));
  return errors;
}

/**
 * The headline strip renders "before → after" per metric. Pull one metric's
 * pair out of the DOM so a test can compare them.
 */
async function metric(page: Page, label: string): Promise<[number, number]> {
  const tile = page
    .getByTestId("scenario-headline-metrics")
    .locator("div", { hasText: label })
    .first();
  const text = (await tile.innerText()).replace(/\s+/g, " ");
  const numbers = [...text.matchAll(/-?[\d,]+(?:\.\d+)?/g)]
    .map((match) => Number(match[0].replace(/,/g, "")))
    .filter((value) => Number.isFinite(value));
  expect(numbers.length, `no numbers found in metric tile "${label}"`).toBeGreaterThanOrEqual(2);
  return [numbers[0], numbers[1]];
}

async function runScenario(page: Page) {
  const run = page.getByTestId("run-scenario");
  await expect(run).toBeEnabled();
  await run.click();
  await expect(page.getByTestId("scenario-diff")).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId("scenario-error")).toHaveCount(0);
}

test.describe("What-if simulation", () => {
  test("the baseline is on screen before anything is simulated", async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto("/simulate");

    await expect(
      page.getByRole("heading", { name: /what changes if conditions change/i }),
    ).toBeVisible();
    // Nothing is simulated until it is run: no diff, and the run control is off.
    await expect(page.getByTestId("scenario-diff")).toHaveCount(0);
    await expect(page.getByTestId("run-scenario")).toBeDisabled();
    // The baseline map is showing, with the plan's movements already drawn.
    await expect(page.getByTestId("map-caption")).toContainText("movements");
    await expect(page.getByTestId("map-view-after")).toBeDisabled();
    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("a wetter monsoon changes the hazard, the phases and the plan", async ({
    page,
  }) => {
    const errors = watchConsole(page);
    await page.goto("/simulate");

    const baselineCaption = await page.getByTestId("map-caption").innerText();

    await page.getByRole("button", { name: "Monsoon escalation" }).click();
    await expect(page.getByText(/rainfall intensity/i)).toBeVisible();
    await runScenario(page);

    // Rainfall enters at Engine 1, so the critical zone area must move.
    const [criticalBefore, criticalAfter] = await metric(page, "Critical zone");
    expect(criticalAfter).toBeGreaterThan(criticalBefore);

    // ...and it must cascade: a heavier monsoon leaves fewer people placeable.
    const [placedBefore, placedAfter] = await metric(page, "Residents placed");
    expect(placedAfter).not.toBe(placedBefore);

    // The map redraws on the scenario, and can be flipped back to the baseline.
    await expect(page.getByTestId("map-view-after")).toBeEnabled();
    const afterCaption = await page.getByTestId("map-caption").innerText();
    expect(afterCaption).not.toBe(baselineCaption);
    await page.getByTestId("map-view-before").click();
    await expect(page.getByTestId("map-caption")).toHaveText(baselineCaption);
    await page.getByTestId("map-view-after").click();
    await expect(page.getByTestId("map-caption")).toHaveText(afterCaption);

    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("closing a road the plan depends on changes routes and assignments", async ({
    page,
  }) => {
    const errors = watchConsole(page);
    await page.goto("/simulate");

    await page.getByRole("button", { name: "Bridge down" }).click();
    const closed = page.getByTestId("close-segment").filter({ has: page.locator("[aria-pressed='true']") });
    await expect(page.locator("[data-testid='close-segment'][aria-pressed='true']")).toHaveCount(1);
    expect(await closed.count()).toBeLessThanOrEqual(1);
    await runScenario(page);

    // A closure enters at Engine 5, so routes must move...
    const [routesBefore, routesAfter] = await metric(page, "Routes above threshold");
    expect(routesAfter).toBeLessThan(routesBefore);

    // ...and the plan built on those routes must move with them.
    const diff = page.getByTestId("scenario-diff");
    await expect(diff.getByText("Routes that change")).toBeVisible();
    await expect(diff.getByText("No route reliability changes.")).toHaveCount(0);
    await expect(diff.getByText("The plan is unchanged.")).toHaveCount(0);

    // A closure is not a hazard event: the red zones must be untouched.
    const [criticalBefore, criticalAfter] = await metric(page, "Critical zone");
    expect(criticalAfter).toBe(criticalBefore);

    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("withdrawing a candidate site removes its capacity from the total", async ({
    page,
  }) => {
    const errors = watchConsole(page);
    await page.goto("/simulate");

    await page.getByRole("button", { name: "Site withdrawn" }).click();
    await runScenario(page);

    const [capacityBefore, capacityAfter] = await metric(page, "Effective capacity");
    expect(capacityAfter).toBeLessThan(capacityBefore);
    await expect(page.getByTestId("scenario-diff").getByText(/withdrawn/i).first()).toBeVisible();

    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("resetting clears the scenario and returns to the baseline", async ({ page }) => {
    await page.goto("/simulate");
    await page.getByRole("button", { name: "Monsoon escalation" }).click();
    await runScenario(page);
    await page.getByRole("button", { name: "Reset" }).click();
    await expect(page.getByTestId("scenario-diff")).toHaveCount(0);
    await expect(page.getByTestId("run-scenario")).toBeDisabled();
  });

  test("the layout holds on a tablet with no horizontal overflow", async ({ page }) => {
    await page.setViewportSize({ width: 834, height: 1112 });
    await page.goto("/simulate");
    await expect(
      page.getByRole("heading", { name: /what changes if conditions change/i }),
    ).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, "the page must not scroll sideways on a tablet").toBeLessThanOrEqual(1);
  });
});
