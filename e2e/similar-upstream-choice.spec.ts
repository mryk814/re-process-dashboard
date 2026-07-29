import { expect, test } from "@playwright/test";

test("similar evidence keeps candidate creation visible at narrow widths", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 720, height: 900 });
  await page.route("**/api/projects/default/candidates/*/similar?*", async (route) => {
    const response = await route.fetch();
    const rows = await response.json() as Array<Record<string, unknown>>;
    await route.fulfill({
      response,
      json: rows.map((row) => ({ ...row, process_key: null })),
    });
  });

  await page.goto("/?view=candidates&project=default");
  const scroll = page.locator(".similar-table-scroll");
  const actions = page.locator(".similar-evidence-panel").getByRole("button", { name: "候補にする" });
  await expect(actions.first()).toBeVisible();
  expect(await actions.count()).toBeGreaterThan(0);
  await expect(actions.first()).toBeDisabled();
  const before = await actions.first().boundingBox();
  const geometry = await scroll.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }));
  expect(geometry.scrollWidth).toBeGreaterThan(geometry.clientWidth);
  await scroll.evaluate((element) => { element.scrollLeft = element.scrollWidth; });
  const after = await actions.first().boundingBox();
  expect(before).not.toBeNull();
  expect(after).not.toBeNull();
  expect(Math.abs((after?.x ?? 0) - (before?.x ?? 0))).toBeLessThanOrEqual(1);
  await page.locator(".similar-evidence-panel").screenshot({
    path: testInfo.outputPath("sticky-candidate-action.png"),
  });
  await page.unrouteAll({ behavior: "wait" });
});

test("similar evidence opens its conditions without leaving candidate comparison", async ({ page }, testInfo) => {
  await page.goto("/?view=candidates&project=default");
  const evidence = page.locator(".similar-evidence-panel");
  const detail = evidence.getByRole("button", { name: "実績を見る" }).first();
  await detail.scrollIntoViewIfNeeded();
  const beforeUrl = page.url();
  const beforeScroll = await page.evaluate(() => window.scrollY);

  await detail.click();
  const drawer = page.getByRole("complementary", { name: "過去実績の根拠" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("heading", { name: "実測特性" })).toBeVisible();
  await expect(drawer.getByRole("heading", { name: "工程条件" })).toBeVisible();
  await expect(drawer.getByRole("heading", { name: /上流組成/ })).toBeVisible();
  await expect(drawer.getByRole("button", { name: "この実績を候補にする" })).toBeVisible();
  expect(page.url()).toBe(beforeUrl);
  expect(await page.evaluate(() => window.scrollY)).toBe(beforeScroll);
  await drawer.screenshot({ path: testInfo.outputPath("historical-evidence-drawer.png") });

  await drawer.getByRole("button", { name: "過去実績の根拠を閉じる" }).click();
  await expect(drawer).toBeHidden();
  await expect(detail).toBeFocused();
  expect(await page.evaluate(() => window.scrollY)).toBe(beforeScroll);
});

test("similar evidence asks which upstream condition to inherit when it is ambiguous", async ({ page }) => {
  await page.route("**/api/projects/default/candidates/*/similar?*", async (route) => {
    const response = await route.fetch();
    const rows = await response.json() as Array<Record<string, unknown>>;
    const source = rows.find((row) => row.process_key === "AN-02") ?? rows[0];
    await route.fulfill({
      response,
      json: [{
        ...source,
        observation_id: "ambiguous-similar-observation",
        observation_ids: ["ambiguous-similar-observation"],
        parent_key: "AN-02",
        process_key: "AN-02",
        melt_key: null,
      }],
    });
  });

  await page.goto("/?view=candidates&project=default");
  const evidence = page.locator(".similar-evidence-panel");
  await evidence.getByRole("button", { name: "候補にする" }).click();

  const upstream = evidence.getByRole("combobox", { name: "AN-02の上流条件" });
  await expect(upstream).toBeVisible();
  await expect(upstream.getByRole("option")).toHaveText([
    "選択してください",
    "焼鈍条件 AN-02 / 成分 ME-01",
    "焼鈍条件 AN-02 / 成分 ME-02",
  ]);
  const add = evidence.getByRole("button", { name: "選んで追加" });
  await expect(add).toBeDisabled();

  await upstream.selectOption({ label: "焼鈍条件 AN-02 / 成分 ME-02" });
  const created = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && response.url().includes("/lineage/AN-02/candidate")
  ));
  await add.click();

  const requestUrl = new URL((await created).url());
  expect(requestUrl.searchParams.get("process_key")).toBe("AN-02");
  expect(requestUrl.searchParams.get("melt_key")).toBe("ME-02");
  await expect(page.getByRole("status")).toContainText("近い過去実績を候補に追加しました");
  await page.unrouteAll({ behavior: "wait" });
});

test("candidate origin actual stays scoped to the selected composition and relation route", async ({ page }) => {
  const apiPort = process.env.PLAYWRIGHT_API_PORT ?? "9001";
  const created = await page.request.post(
    `http://127.0.0.1:${apiPort}/api/projects/hot-rolling-default/lineage/HR-02/candidate`,
    { params: { process_key: "HR-02", melt_key: "ME-01" } },
  );
  const createdBody = await created.text();
  if (!created.ok()) {
    throw new Error(`candidate creation failed (${created.status()}): ${createdBody}`);
  }
  const candidate = JSON.parse(createdBody) as { id: string };

  await page.goto(`/?view=candidates&project=hot-rolling-default&candidate=${candidate.id}`);

  const origin = page.locator(".candidate-origin-measurements");
  await expect(origin).toContainText("作成元実測");
  await expect(origin).toContainText("TS 470");
  await expect(origin).not.toContainText("505");

  await page.getByRole("button", { name: "作成元の実績を見る" }).click();
  const drawer = page.getByRole("complementary", { name: "過去実績の根拠" });
  await expect(drawer).toContainText("HR-02 / 成分 ME-01");
  await expect(drawer.locator(".historical-measurements")).toContainText("470");
  await expect(drawer.locator(".historical-measurements")).not.toContainText("505");
  await expect(drawer.getByRole("heading", { name: "工程条件" })).toBeVisible();
  await expect(drawer.getByRole("heading", { name: /上流組成 ME-01/ })).toBeVisible();

  await page.route("**/api/projects/hot-rolling-default/candidates/*/origin-evidence", async (route) => {
    await route.fulfill({ status: 409, json: { code: "origin_mismatch", message: "参照データ不一致" } });
  });
  await page.reload();
  await page.getByRole("button", { name: "作成元の実績を見る" }).click();
  const unavailableDrawer = page.getByRole("complementary", { name: "過去実績の根拠" });
  await expect(unavailableDrawer).toContainText("現在の集約値では代用していません");
  await expect(unavailableDrawer.locator(".historical-measurements")).not.toContainText("470");
  await expect(unavailableDrawer.locator(".historical-measurements")).not.toContainText("505");
});
