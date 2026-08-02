import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import {
  apiBaseUrl,
  createProjectWithCandidate,
  starterCandidate,
} from "./helpers";

async function projectCandidate(request: APIRequestContext, projectId: string) {
  const response = await request.get(`${apiBaseUrl}/api/projects/${projectId}/candidates`);
  expect(response.status(), await response.text()).toBe(200);
  const candidates = await response.json() as Array<{ id: string; revision: number }>;
  expect(candidates).toHaveLength(1);
  return candidates[0];
}

async function createChainCandidate(request: APIRequestContext) {
  const chainsResponse = await request.get(`${apiBaseUrl}/api/chains`);
  expect(chainsResponse.status(), await chainsResponse.text()).toBe(200);
  const chains = await chainsResponse.json() as Array<{
    definition: { chain_id: string };
    revisions: Array<{ revision: number; revision_digest: string }>;
  }>;
  const chain = chains.find(
    (item) => item.definition.chain_id === "welding-consumable-a-b-c-v1",
  );
  expect(chain, "bundled Chain definition must be available").toBeTruthy();
  const revision = chain!.revisions[0];
  const projectResponse = await request.post(`${apiBaseUrl}/api/projects`, {
    data: {
      name: `Chain保存境界 ${Date.now()}`,
      scientific_identity: {
        identity_kind: "chain",
        chain_revision_id: `${chain!.definition.chain_id}:r${revision.revision}`,
        chain_revision_digest: revision.revision_digest,
      },
    },
  });
  expect(projectResponse.status(), await projectResponse.text()).toBe(201);
  const project = await projectResponse.json() as { id: string };
  const contractResponse = await request.get(
    `${apiBaseUrl}/api/projects/${project.id}/chain/candidate-contract`,
  );
  expect(contractResponse.status(), await contractResponse.text()).toBe(200);
  const contract = await contractResponse.json() as {
    external_inputs: Array<{
      external_path: string;
      candidate_path: string;
      kind: string;
      editable: boolean;
      allowed_range: { min: number; max: number } | null;
    }>;
    starter_candidate: object;
  };
  const candidateResponse = await request.post(
    `${apiBaseUrl}/api/projects/${project.id}/chain/candidates`,
    { data: contract.starter_candidate },
  );
  expect(candidateResponse.status(), await candidateResponse.text()).toBe(201);
  const candidate = await candidateResponse.json() as { id: string; revision: number };
  const executionResponse = await request.post(
    `${apiBaseUrl}/api/projects/${project.id}/chain/candidates/${candidate.id}/executions`,
    {
      data: {
        candidate_revision: candidate.revision,
        request_id: `save-boundary-${Date.now()}`,
        debounce_ms: 0,
      },
    },
  );
  expect(executionResponse.status(), await executionResponse.text()).toBe(200);
  return { project, candidate, contract };
}

function projectMenu(page: Page, name: string) {
  return page.getByRole("navigation", { name: "プロジェクト内メニュー" })
    .getByRole("button", { name, exact: true });
}

