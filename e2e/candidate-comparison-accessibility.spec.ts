import { expect, test } from "@playwright/test";

test("comparison panes keep candidate rows aligned after text enlargement", async ({ page }) => {
  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await expect(page.locator(".comparison-prediction-table tbody tr")).toHaveCount(3);
  await expect(page.locator(".decision-prediction").first()).toBeVisible();

  for (const selector of [
    ".comparison-input-table",
    ".comparison-prediction-table",
    ".comparison-action-table",
  ]) {
    const rows = page.locator(`${selector} tbody tr`);
    await expect(rows).toHaveCount(3);
    await expect(rows.locator('th[scope="row"]')).toHaveCount(3);
  }

  const baseline = await page.evaluate(() => ({
    rootFontSize: Number.parseFloat(getComputedStyle(document.documentElement).fontSize),
    predictionFontSize: Number.parseFloat(
      getComputedStyle(document.querySelector(".decision-prediction")!).fontSize,
    ),
    supportFontSize: Number.parseFloat(
      getComputedStyle(document.querySelector(
        ".comparison-prediction-table .target-support-list > span",
      )!).fontSize,
    ),
    rowHeights: Object.fromEntries(
      Array.from(document.querySelectorAll<HTMLTableRowElement>(
        ".candidate-name-table tbody tr",
      )).map((row) => [
        row.dataset.candidateId!,
        row.getBoundingClientRect().height,
      ]),
    ),
  }));
  const rowsAreAligned = () => page.evaluate(() => {
    const selectors = [
      ".candidate-name-table",
      ".comparison-input-table",
      ".comparison-prediction-table",
      ".comparison-action-table",
    ];
    const rowSets = selectors.map((selector) =>
      Array.from(document.querySelectorAll<HTMLTableRowElement>(
        `${selector} tbody tr`,
      )),
    );
    return rowSets[0].every((_, rowIndex) => {
      const candidateId = rowSets[0][rowIndex].dataset.candidateId;
      const heights = rowSets.map((rows) => {
        const row = rows.find((item) => item.dataset.candidateId === candidateId);
        return row?.getBoundingClientRect().height ?? 0;
      });
      return heights.every((height) => height > 0)
        && Math.max(...heights) - Math.min(...heights) <= 0.5;
    });
  });
  await page.evaluate((fontSize) => {
    document.documentElement.style.fontSize = `${fontSize * 1.5}px`;
  }, baseline.rootFontSize);

  await expect.poll(async () => page.locator(".decision-prediction").first().evaluate(
    (node) => Number.parseFloat(getComputedStyle(node).fontSize),
  )).toBeGreaterThanOrEqual(baseline.predictionFontSize * 1.49);
  await expect.poll(async () => page.locator(
    ".comparison-prediction-table .target-support-list > span",
  ).first().evaluate(
    (node) => Number.parseFloat(getComputedStyle(node).fontSize),
  )).toBeGreaterThanOrEqual(baseline.supportFontSize * 1.49);

  await expect.poll(rowsAreAligned).toBe(true);
  await expect.poll(async () => page.evaluate((baselineRows) => (
    Array.from(document.querySelectorAll<HTMLTableRowElement>(
      ".candidate-name-table tbody tr",
    )).some((row) => (
      row.getBoundingClientRect().height
      > (baselineRows[row.dataset.candidateId!] ?? 0) + 1
    ))
  ), baseline.rowHeights)).toBe(true);

  await page.evaluate(() => {
    document.documentElement.style.fontSize = "";
  });
  await expect.poll(async () => page.evaluate((baselineRows) => (
    Array.from(document.querySelectorAll<HTMLTableRowElement>(
      ".candidate-name-table tbody tr",
    )).every((row) => (
      Math.abs(
        row.getBoundingClientRect().height
        - (baselineRows[row.dataset.candidateId!] ?? 0)
      ) <= 1
    ))
  ), baseline.rowHeights)).toBe(true);
  await expect.poll(rowsAreAligned).toBe(true);
});
