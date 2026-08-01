import { expect, test } from "@playwright/test";

import { expectNoBlockingAxeViolations } from "./axe";
import { apiBaseUrl } from "./helpers";

async function createExecutedChain(page: import("@playwright/test").Page) {
  const chainsResponse = await page.request.get(`${apiBaseUrl}/api/chains`);
  expect(chainsResponse.status()).toBe(200);
  const chains = await chainsResponse.json() as Array<{
    definition: { chain_id: string };
    revisions: Array<{ revision: number; revision_digest: string }>;
  }>;
  const chain = chains.find((item) => item.revisions.length > 0);
  expect(chain, "an existing Chain with a Revision is required").toBeTruthy();
  const revision = chain!.revisions[0];
  const projectResponse = await page.request.post(`${apiBaseUrl}/api/projects`, {
    data: {
      name: `Chain graph ${Date.now()}`,
      scientific_identity: {
        identity_kind: "chain",
        chain_revision_id: `${chain!.definition.chain_id}:r${revision.revision}`,
        chain_revision_digest: revision.revision_digest,
      },
    },
  });
  expect(projectResponse.status(), await projectResponse.text()).toBe(201);
  const project = await projectResponse.json() as { id: string };
  const contractResponse = await page.request.get(`${apiBaseUrl}/api/projects/${project.id}/chain/candidate-contract`);
  expect(contractResponse.status(), await contractResponse.text()).toBe(200);
  const contract = await contractResponse.json() as { starter_candidate: object };
  const candidateResponse = await page.request.post(`${apiBaseUrl}/api/projects/${project.id}/chain/candidates`, {
    data: contract.starter_candidate,
  });
  expect(candidateResponse.status(), await candidateResponse.text()).toBe(201);
  const candidate = await candidateResponse.json() as { id: string; revision: number };
  const executionResponse = await page.request.post(
    `${apiBaseUrl}/api/projects/${project.id}/chain/candidates/${candidate.id}/executions`,
    { data: { candidate_revision: candidate.revision, request_id: `graph-${Date.now()}`, debounce_ms: 0 } },
  );
  expect(executionResponse.status(), await executionResponse.text()).toBe(200);
  return { project, candidate };
}

test("Chain graph is read-only, keyboard reachable, and has a same-meaning binding table", async ({ page }) => {
  const { project, candidate } = await createExecutedChain(page);
  await page.goto(`/?view=chain-graph&project=${project.id}&candidate=${candidate.id}`);

  await expect(page.locator("#chain-graph-heading")).toBeVisible();
  await expect(page.getByText("binding一覧", { exact: true })).toBeVisible();
  await expect(page.locator(".chain-graph-table-wrap tbody tr")).not.toHaveCount(0);
  await expect(page.locator(".chain-graph-node.latest")).not.toHaveCount(0);
  await expect(page.getByText("固定参照", { exact: true }).first()).toBeVisible();

  const firstExternalPort = page.locator(".chain-graph-external-port").first();
  await firstExternalPort.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Binding inspector" })).toBeVisible();
  await expect(page.getByText("source unit", { exact: true })).toBeVisible();
  await expect(page.getByText("target unit", { exact: true })).toBeVisible();

  const firstNode = page.locator(".chain-graph-node-button").first();
  await firstNode.focus();
  await page.keyboard.press("Space");
  await expect(page.getByRole("heading", { name: /Stage inspector/ })).toBeVisible();
  await expectNoBlockingAxeViolations(page);
});
