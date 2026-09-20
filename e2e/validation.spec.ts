import { expect, test, type Page } from "@playwright/test";

/**
 * The credibility layer, as a judge reads it.
 *
 * The thing worth checking here is not that numbers appear - it is that the
 * *awkward* ones appear: that the uncross-validated AUC is on screen labelled
 * as not independent, that the terrain-only figure is shown even though it is
 * the lowest, that the sample size and interval travel with the headline, and
 * that the limitation paragraph is not hidden behind a disclosure.
 */

function watchConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(String(error)));
  return errors;
}

function firstNumber(text: string): number {
  const match = /-?\d+(?:\.\d+)?/.exec(text.replace(/,/g, ""));
  return match ? Number(match[0]) : Number.NaN;
}

test.describe("Model validation", () => {
  test("the back-test is on screen with its sample size and interval", async ({
    page,
  }) => {
    const errors = watchConsole(page);
    await page.goto("/model");

    const headline = page.getByTestId("backtest-headline");
    await expect(headline).toBeVisible();
    const text = await headline.innerText();
    expect(text).toMatch(/ROC-AUC/i);
    expect(text).toMatch(/95% CI/i);
    expect(text).toMatch(/recorded incidents/i);

    // All three variants, including the two that read worse than the flattering one.
    for (const id of ["as_deployed", "spatial_cv", "terrain_only"]) {
      await expect(page.getByTestId(`backtest-${id}`)).toBeVisible();
    }
    await expect(
      page
        .getByTestId("backtest-as_deployed")
        .getByText("not independent", { exact: true }),
    ).toBeVisible();
    await expect(
      page
        .getByTestId("backtest-spatial_cv")
        .getByText("independent", { exact: true }),
    ).toBeVisible();

    // The limitation is stated, not tucked away.
    await expect(page.getByText(/cannot prove that one is/i)).toBeVisible();
    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("the flattering figure is shown as higher, and labelled", async ({ page }) => {
    await page.goto("/model");
    const leaky = firstNumber(
      await page
        .getByTestId("backtest-as_deployed")
        .locator("td")
        .nth(1)
        .innerText(),
    );
    const honest = firstNumber(
      await page.getByTestId("backtest-spatial_cv").locator("td").nth(1).innerText(),
    );
    expect(leaky).toBeGreaterThan(honest);
    expect(honest).toBeGreaterThan(0.5);
  });

  test("the sensitivity analysis reports its runs and flags the movers", async ({
    page,
  }) => {
    const errors = watchConsole(page);
    await page.goto("/model");

    const headline = page.getByTestId("sensitivity-headline");
    await expect(headline).toBeVisible();
    const text = await headline.innerText();
    expect(text).toMatch(/runs perturbing/i);
    expect(text).toMatch(/Spearman/i);
    expect(text).toMatch(/top \d+ set is unchanged/i);

    // Every habitation carries a verdict, and at least one of the two labels is
    // present - a table where everything is "rank-stable" and nothing can ever
    // read otherwise would not be telling anyone anything.
    const rows = page.locator("[data-testid^='stability-H-']");
    const total = await rows.count();
    expect(total).toBeGreaterThanOrEqual(10);
    const stable = await rows
      .filter({ has: page.getByText("rank-stable", { exact: true }) })
      .count();
    const sensitive = await rows
      .filter({ has: page.getByText("weight-sensitive", { exact: true }) })
      .count();
    expect(stable + sensitive).toBe(total);
    // A table where nothing can ever read "weight-sensitive" is not telling
    // anyone anything. This corridor has movers, and they are named.
    expect(sensitive).toBeGreaterThan(0);

    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("the confidence surface reports its own narrow range", async ({ page }) => {
    await page.goto("/model");
    await expect(
      page.getByRole("heading", { name: /where the evidence is thin/i }),
    ).toBeVisible();
    await expect(page.getByText(/never multiplied into/i).first()).toBeVisible();
  });

  test("rank stability reaches the habitation priority screen", async ({ page }) => {
    const errors = watchConsole(page);
    await page.goto("/priority");
    const chip = page.getByTestId("rank-stability");
    await expect(chip).toBeVisible();
    await expect(chip).toContainText(/rank \d+–\d+ under ±20% weights/i);
    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("the confidence layer can be turned on over the hazard surface", async ({
    page,
  }) => {
    const errors = watchConsole(page);
    await page.goto("/risk");
    const toggle = page.getByLabel(/evidence confidence/i);
    await expect(toggle).toBeVisible();
    await expect(toggle).not.toBeChecked();
    await toggle.check();
    await expect(toggle).toBeChecked();
    expect(errors, errors.join("\n")).toEqual([]);
  });
});
