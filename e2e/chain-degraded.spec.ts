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
  await expect(page.getByText("固定されたA → B → Cを表示しています")).toBeVisible();
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
