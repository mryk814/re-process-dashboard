import { expect, test } from "@playwright/test";

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
  await evidence.getByRole("button", { name: "実測から候補化" }).click();

  const upstream = evidence.getByRole("combobox", { name: "AN-02の上流条件" });
  await expect(upstream).toBeVisible();
  await expect(upstream.getByRole("option")).toHaveText([
    "選択してください",
    "焼鈍条件 AN-02 / 成分 ME-01",
    "焼鈍条件 AN-02 / 成分 ME-02",
  ]);
  const add = evidence.getByRole("button", { name: "選んで候補化" });
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
});
