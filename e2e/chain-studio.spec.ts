import { expect, test } from "@playwright/test";

import { expectNoBlockingAxeViolations } from "./axe";
import { apiBaseUrl } from "./helpers";

test("Prediction Graph Studio completes the same draft through canvas and linear controls", async ({ page }) => {
  const graphId = `graph-studio-e2e-${Date.now()}`;
  const serverErrors: string[] = [];
  const taskDefinitionRequests: string[] = [];
  const publishRequests: string[] = [];
  const projectCreateRequests: string[] = [];
  let expectedCatalogFailure = false;
  page.on("request", (request) => {
    if (request.url().endsWith("/api/prediction-graphs/publish")) publishRequests.push(request.url());
    if (request.url().endsWith("/api/prediction-graphs/projects")) projectCreateRequests.push(request.url());
  });
  page.on("response", (response) => {
    if (response.status() >= 500 && !expectedCatalogFailure) {
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
  await page.getByRole("button", { name: "draftを保存" }).click();
  await expect(page.getByText("保存済み v1")).toBeVisible();
  await expect(page).toHaveURL(/[?&]draft=/);
  const draftId = new URL(page.url()).searchParams.get("draft");
  expect(draftId).toBeTruthy();
  const secondTab = await page.context().newPage();
  await secondTab.goto(`/?view=chain-studio&draft=${draftId}`);
  await expect(secondTab.getByLabel("Graph ID")).toHaveValue(graphId);
  await expect(secondTab.getByText("保存済み v1")).toBeVisible();
  await secondTab.getByRole("button", { name: "データライブラリ" }).click();
  await expect(secondTab).toHaveURL(/view=data-library/);
  expect(new URL(secondTab.url()).searchParams.has("draft")).toBe(false);
  expect(new URL(secondTab.url()).hash).not.toContain("draft");
  await secondTab.close();
  const missingDraftTab = await page.context().newPage();
  await missingDraftTab.goto("/?view=chain-studio&draft=other-workspace-draft");
  await expect(missingDraftTab.getByRole("alert").filter({ hasText: "保存済みdraftを再開できませんでした" })).toContainText(
    "draft other-workspace-draft は現在のWorkspaceにありません。",
  );
  await expect(missingDraftTab).toHaveURL(/[?&]draft=other-workspace-draft/);
  await expect(missingDraftTab.getByLabel("Graph ID")).toBeDisabled();
  await expect(missingDraftTab.getByRole("button", { name: "新しいdraftを開始" })).toBeVisible();
  await missingDraftTab.close();
  const draftResponse = await page.request.get(`${apiBaseUrl}/api/prediction-graph-drafts/${draftId}`);
  expect(draftResponse.status(), await draftResponse.text()).toBe(200);
  const serverDraft = await draftResponse.json() as {
    version: number;
    content: {
      definition: {
        label: string;
        stages: Array<{ stage_kind: string; contract_id: string }>;
      };
      project_name: string;
    };
  };
  serverDraft.content.definition.label = "別画面で保存したGraph";
  serverDraft.content.project_name = "別画面で保存したProject";
  const competingSave = await page.request.put(
    `${apiBaseUrl}/api/prediction-graph-drafts/${draftId}`,
    { data: { expected_version: serverDraft.version, content: serverDraft.content } },
  );
  expect(competingSave.status(), await competingSave.text()).toBe(200);

  await page.getByLabel("表示名／目的").fill("手元で続けたGraph");
  await page.getByRole("button", { name: "draftを保存" }).click();
  const conflict = page.getByRole("alert").filter({ hasText: "サーバーに新しいdraft v2があります" });
  await expect(conflict).toContainText("別画面で保存したGraph ／ 別画面で保存したProject");
  await conflict.getByRole("button", { name: "手元版で上書き" }).click();
  await expect(page.getByText("保存済み v3")).toBeVisible();

  expectedCatalogFailure = true;
  await page.route("**/api/prediction-graphs/catalog", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ message: "catalog maintenance", code: "subsystem_unavailable" }),
    });
  });
  await page.reload();
  await expect(heading).toBeVisible();
  await expect(page.getByRole("heading", { name: "catalogなしで保持しているdraft" })).toBeVisible();
  await expect(page.getByText(`${graphId}`)).toBeVisible();
  await expect(page.getByText("1 Node · 2 Binding · 1 Decision Output")).toBeVisible();
  await expect(page.getByRole("alert").filter({ hasText: "Node catalogの取得に失敗しました" })).toContainText("catalog maintenance");
  expectedCatalogFailure = false;
  await page.unroute("**/api/prediction-graphs/catalog");
  await page.getByRole("button", { name: "Node catalogだけを再取得" }).click();
  await expect(page.getByLabel("表示名／目的")).toHaveValue("手元で続けたGraph");
  await expect(page.getByLabel("作成するProject名")).toHaveValue("Graph Studio Project smoke");
  await expect(page.getByText("保存済み v3")).toBeVisible();

  const catalogResponse = await page.request.get(`${apiBaseUrl}/api/prediction-graphs/catalog`);
  expect(catalogResponse.status(), await catalogResponse.text()).toBe(200);
  const unavailableCatalog = await catalogResponse.json() as {
    stages: Array<{
      stage_kind: string;
      contract_id: string;
      status: string;
      reason: string | null;
      surface: unknown;
    }>;
  };
  const draftStage = serverDraft.content.definition.stages[0];
  const unavailableItem = unavailableCatalog.stages.find((item) => (
    item.stage_kind === draftStage.stage_kind && item.contract_id === draftStage.contract_id
  ));
  expect(unavailableItem).toBeTruthy();
  unavailableItem!.status = "unavailable";
  unavailableItem!.reason = "fixtureで一時利用不可";
  unavailableItem!.surface = null;
  await page.route("**/api/prediction-graphs/catalog", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(unavailableCatalog) });
  });
  await page.reload();
  const unavailableReference = page.getByRole("alert").filter({ hasText: "現在利用できないNode参照があります" });
  await expect(unavailableReference).toContainText(`${draftStage.stage_kind} · ${draftStage.contract_id}`);
  await expect(unavailableReference).toContainText("input Binding 2件");
  await expect(unavailableReference.getByRole("button", { name: "接続と判断出力を外して置換" })).toBeVisible();
  await expect(unavailableReference.getByRole("button", { name: "参照と依存を削除" })).toBeVisible();
  await page.unroute("**/api/prediction-graphs/catalog");
  await unavailableReference.getByRole("button", { name: "Node catalogだけを再取得" }).click();
  await expect(unavailableReference).toHaveCount(0);

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

  const publishResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/prediction-graphs/publish"
  ));
  const projectCreateResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/prediction-graphs/projects"
  ));
  await page.getByRole("button", { name: "Revisionを公開してProjectを作成" }).focus();
  await page.keyboard.press("Enter");
  expect((await publishResponse).status()).toBe(201);
  expect((await projectCreateResponse).status()).toBe(201);
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
  await page.getByLabel("Graph ID").fill(`${graphId}-navigation-guard`);
  const publishCount = publishRequests.length;
  const projectCreateCount = projectCreateRequests.length;
  let releasePublish!: () => void;
  let releaseProjectCreate!: () => void;
  const publishReleased = new Promise<void>((resolve) => { releasePublish = resolve; });
  const projectCreateReleased = new Promise<void>((resolve) => { releaseProjectCreate = resolve; });
  let reportPublishStarted!: () => void;
  let reportProjectCreateStarted!: () => void;
  const publishStarted = new Promise<void>((resolve) => { reportPublishStarted = resolve; });
  const projectCreateStarted = new Promise<void>((resolve) => { reportProjectCreateStarted = resolve; });
  await page.route("**/api/prediction-graphs/publish", async (route) => {
    reportPublishStarted();
    await publishReleased;
    await route.continue();
  }, { times: 1 });
  await page.route("**/api/prediction-graphs/projects", async (route) => {
    reportProjectCreateStarted();
    await projectCreateReleased;
    await route.continue();
  }, { times: 1 });
  await page.getByRole("button", { name: "Revisionを公開してProjectを作成" }).click();
  await publishStarted;
  await page.getByRole("button", { name: "データライブラリ" }).click();
  await expect(page).toHaveURL(/view=chain-studio/);
  releasePublish();
  await projectCreateStarted;
  await page.getByRole("button", { name: "データライブラリ" }).click();
  await expect(page).toHaveURL(/view=chain-studio/);
  releaseProjectCreate();
  await expect(page).toHaveURL(/view=project.*project=/);
  expect(publishRequests).toHaveLength(publishCount + 1);
  expect(projectCreateRequests).toHaveLength(projectCreateCount + 1);
  expect(serverErrors).toEqual([]);
});
