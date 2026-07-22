import { expect, test, type APIRequestContext } from "@playwright/test";

const api = "http://127.0.0.1:8875";

async function createIsolatedProject(request: APIRequestContext) {
  const catalog = await (await request.get(`${api}/api/task-definitions`)).json() as Array<{ definition: { task_definition: { id: string } }; starter_candidate: Record<string, unknown> }>;
  const task = catalog.find((item) => item.definition.task_definition.id === "annealed-properties-v1")!;
  const created = await request.post(`${api}/api/projects`, { data: { name: `振り返りE2E ${Date.now()}`, task_id: "annealed-properties-v1" } });
  expect(created.status()).toBe(201);
  const project = await created.json() as { id: string };
  const candidate = await request.post(`${api}/api/projects/${project.id}/candidates`, { data: { ...task.starter_candidate, name: "振り返り候補" } });
  expect(candidate.status()).toBe(201);
  return project.id;
}

test("primary navigation follows the decision flow and separates developer administration", async ({ page }) => {
  await page.goto("/?view=project&project=default");

  await expect(page.locator(".topbar nav").getByRole("button")).toHaveText(["プロジェクト概要"]);
  await expect(page.getByRole("navigation", { name: "プロジェクト内メニュー" }).getByRole("button")).toHaveText([
    "データ探索",
    "範囲探索",
    "候補比較",
    "開発・管理",
  ]);

  await page.getByRole("button", { name: "データ探索", exact: true }).click();
  await expect(page.getByRole("heading", { name: "調べるノードを選択してください" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "データ探索" })).toBeVisible();
  await page.getByRole("button", { name: "問題から探す", exact: true }).click();
  await expect(page.getByRole("heading", { name: "問題から探す" })).toBeVisible();
  await expect(page.locator(".quality-filters")).toBeVisible();
  await expect(page.locator(".quality-summary")).toHaveCount(0);
  await expect(page.locator(".reference-scenarios")).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "プロジェクト内メニュー" }).getByRole("button", { name: "データ探索" })).toHaveClass(/active/);
  const qualityUrl = page.url();
  await page.getByRole("navigation", { name: "プロジェクト内メニュー" }).getByRole("button", { name: "データ探索" }).click();
  await expect(page).toHaveURL(qualityUrl);

  await page.getByRole("button", { name: "開発・管理", exact: true }).click();
  await expect(page.getByRole("heading", { name: "検証と構成" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "データ品質集計" })).toBeVisible();
  await expect(page.locator(".quality-summary")).toBeVisible();
  await expect(page.locator(".quality-filters")).toHaveCount(0);
  await page.getByRole("button", { name: "予測タスク定義", exact: true }).click();
  await expect(page).toHaveURL(/admin=task/);
  await expect(page.getByRole("heading", { name: "予測タスク定義" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "成分" })).toBeVisible();
  await expect(page.getByText("引張強さ").first()).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "予測タスク定義" })).toBeVisible();
  await page.getByRole("button", { name: "モデルと実行環境", exact: true }).click();
  await expect(page).toHaveURL(/admin=model/);
  await expect(page.getByRole("heading", { name: "モデルと実行環境" })).toBeVisible();
  await expect(page.locator(".admin-model-identity")).toBeVisible();
});

test("saved prediction returns directly to project history", async ({ page, request }) => {
  const projectId = await createIsolatedProject(request);
  await page.goto(`/?view=candidates&project=${projectId}`);
  const save = page.getByRole("button", { name: /の詳細予測を保存/ });
  await expect(save).toBeEnabled();
  const snapshotResponse = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname.endsWith("/predict"));
  await save.click();
  expect((await snapshotResponse).status()).toBe(200);

  await page.getByRole("button", { name: "保存結果・履歴" }).click();
  await expect(page).toHaveURL(new RegExp(`view=project.*project=${projectId}`));
  await expect(page.getByRole("heading", { name: "候補と判断履歴" })).toBeVisible();
  await expect(page.getByText("固定した予測").first()).toBeVisible();
});

test("hot rolling remains a project task and uses task labels", async ({ page }) => {
  await page.goto("/?view=candidates&project=hot-rolling-default");
  await expect(page.locator(".topbar nav")).not.toContainText("熱延");
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await expect(page.getByText("均熱温度", { exact: true }).first()).toBeVisible();
  await expect(page.locator(".comparison-detail-table thead .prediction-col").filter({ hasText: "引張強さ" })).toBeVisible();
});
