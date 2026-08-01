import { expect, test } from "@playwright/test";
import { expectNoBlockingAxeViolations } from "./axe";

const apiPort = Number(process.env.PLAYWRIGHT_API_PORT ?? 8876);
const apiBase = `http://127.0.0.1:${apiPort}`;

test("broken Chain evaluation is isolated and explained without hiding Projects", async ({
  page,
  request,
}) => {
  const failedApiResponses: string[] = [];
  page.on("response", (response) => {
    if (response.url().includes("/api/") && response.status() >= 500) {
      failedApiResponses.push(`${response.status()} ${response.url()}`);
    }
  });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "焼鈍条件の候補検討", level: 1 }),
  ).toBeVisible();

  const readiness = await request.get(`${apiBase}/api/readiness`);
  expect(readiness.ok()).toBeTruthy();
  const readinessBody = await readiness.json();
  const evaluation = readinessBody.optional_subsystems.find(
    (item: { subsystem_id: string }) => (
      item.subsystem_id === "chain_evaluation:welding-consumable-a-b-c-v1"
    ),
  );
  expect(evaluation.status).toBe("unavailable");
  expect(evaluation.recovery_hint).toContain("評価JSON");

  const projectsResponse = await request.get(`${apiBase}/api/projects`);
  const project = (await projectsResponse.json()).find(
    (item: { name: string }) => item.name === "保存済み証跡を確認するChain",
  );

  await page.goto(`/?view=project&project=${encodeURIComponent(project.id)}`);
  await expect(
    page.getByRole("heading", { name: "保存済み証跡を確認するChain", level: 1 }),
  ).toBeVisible();
  await expect(page.getByText("溶接材料Chainを利用できません。")).toBeVisible();
  await expect(
    page.getByText("溶接材料Chainの評価成果物を利用できません。"),
  ).toBeVisible();
  await expect(page.getByText(/復旧: 評価JSONのschema/)).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "候補と判断履歴" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "候補比較" }).click();
  await expect(page.getByRole("region", { name: "Chain候補作業面" })).toBeVisible();
  await expect(page.getByText("保存済みの候補・実行結果・Snapshot・実測analysisは参照できます。")).toBeVisible();
  await expect(page.getByText("固定されたChainの最新実行を表示しています")).toBeVisible();
  await expect(page.getByRole("button", { name: "全Stageを固定" })).toBeDisabled();
  await expect(page.getByText(/現revisionを固定済み/)).toBeVisible();
  await expect(page.getByText("request degraded-e2e-saved-execution")).toBeVisible();
  await expect(page.getByText("Stage A · 最新")).toBeVisible();
  await page.getByText("固定入力", { exact: true }).click();
  await expect(page.locator(".chain-snapshot-evidence pre").first()).toContainText(
    "\"candidate.blend\"",
  );
  await expectNoBlockingAxeViolations(page, "Chain unavailable");
  expect(failedApiResponses).toEqual([]);
});

test("a direct Chain deep link waits for templates and availability before loading Chain data", async ({
  page,
  request,
}) => {
  const projectsResponse = await request.get(`${apiBase}/api/projects`);
  const project = (await projectsResponse.json()).find(
    (item: { name: string }) => item.name === "保存済み証跡を確認するChain",
  );
  if (!project) throw new Error("Chain E2E Project was not seeded.");
  const prematureChainRequests: string[] = [];
  let releaseCatalogs!: () => void;
  const catalogsReleased = new Promise<void>((resolve) => {
    releaseCatalogs = resolve;
  });
  await page.route("**/api/subsystem-availability", async (route) => {
    await catalogsReleased;
    await route.continue();
  });
  await page.route("**/api/chains", async (route) => {
    await catalogsReleased;
    await route.continue();
  });
  page.on("request", (browserRequest) => {
    const url = browserRequest.url();
    if (
      url.includes(`/api/projects/${project.id}/chain/`)
      && !url.endsWith("/chain/evaluation")
    ) {
      prematureChainRequests.push(url);
    }
  });

  await page.goto(`/?view=project&project=${encodeURIComponent(project.id)}`);
  await expect(page.getByText("Chainの利用状況を確認しています", {
    exact: true,
  })).toBeVisible();
  await expect(page.getByRole("button", { name: /Chain候補を開く/ })).toBeDisabled();
  await expect(page.getByRole("button", { name: "このプロジェクトの続き" })).toBeDisabled();

  await page.goto(`/?view=candidates&project=${encodeURIComponent(project.id)}`);
  await expect(page.getByRole("heading", {
    name: "候補を読み込んでいます",
  })).toBeVisible();
  await page.waitForTimeout(250);
  expect(prematureChainRequests).toEqual([]);

  releaseCatalogs();
  await expect(page.getByRole("region", { name: "Chain候補作業面" })).toBeVisible();
  await expect(page.getByRole("button", { name: "全Stageを固定" })).toBeDisabled();
});

test("a failed availability catalog blocks a direct Chain deep link", async ({
  page,
  request,
}) => {
  const projectsResponse = await request.get(`${apiBase}/api/projects`);
  const project = (await projectsResponse.json()).find(
    (item: { name: string }) => item.name === "保存済み証跡を確認するChain",
  );
  if (!project) throw new Error("Chain E2E Project was not seeded.");
  const chainRequests: string[] = [];
  await page.route("**/api/subsystem-availability", (route) => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify({ detail: "availability catalog unavailable" }),
  }));
  page.on("request", (browserRequest) => {
    const url = browserRequest.url();
    if (url.includes(`/api/projects/${project.id}/chain/`)) {
      chainRequests.push(url);
    }
  });

  await page.goto(`/?view=project&project=${encodeURIComponent(project.id)}`);
  await expect(page.getByText("Chainの利用状況を取得できません", {
    exact: true,
  })).toBeVisible();
  await expect(page.getByText(
    "Chain評価の利用状況を取得できません",
  )).toBeVisible();
  await expect(page.getByRole("button", { name: /Chain候補を開く/ })).toBeDisabled();
  await expect(page.getByRole("button", { name: "このプロジェクトの続き" })).toBeDisabled();

  await page.goto(`/?view=candidates&project=${encodeURIComponent(project.id)}`);
  await expect(page.getByRole("heading", {
    name: "候補を表示できません",
  })).toBeVisible();
  await expect(page.getByText(
    "Chainの利用状況を取得できませんでした。再読み込みしてから操作してください。",
  )).toBeVisible();
  await expect(page.getByText(/FastAPI を/)).toHaveCount(0);
  await page.waitForTimeout(250);
  expect(chainRequests).toEqual([]);
});
