import { expect, test } from "@playwright/test";
import { expectNoBlockingAxeViolations } from "./axe";

test("a narrow candidate input pane reflows fields without horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.addInitScript(() => {
    localStorage.setItem("material-workbench:layout:inspector-width:v1", "260");
  });
  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();

  const layout = await page.locator(".candidate-inspector").evaluate((inspector) => {
    const composition = inspector.querySelector<HTMLElement>(".composition-fields");
    return {
      clientWidth: inspector.clientWidth,
      scrollWidth: inspector.scrollWidth,
      columns: composition ? getComputedStyle(composition).gridTemplateColumns : "",
      repeatedUnitCount: composition?.querySelectorAll(".slider-field em small").length ?? -1,
    };
  });
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth);
  expect(layout.columns.split(" ")).toHaveLength(1);
  expect(layout.repeatedUnitCount).toBe(0);
});

test("comparison panes keep candidate rows aligned after text enlargement", async ({ page }) => {
  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await expect(page.locator(".comparison-prediction-table tbody tr")).toHaveCount(3);
  await expect(page.locator(".decision-prediction").first()).toBeVisible();
  await expect(page.locator(".candidate-name-table thead tr")).toHaveCount(1);

  const selectedLabel = await page.locator(
    ".candidate-name-table tbody tr.selected-row input",
  ).inputValue();
  await page.getByText("選択候補を1件ずつ読む", { exact: true }).click();
  const readingView = page.getByRole("region", { name: selectedLabel });
  await expect(readingView).toBeVisible();
  await expect(readingView.getByRole("heading", { name: "入力条件" })).toBeVisible();
  await expect(readingView.getByRole("heading", {
    name: "予測・支持範囲・目標達成",
  })).toBeVisible();
  await expect(readingView.getByText("予測", { exact: true }).first()).toBeVisible();
  await expect(readingView.getByText("支持範囲", { exact: true }).first()).toBeVisible();
  await expect(readingView.getByText("目標達成", { exact: true }).first()).toBeVisible();
  await expect(readingView.getByRole("heading", { name: "ヒートパターン" })).toBeVisible();
  await expect(readingView.getByText("536.0 MPa", { exact: true })).toBeVisible();
  await expect(readingView.getByText("533.2–538.8 MPa", { exact: true })).toBeVisible();
  await expect(readingView.getByText(/分、\d+(?:\.\d+)? °C/).first()).toBeVisible();
  await expectNoBlockingAxeViolations(page, "expanded selected Candidate reading view");

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
    readingFontSize: Number.parseFloat(
      getComputedStyle(document.querySelector(
        ".selected-candidate-reading-content dd",
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
      const tops = rowSets.map((rows) => {
        const row = rows.find((item) => item.dataset.candidateId === candidateId);
        return row?.getBoundingClientRect().top ?? 0;
      });
      return heights.every((height) => height > 0)
        && Math.max(...heights) - Math.min(...heights) <= 0.5
        && Math.max(...tops) - Math.min(...tops) <= 0.5;
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
  await expect.poll(async () => readingView.locator("dd").first().evaluate(
    (node) => Number.parseFloat(getComputedStyle(node).fontSize),
  )).toBeGreaterThanOrEqual(baseline.readingFontSize * 1.49);

  await expect.poll(rowsAreAligned).toBe(true);
  await expect.poll(async () => page.evaluate(() => {
    const panes = [
      ".comparison-input-scroll",
      ".comparison-prediction-scroll",
      ".comparison-action-scroll",
    ].map((selector) => document.querySelector<HTMLElement>(selector)!);
    panes.forEach((pane) => {
      pane.scrollTop = 80;
    });
    return panes.every((pane) => {
      const headerRows = pane.querySelectorAll<HTMLTableRowElement>("thead > tr");
      const first = headerRows[0]?.querySelector("th")?.getBoundingClientRect();
      const second = headerRows[1]?.querySelector("th")?.getBoundingClientRect();
      return Boolean(first && second && second.top >= first.bottom - 0.5);
    });
  })).toBe(true);
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
