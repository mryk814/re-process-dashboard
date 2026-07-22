import { expect, test, type APIRequestContext } from "@playwright/test";

const api = "http://127.0.0.1:8875";

async function createProject(request: APIRequestContext, taskId: string) {
  const catalog = await (await request.get(`${api}/api/task-definitions`)).json() as Array<{ definition: { task_definition: { id: string } }; starter_candidate: Record<string, unknown> }>;
  const task = catalog.find((item) => item.definition.task_definition.id === taskId)!;
  const response = await request.post(`${api}/api/projects`, { data: {
    name: `探索E2E ${taskId} ${Date.now()}`,
    task_id: taskId,
  } });
  expect(response.status()).toBe(201);
  const project = await response.json() as { id: string };
  const candidate = await request.post(`${api}/api/projects/${project.id}/candidates`, { data: { ...task.starter_candidate, name: "探索基準" } });
  expect(candidate.status()).toBe(201);
  return project;
}

test("annealed screening keeps draft separate and batches multiple points into stock", async ({ page, request }) => {
  const project = await createProject(request, "annealed-properties-v1");
  await page.goto(`/?view=explore&project=${project.id}`);
  await expect(page.getByRole("heading", { name: "範囲探索" })).toBeVisible();
  await expect(page.locator("optgroup[label='成分']")).toHaveCount(2);
  await expect(page.locator("optgroup[label='焼鈍条件']")).toHaveCount(2);
  await expect(page.locator("optgroup[label='焼鈍履歴'] option[value='heat_pattern.1.temperature_c']")).toHaveCount(2);

  await page.getByRole("button", { name: "変数を追加" }).click();
  const rows = page.locator(".variable-table tbody tr");
  await rows.nth(2).getByRole("combobox").nth(1).selectOption("range");
  await rows.nth(2).locator("input").nth(0).fill("0.8");
  await rows.nth(2).locator("input").nth(1).fill("2.0");
  await page.getByLabel(/副条件: 降伏強さ/).fill("350");

  const runResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/screening");
  await page.getByRole("button", { name: "探索を実行" }).click();
  expect((await runResponse).status()).toBe(201);
  await expect(page.locator(".screening-hidden-variables")).toContainText("Mn");
  await expect(page.getByLabel("X軸")).toBeVisible();
  await expect(page.getByLabel("Y軸")).toBeVisible();
  await expect(page.locator(".screening-display-controls").getByLabel("色")).toBeVisible();
  await expect(page.getByRole("region", { name: "選択した探索点の詳細" })).toContainText("引張強さ");
  await expect(page.getByRole("region", { name: "選択した探索点の詳細" })).toContainText("降伏強さ");
  await rows.nth(2).locator("input").nth(0).fill("0.9");
  await expect(page.getByText(/未実行の条件変更/)).toBeVisible();

  await page.getByLabel("目標特性").selectOption("YS");
  const rerunRequest = page.waitForRequest((request) => request.method() === "POST" && new URL(request.url()).pathname === "/api/screening");
  const rerunResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/screening");
  await page.getByRole("button", { name: "探索を実行" }).click();
  const rerunPayload = (await rerunRequest).postDataJSON() as { target: string; secondary_targets: Record<string, number> };
  expect(rerunPayload.target).toBe("YS");
  expect(rerunPayload.secondary_targets).not.toHaveProperty("YS");
  expect((await rerunResponse).status()).toBe(201);
  await expect(page.getByText(/未実行の条件変更/)).toHaveCount(0);

  const pointChecks = page.locator('input[aria-label^="点 "]');
  await pointChecks.nth(0).check();
  await pointChecks.nth(1).check();
  const batchResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/candidates"));
  await page.getByRole("button", { name: "2件を候補へ追加" }).click();
  expect((await batchResponse).status()).toBe(201);
  await expect(page).toHaveURL(/view=explore/);
  const candidates = await (await request.get(`${api}/api/projects/${project.id}/candidates`)).json() as Array<{ provenance?: { source_kind: string; source_ref: { run_id?: string; point_index?: number } } }>;
  expect(candidates.filter((candidate) => candidate.provenance?.source_kind === "screening")).toHaveLength(2);

  await page.getByRole("button", { name: "候補比較へ" }).click();
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
});

test("hot rolling screening accepts task-defined process fields", async ({ page, request }) => {
  const project = await createProject(request, "hot-rolled-properties-v1");
  await page.goto(`/?view=explore&project=${project.id}`);
  const rows = page.locator(".variable-table tbody tr");
  await rows.nth(0).getByRole("combobox").first().selectOption("process.soaking_temperature_c");
  await rows.nth(0).getByRole("combobox").nth(1).selectOption("range");
  await rows.nth(0).locator("input").nth(0).fill("1170");
  await rows.nth(0).locator("input").nth(1).fill("1190");
  await rows.nth(1).getByRole("combobox").first().selectOption("process.finish_temperature_c");
  await rows.nth(1).getByRole("combobox").nth(1).selectOption("range");
  await rows.nth(1).locator("input").nth(0).fill("850");
  await rows.nth(1).locator("input").nth(1).fill("930");

  const runResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/screening");
  await page.getByRole("button", { name: "探索を実行" }).click();
  const response = await runResponse;
  expect(response.status(), await response.text()).toBe(201);
  const body = await response.json() as { points: Array<{ inputs: Record<string, number | string>; predictions: Record<string, unknown> }> };
  expect(body.points[0].inputs["process.soaking_temperature_c"]).toBeGreaterThanOrEqual(1170);
  expect(body.points[0].inputs["process.finish_temperature_c"]).toBeGreaterThanOrEqual(850);
  expect(Object.keys(body.points[0].predictions)).toEqual(["TS"]);
  await expect(page.getByLabel("X軸")).toHaveValue("process.soaking_temperature_c");
});
