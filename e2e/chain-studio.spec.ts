import { expect, test } from "@playwright/test";

import { expectNoBlockingAxeViolations } from "./axe";
import { apiBaseUrl } from "./helpers";

test("Prediction Graph Studio completes the same draft through canvas and linear controls", async ({ page }) => {
  const graphId = `graph-studio-e2e-${Date.now()}`;
  const serverErrors: string[] = [];
  const taskDefinitionRequests: string[] = [];
  const publishRequests: string[] = [];
  const projectCreateRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/prediction-graphs/publish")) publishRequests.push(request.url());
    if (request.url().endsWith("/api/prediction-graphs/projects")) projectCreateRequests.push(request.url());
  });
  page.on("response", (response) => {
    if (response.status() >= 500) {
      serverErrors.push(`${response.status()} ${response.request().method()} ${response.url()}`);
    }
    if (response.url().includes("/task-definition")) taskDefinitionRequests.push(response.url());
  });
  await page.route("**/api/prediction-graphs/validate", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 200));
    await route.continue();
  });
  await page.goto("/?view=chain-studio");

  const heading = page.getByRole("heading", { name: "入力・Model・判断出力を直接つなぐ" });
  await expect(heading).toBeVisible();
  await expect(page.locator(".chain-studio-canvas .input-node").first()).toBeVisible();
  await expect(page.locator(".chain-studio-canvas .model-node")).toHaveCount(1);
  await expect(page.locator(".chain-studio-canvas .decision-node")).toHaveCount(1);
  await expect(page.getByText("この一覧だけでnode追加、接続、削除、並べ替え、公開まで完了できます。")).toBeVisible();
  await expect(page.locator(".chain-studio-edges path[data-edge-kind='binding']")).toHaveCount(2);
  await expect(page.locator(".chain-studio-edges path[data-edge-kind='decision_output']")).toHaveCount(1);

  await page.getByLabel("Graph ID").fill(graphId);
  await page.getByLabel("表示名／目的").fill("Graph Studio keyboard smoke");
  await page.getByLabel("作成するProject名").fill("Graph Studio Project smoke");

  await page.locator(".model-node .chain-studio-node-title").click();
  await expect(page.locator(".chain-studio-inspector")).toContainText("contract digest");

  const bindingCount = await page.getByRole("group", { name: "Binding一覧" }).locator(".binding-row").count();
  await page.locator(".model-node .chain-studio-port.output").first().click();
  await page.locator(".model-node .chain-studio-port.input").first().click();
  await expect(page.locator(".chain-studio-port-error")).toContainText("互換ではありません");
  await expect(page.getByRole("group", { name: "Binding一覧" }).locator(".binding-row")).toHaveCount(bindingCount);

  const outputGroup = page.getByRole("group", { name: "Decision Output一覧" });
  await outputGroup.getByRole("button", { name: "削除" }).first().focus();
  await page.keyboard.press("Enter");
  const addOutput = outputGroup.getByRole("button", { name: /Decision Outputへ追加/ }).first();
  await addOutput.focus();
  await page.keyboard.press("Enter");

  const inputGroup = page.getByRole("group", { name: "Input一覧" });
  await inputGroup.getByRole("button", { name: "削除" }).first().click();
  await page.getByRole("button", { name: "Graphを検証" }).click();
  await expect(page.getByLabel("Graph ID")).toBeDisabled();
  const finding = page.locator(".chain-studio-findings.invalid li button").filter({ hasText: /unbound|required/ }).first();
  await expect(finding).toBeVisible();
  await finding.click();
  await expect(page.locator(".model-node .chain-studio-port.input:focus")).toHaveCount(1);
  await page.getByRole("group", { name: "Binding一覧" }).locator(".binding-row").first().getByRole("button", { name: "Inputを作成して接続" }).click();
  await page.getByRole("button", { name: "Graphを検証" }).click();
  await expect(page.locator(".chain-studio-findings.valid")).toContainText("公開可能");
  const digest = await page.locator(".chain-studio-findings code").textContent();
  await page.getByRole("button", { name: "compact" }).click();
  await page.getByRole("button", { name: "fit" }).click();
  await expect(page.locator(".chain-studio-findings code")).toHaveText(digest!);

  const nodeGroup = page.getByRole("group", { name: "Node一覧" });
  const transformOption = nodeGroup.locator("select option").filter({ hasText: "Transform" }).first();
  const transformValue = await transformOption.getAttribute("value");
  expect(transformValue).toBeTruthy();
  await nodeGroup.locator("select").selectOption(transformValue!);
  await nodeGroup.getByRole("button", { name: "Nodeを追加" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".chain-studio-canvas .transform-node")).toHaveCount(1);
  const transformRow = nodeGroup.locator(".node-row").filter({ hasText: "Transform" });
  await transformRow.getByRole("button", { name: "削除" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".chain-studio-canvas .transform-node")).toHaveCount(0);

  await page.getByRole("button", { name: "Revisionを公開してProjectを作成" }).focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/view=project.*project=/);
  const projectId = new URL(page.url()).searchParams.get("project");
  expect(projectId).toBeTruthy();
  await expect(page.getByRole("heading", { name: "Graph Studio Project smoke", exact: true })).toBeVisible();
  await expect(page.getByText("Prediction Graph Revisionを固定したProjectです")).toBeVisible();
  await expect(page.getByRole("button", { name: /Graph Studio Project smoke/ })).toContainText(`Graph Revision · ${graphId}:r1`);

  const projectResponse = await page.request.get(`${apiBaseUrl}/api/projects/${projectId}`);
  expect(projectResponse.status(), await projectResponse.text()).toBe(200);
  const project = await projectResponse.json() as {
    name: string;
    scientific_identity: { identity_kind: string; graph_revision_id: string };
  };
  expect(project.name).toBe("Graph Studio Project smoke");
  expect(project.scientific_identity.identity_kind).toBe("prediction_graph");
  expect(project.scientific_identity.graph_revision_id).toContain(`${graphId}:r1`);
  await page.goto(`/?view=candidates&project=${projectId}`);
  await expect(page.getByRole("heading", { name: "この画面はPrediction Graph Projectでは利用できません" })).toBeVisible();
  expect(taskDefinitionRequests.filter((url) => url.includes(`/api/projects/${projectId}/task-definition`))).toEqual([]);
  await expectNoBlockingAxeViolations(page);

  await page.goto("/?view=chain-studio");
  await expect(heading).toBeVisible();
  const publishCount = publishRequests.length;
  const projectCreateCount = projectCreateRequests.length;
  const validationStarted = page.waitForRequest((request) => request.url().endsWith("/api/prediction-graphs/validate"));
  await page.getByRole("button", { name: "Revisionを公開してProjectを作成" }).click();
  await validationStarted;
  await page.getByRole("button", { name: "データライブラリ" }).click();
  await expect(page).toHaveURL(/view=data-library/);
  await page.waitForTimeout(300);
  expect(publishRequests).toHaveLength(publishCount);
  expect(projectCreateRequests).toHaveLength(projectCreateCount);
  await expect(page).toHaveURL(/view=data-library/);
  expect(serverErrors).toEqual([]);
});