test("normal Candidate save settles before navigation and failure retains the draft", async ({ page }) => {
  const project = await createProjectWithCandidate(
    page.request,
    "annealed-properties-v1",
    `通常候補保存境界 ${Date.now()}`,
    "保存境界候補",
  );
  const candidate = await projectCandidate(page.request, project.id);
  let releaseFirstSave!: () => void;
  const firstSaveCanFinish = new Promise<void>((resolve) => {
    releaseFirstSave = resolve;
  });
  let firstSaveStarted!: () => void;
  const firstSaveDidStart = new Promise<void>((resolve) => {
    firstSaveStarted = resolve;
  });
  let saveAttempt = 0;
  let successfulSaveResponses = 0;
  let forceFailure = false;
  page.on("response", (response) => {
    if (
      response.request().method() === "PUT"
      && new URL(response.url()).pathname.endsWith(`/candidates/${candidate.id}`)
      && response.status() === 200
    ) successfulSaveResponses += 1;
  });
  await page.route(
    new RegExp(`/api/projects/${project.id}/candidates/${candidate.id}$`),
    async (route) => {
      if (route.request().method() !== "PUT") {
        await route.continue();
        return;
      }
      saveAttempt += 1;
      if (saveAttempt === 1) {
        firstSaveStarted();
        await firstSaveCanFinish;
        await route.continue();
        return;
      }
      if (forceFailure) {
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "forced candidate save failure" }),
        });
      } else {
        await route.continue();
      }
    },
  );

  await page.goto(`/?view=candidates&project=${project.id}&candidate=${candidate.id}`);
  const numeric = page.locator(
    ".comparison-detail-table tbody tr.selected-row input[type=number]",
  ).first();
  const originalValue = await numeric.inputValue();
  const firstValue = Number(originalValue) + 0.001;
  await numeric.fill(String(firstValue));
  await numeric.blur();
  const visibleFirstDraft = await numeric.inputValue();
  expect(visibleFirstDraft).not.toBe(originalValue);
  await projectMenu(page, "概要").click();
  await firstSaveDidStart;
  await expect(page).toHaveURL(/view=candidates/);
  await expect(numeric).toHaveValue(visibleFirstDraft);
  const laterValue = firstValue + 0.001;
  await numeric.fill(String(laterValue));
  await numeric.blur();

  const firstSaveResponse = page.waitForResponse((response) => (
    response.request().method() === "PUT"
    && new URL(response.url()).pathname.endsWith(`/candidates/${candidate.id}`)
  ));
  releaseFirstSave();
  expect((await firstSaveResponse).status()).toBe(200);
  await expect.poll(() => saveAttempt).toBe(2);
  await expect.poll(() => successfulSaveResponses).toBe(2);
  await expect(page).toHaveURL(/view=project/);

  await projectMenu(page, "候補比較").click();
  await expect(page).toHaveURL(/view=candidates/);
  forceFailure = true;
  const secondValue = Number(await numeric.inputValue()) + 0.001;
  await numeric.fill(String(secondValue));
  await numeric.blur();
  const visibleSecondDraft = await numeric.inputValue();
  const failedSaveResponse = page.waitForResponse((response) => (
    response.request().method() === "PUT"
    && new URL(response.url()).pathname.endsWith(`/candidates/${candidate.id}`)
  ));
  await page.goBack();
  expect((await failedSaveResponse).status()).toBe(500);
  await expect(page).toHaveURL(/view=candidates/);
  await expect(numeric).toHaveValue(visibleSecondDraft);
  await expect(page.locator(".workspace-notice.error")).toContainText("入力を保存できません");
  forceFailure = false;
  const retriedSave = page.waitForResponse((response) => (
    response.request().method() === "PUT"
    && new URL(response.url()).pathname.endsWith(`/candidates/${candidate.id}`)
  ));
  await page.goBack();
  expect((await retriedSave).status()).toBe(200);
  await expect(page).toHaveURL(/view=project/);
});

