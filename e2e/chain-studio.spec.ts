import { expect, test } from "@playwright/test";

import { expectNoBlockingAxeViolations } from "./axe";
import { apiBaseUrl } from "./helpers";

test("scalar Chain Studio validates typed bindings and publishes an immutable revision", async ({ page }) => {
  const chainId = `studio-e2e-${Date.now()}`;
  await page.goto("/?view=chain-studio");

  await expect(page.getByRole("heading", { name: "予測Taskを固定したChainとして公開する" })).toBeVisible();
  const stageSelects = page.locator(".chain-studio-stages select");
  await expect(stageSelects).toHaveCount(2);
  await stageSelects.nth(0).selectOption("welding-consumable-stage-b-v1");
  await stageSelects.nth(1).selectOption("welding-stage-c-properties-v1");
  await page.locator('input').nth(0).fill(chainId);
  await page.locator('input').nth(1).fill("scalar Chain UI smoke");

  const switchableRails = page.locator(".chain-studio-binding-rail", { hasText: "2候補" });
  await expect(switchableRails).toHaveCount(16);
  for (const rail of await switchableRails.all()) await rail.click();
  const selectedSources = await page.locator(".chain-studio-table-wrap select").evaluateAll(
    (selects) => selects.map((select) => (select as HTMLSelectElement).value),
  );
  expect(selectedSources.filter((value) => value.startsWith("stage:S1:"))).toHaveLength(16);
  await expect(page.locator(".chain-studio-summary div").nth(1)).toContainText("candidateの既定namespace以外は拒否します");

  await page.getByRole("button", { name: "draftを検証" }).click();
  await expect(page.locator(".chain-studio-success")).toContainText("Task contract、binding、unit／basis、Package／Dataset固定を確認しました。");
  await page.getByRole("button", { name: "immutable Revisionを公開" }).click();
  await expect(page.locator(".chain-studio-success")).toContainText("r1 として公開しました");
  await expectNoBlockingAxeViolations(page);

  const templatesResponse = await page.request.get(`${apiBaseUrl}/api/chains`);
  expect(templatesResponse.status()).toBe(200);
  const template = (await templatesResponse.json() as Array<{
    definition: { chain_id: string };
    revisions: Array<{ revision_digest: string }>;
  }>).find((item) => item.definition.chain_id === chainId)!;
  const projectResponse = await page.request.post(`${apiBaseUrl}/api/projects`, {
    data: {
      name: "scalar Chain Studio smoke",
      scientific_identity: {
        identity_kind: "chain",
        chain_revision_id: `${chainId}:r1`,
        chain_revision_digest: template.revisions[0].revision_digest,
      },
    },
  });
  expect(projectResponse.status(), await projectResponse.text()).toBe(201);
  const project = await projectResponse.json() as { id: string };

  const domainRequests: string[] = [];
  page.on("request", (request) => {
    if (/candidate-contract|analysis-variants|distribution-runs/.test(request.url())) {
      domainRequests.push(request.url());
    }
  });
  await page.goto(`/?view=candidates&project=${project.id}`);
  await page.getByRole("button", { name: "固定契約から基準候補を作成" }).click();
  await expect(page.locator(".chain-stage-node.latest")).toHaveCount(2);
  await expect(page.locator('[data-chain-evidence-renderer="generic/v1"]')).toBeVisible();

  await page.route("**/executions", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 500));
    await route.continue();
  });
  const downstream = page.locator(
    '[data-chain-external-path="candidate.process.test_temperature_c"] input',
  );
  const changedValue = Number(await downstream.inputValue()) + 1;
  const saved = page.waitForResponse((response) => (
    response.request().method() === "PUT"
    && response.url().includes("/chain/candidates/")
  ));
  await downstream.fill(String(changedValue));
  await expect(page.locator(".chain-stage-node").filter({ hasText: "S1" })).toHaveClass(/latest/);
  await expect(page.locator(".chain-stage-node").filter({ hasText: "S2" })).toHaveClass(/stale/);
  expect((await saved).status()).toBe(200);
  await expect(page.locator(".chain-stage-node.latest")).toHaveCount(2);

  await page.getByRole("button", { name: "全Stageを固定" }).click();
  await expect(page.locator(".chain-snapshot-evidence")).toBeVisible();
  expect(domainRequests).toEqual([]);
});
