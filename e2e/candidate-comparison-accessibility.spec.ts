import { expect, test } from "@playwright/test";
import { expectNoBlockingAxeViolations } from "./axe";
import { apiBaseUrl } from "./helpers";

test("candidate input pane snaps closed and restores its previous width", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1180, height: 820 });
  await page.goto("/?view=candidates&project=default");
  await page.evaluate(() => {
    localStorage.setItem("material-workbench:layout:inspector-width:v1", "330");
    localStorage.removeItem("material-workbench:layout:inspector-collapsed:v1");
  });
  await page.reload();
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();

  const inspector = page.getByRole("complementary", { name: "選択候補の入力", exact: true });
  const workspace = page.locator(".central-workspace");
  const resizer = page.getByRole("separator", { name: "選択候補の入力パネル幅を調整" });
  await expect(inspector).toBeVisible();
  await expect(resizer).toHaveAttribute("aria-valuenow", "330");
  const expandedWorkspaceWidth = await workspace.evaluate((element) => element.clientWidth);

  const box = await resizer.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + 80);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width / 2 - 100, box!.y + 80, { steps: 8 });
  await page.mouse.up();

  const collapsed = page.getByRole("complementary", { name: "折りたたまれた選択候補の入力" });
  await expect(collapsed).toBeVisible();
  await expect(inspector).toHaveCount(0);
  await expect(resizer).toHaveCount(0);
  await expect.poll(() => workspace.evaluate((element) => element.clientWidth))
    .toBeGreaterThan(expandedWorkspaceWidth + 250);
  await expect.poll(() => page.evaluate(() => (
    localStorage.getItem("material-workbench:layout:inspector-collapsed:v1")
  ))).toBe("true");

  await page.reload();
  await expect(page.getByRole("complementary", { name: "折りたたまれた選択候補の入力" })).toBeVisible();
  await page.locator(".candidate-inspector").evaluate((element) => {
    element.dataset.preservationProbe = "kept";
  });
  await page.getByRole("button", { name: "選択候補の入力を開く" }).click();
  await expect(page.getByRole("complementary", { name: "選択候補の入力", exact: true })).toBeVisible();
  await expect(resizer).toHaveAttribute("aria-valuenow", "330");

  const collapseButton = page.getByRole("button", { name: "入力パネルを折りたたむ" });
  await collapseButton.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("complementary", { name: "折りたたまれた選択候補の入力" })).toBeVisible();
  await expect(page.getByRole("button", { name: "選択候補の入力を開く" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("complementary", { name: "選択候補の入力", exact: true })).toBeVisible();
  await expect(collapseButton).toBeFocused();
  await expect(resizer).toHaveAttribute("aria-valuenow", "330");
  await expect(inspector).toHaveAttribute("data-preservation-probe", "kept");
  await expect.poll(() => page.evaluate(() => (
    localStorage.getItem("material-workbench:layout:inspector-collapsed:v1")
  ))).toBe("false");
  await page.reload();
  await expect(page.getByRole("complementary", { name: "選択候補の入力", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "入力パネルを折りたたむ" }).click();
  await expect(page.getByRole("complementary", { name: "折りたたまれた選択候補の入力" })).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("candidate-inspector-collapsed.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 800, height: 820 });
  await expect.poll(() => collapsed.evaluate((element) => element.clientHeight)).toBeLessThan(50);
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await expectNoBlockingAxeViolations(page, "collapsed candidate input pane");
});

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

test("comparison table height is adjustable for any candidate count and persists", async ({ page }) => {
  await page.goto("/?view=candidates&project=default");
  await page.evaluate(() => {
    localStorage.setItem("material-workbench:layout:comparison-height:v1", "220");
  });
  await page.reload();
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();

  const createdCandidates: Array<{ id: string; revision: number }> = [];
  for (let index = 0; index < 4; index += 1) {
    const createdResponse = page.waitForResponse((response) => (
      response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith("/api/projects/default/candidates")
    ));
    await page.getByRole("button", { name: "候補を追加", exact: true }).click();
    const response = await createdResponse;
    expect(response.status()).toBe(201);
    createdCandidates.push(await response.json() as { id: string; revision: number });
  }

  await expect(page.locator(".candidate-name-table tbody tr")).toHaveCount(7);
  await expect(page.getByRole("button", { name: /全7候補を表示/ })).toHaveCount(0);

  const resizer = page.getByRole("separator", { name: "候補比較表の高さを調整" });
  const pane = page.locator(".comparison-prediction-scroll");
  await expect(resizer).toHaveAttribute("aria-orientation", "horizontal");
  await expect(resizer).toHaveAttribute("aria-valuenow", "220");
  await expect.poll(() => pane.evaluate((element) => element.clientHeight)).toBe(220);

  const box = await resizer.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2 + 80);
  await page.mouse.up();
  await expect(resizer).toHaveAttribute("aria-valuenow", "300");
  await expect.poll(() => pane.evaluate((element) => element.clientHeight)).toBe(300);

  await resizer.focus();
  await page.keyboard.press("ArrowDown");
  await expect(resizer).toHaveAttribute("aria-valuenow", "320");
  await expect.poll(() => page.evaluate(() => (
    localStorage.getItem("material-workbench:layout:comparison-height:v1")
  ))).toBe("320");

  await page.reload();
  await expect(page.getByRole("separator", { name: "候補比較表の高さを調整" })).toHaveAttribute("aria-valuenow", "320");

  for (const candidate of createdCandidates) {
    expect((await page.request.delete(`${apiBaseUrl}/api/projects/default/candidates/${candidate.id}?expected_revision=${candidate.revision}`)).status()).toBe(204);
  }
});

test("comparison panes keep candidate rows aligned after text enlargement", async ({ page }) => {
  await page.goto("/?view=candidates&project=default");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await expect(page.locator(".comparison-prediction-table tbody tr")).toHaveCount(3);
  await expect(page.locator(".decision-prediction").first()).toBeVisible();
  await expect(page.locator(".candidate-name-table thead tr")).toHaveCount(1);

  const selectedLabel = await page.locator(
    ".candidate-name-table tbody tr.selected-row",
  ).getByRole("textbox").inputValue();
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
  await expect(
    readingView.getByText("90%予測区間: 533.2–538.8 MPa", { exact: true }),
  ).toBeVisible();
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