test("Chain Candidate save settles before navigation and invalid numeric input remains authoritative", async ({ page }) => {
  const { project, candidate, contract } = await createChainCandidate(page.request);
  const numericDefinition = contract.external_inputs.find((definition) => (
    definition.kind === "number"
    && definition.editable
    && definition.allowed_range
  ))!;
  const categoricalDefinition = contract.external_inputs.find((definition) => (
    definition.kind === "categorical"
    && definition.editable
    && definition.choices.length > 1
  ));
  let saveAttempt = 0;
  let forceFailure = true;
  let releaseFirstSave!: () => void;
  const firstSaveCanFinish = new Promise<void>((resolve) => {
    releaseFirstSave = resolve;
  });
  let firstSaveStarted!: () => void;
  const firstSaveDidStart = new Promise<void>((resolve) => {
    firstSaveStarted = resolve;
  });
  await page.route(
    new RegExp(`/api/projects/${project.id}/chain/candidates/${candidate.id}$`),
    async (route) => {
      if (route.request().method() !== "PUT") {
        await route.continue();
        return;
      }
      saveAttempt += 1;
      if (saveAttempt === 1) {
        firstSaveStarted();
        await firstSaveCanFinish;
        await route.continue();
        return;
      }
      if (forceFailure) {
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "forced Chain save failure" }),
        });
      } else {
        await route.continue();
      }
    },
  );

  await page.goto(`/?view=candidates&project=${project.id}&candidate=${candidate.id}`);
  const input = page.locator(
    `[data-chain-external-path="${numericDefinition.external_path}"] input`,
  );
  const firstValue = numericDefinition.allowed_range!.min
    + (numericDefinition.allowed_range!.max - numericDefinition.allowed_range!.min) * 0.4;
  await input.fill(String(firstValue));
  await projectMenu(page, "概要").click();
  await firstSaveDidStart;
  await expect(page).toHaveURL(/view=candidates/);
  await expect(input).toHaveValue(String(firstValue));

  const firstSaveResponse = page.waitForResponse((response) => (
    response.request().method() === "PUT"
    && new URL(response.url()).pathname.endsWith(`/chain/candidates/${candidate.id}`)
  ));
  releaseFirstSave();
  expect((await firstSaveResponse).status()).toBe(200);
  await expect(page).toHaveURL(/view=project/);

  await projectMenu(page, "候補比較").click();
  await expect(page).toHaveURL(/view=candidates/);
  const secondValue = firstValue
    + (numericDefinition.allowed_range!.max - numericDefinition.allowed_range!.min) * 0.05;
  await input.fill(String(secondValue));
  const failedSaveResponse = page.waitForResponse((response) => (
    response.request().method() === "PUT"
    && new URL(response.url()).pathname.endsWith(`/chain/candidates/${candidate.id}`)
  ));
  await projectMenu(page, "概要").click();
  expect((await failedSaveResponse).status()).toBe(500);
  await expect(page).toHaveURL(/view=candidates/);
  await expect(input).toHaveValue(String(secondValue));
  await expect(page.locator(".chain-status-line")).toContainText("入力はこの画面に保持しています");
  forceFailure = false;
  const retriedSave = page.waitForResponse((response) => (
    response.request().method() === "PUT"
    && new URL(response.url()).pathname.endsWith(`/chain/candidates/${candidate.id}`)
  ));
  await projectMenu(page, "概要").click();
  expect((await retriedSave).status()).toBe(200);
  await expect(page).toHaveURL(/view=project/);

  if (categoricalDefinition) {
    await projectMenu(page, "候補比較").click();
    await expect(page).toHaveURL(/view=candidates/);
    await input.fill("");
    const category = page.locator(
      `[data-chain-external-path="${categoricalDefinition.external_path}"] select`,
    );
    const currentCategory = await category.inputValue();
    const nextCategory = categoricalDefinition.choices.find((choice) => choice !== currentCategory)!;
    await category.selectOption(nextCategory);
    await projectMenu(page, "概要").click();
    await expect(page).toHaveURL(/view=candidates/);
    await expect(input).toHaveValue("");
  }
});

test("Actual draft survives Candidate revision churn until explicit rebase", async ({ page }) => {
  const project = await createProjectWithCandidate(
    page.request,
    "annealed-properties-v1",
    `実測draft境界 ${Date.now()}`,
    "実測draft候補",
  );
  const candidate = await projectCandidate(page.request, project.id);
  await page.goto(`/?view=candidates&project=${project.id}&candidate=${candidate.id}`);
  const panel = page.getByRole("region", { name: "予測と実測の照合" });
  await panel.getByRole("button", { name: "実測を登録" }).click();
  await panel.getByLabel("実測値", { exact: true }).fill("512");
  await panel.getByLabel("実験番号").fill("EXP-SAVE-42");
  await panel.getByLabel("メモ").fill("revisionを跨いで保持");

  const numeric = page.locator(
    ".comparison-detail-table tbody tr.selected-row input[type=number]",
  ).first();
  const nextValue = Number(await numeric.inputValue()) + 0.001;
  const candidateSaved = page.waitForResponse((response) => (
    response.request().method() === "PUT"
    && new URL(response.url()).pathname.endsWith(`/candidates/${candidate.id}`)
  ));
  await numeric.fill(String(nextValue));
  await numeric.blur();
  const savedCandidate = await (await candidateSaved).json() as { revision: number };

  const warning = panel.locator(".actual-revision-warning");
  await expect(warning).toContainText(`編集版 ${candidate.revision} から ${savedCandidate.revision}`);
  await expect(panel.getByLabel("実測値", { exact: true })).toHaveValue("512");
  await expect(panel.getByLabel("実験番号")).toHaveValue("EXP-SAVE-42");
  await expect(panel.getByLabel("メモ")).toHaveValue("revisionを跨いで保持");
  await expect(warning.getByRole("button", { name: "入力を破棄" })).toBeVisible();
  await expect(panel.getByRole("button", { name: new RegExp(`編集版 ${candidate.revision} の予測と実測を保存`) })).toBeDisabled();

  await warning.getByRole("button", {
    name: `現在の編集版 ${savedCandidate.revision} へ引き継ぐ`,
  }).click();
  await expect(warning).toHaveCount(0);
  const saveButton = panel.getByRole("button", {
    name: `編集版 ${savedCandidate.revision} の予測と実測を保存`,
  });
  await expect(saveButton).toBeFocused();
  let releaseActual!: () => void;
  const actualCanFinish = new Promise<void>((resolve) => {
    releaseActual = resolve;
  });
  let failHistoryRefresh = false;
  await page.route(
    new RegExp(`/api/projects/${project.id}/candidates/${candidate.id}/actuals$`),
    async (route) => {
      if (route.request().method() === "POST") await actualCanFinish;
      await route.continue();
    },
  );
  await page.route(
    new RegExp(`/api/projects/${project.id}/candidates/${candidate.id}/prediction-vs-actual$`),
    async (route) => {
      if (failHistoryRefresh) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "forced history refresh failure" }),
        });
      } else {
        await route.continue();
      }
    },
  );
  const actualCreated = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname.endsWith(`/candidates/${candidate.id}/actuals`)
  ));
  failHistoryRefresh = true;
  await saveButton.click();
  await expect(panel.getByLabel("実測値", { exact: true })).toBeDisabled();
  await expect(panel.getByLabel("メモ")).toBeDisabled();
  await expect(panel.getByRole("button", { name: "入力を閉じる" })).toBeDisabled();
  releaseActual();
  const actualResponse = await actualCreated;
  expect(actualResponse.status(), await actualResponse.text()).toBe(201);
  const receipt = await actualResponse.json() as { snapshot_id: string };
  expect(receipt.snapshot_id).toBeTruthy();
  await expect(panel.getByRole("button", { name: "実測を登録" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "実測を登録" })).toBeFocused();
  await expect(panel.getByRole("status")).toContainText("実測と固定Snapshotは保存済みです");
});

test("an in-flight Actual save does not leave a newly selected Candidate saving", async ({ page }) => {
  const project = await createProjectWithCandidate(
    page.request,
    "annealed-properties-v1",
    `実測owner境界 ${Date.now()}`,
    "実測owner候補A",
  );
  const [first] = await (await page.request.get(
    `${apiBaseUrl}/api/projects/${project.id}/candidates`,
  )).json() as Array<{ id: string; revision: number }>;
  const starter = await starterCandidate(page.request, "annealed-properties-v1");
  const secondResponse = await page.request.post(
    `${apiBaseUrl}/api/projects/${project.id}/candidates`,
    { data: { ...starter, name: "実測owner候補B" } },
  );
  expect(secondResponse.status(), await secondResponse.text()).toBe(201);
  const second = await secondResponse.json() as { id: string };
  let releaseActual!: () => void;
  const actualCanFinish = new Promise<void>((resolve) => {
    releaseActual = resolve;
  });
  await page.route(
    new RegExp(`/api/projects/${project.id}/candidates/${first.id}/actuals$`),
    async (route) => {
      if (route.request().method() === "POST") await actualCanFinish;
      await route.continue();
    },
  );

  await page.goto(`/?view=candidates&project=${project.id}&candidate=${first.id}`);
  let panel = page.getByRole("region", { name: "予測と実測の照合" });
  await panel.getByRole("button", { name: "実測を登録" }).click();
  await panel.getByLabel("実測値", { exact: true }).fill("512");
  await panel.getByRole("button", { name: /の予測と実測を保存/ }).click();
  await page.getByRole("button", { name: "実測owner候補Bを選択" }).click();
  await expect(page).toHaveURL(new RegExp(`candidate=${second.id}`));
  panel = page.getByRole("region", { name: "予測と実測の照合" });
  await panel.getByRole("button", { name: "実測を登録" }).click();
  await expect(panel.getByRole("button", { name: /の予測と実測を保存/ })).not.toContainText("保存中");
  releaseActual();
});
