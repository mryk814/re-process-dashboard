import { expect, test } from "@playwright/test";

import { apiBaseUrl } from "./helpers";

test("Chain outputs use the pinned labels, units, decimals, and uncertainty wording", async ({ page }) => {
  const projectsResponse = await page.request.get(`${apiBaseUrl}/api/projects`);
  expect(projectsResponse.status()).toBe(200);
  const projects = await projectsResponse.json() as Array<{
    id: string;
    scientific_identity: { identity_kind: string };
  }>;
  let project = projects.find((item) => item.scientific_identity.identity_kind === "chain");
  if (!project) {
    const chainsResponse = await page.request.get(`${apiBaseUrl}/api/chains`);
    expect(chainsResponse.status()).toBe(200);
    const chains = await chainsResponse.json() as Array<{
      definition: { chain_id: string };
      revisions: Array<{ revision: number; revision_digest: string }>;
    }>;
    const chain = chains.find((item) => item.definition.chain_id === "welding-consumable-a-b-c-v1");
    expect(chain, "bundled Chain definition must be available").toBeTruthy();
    const revision = chain!.revisions[0];
    const projectResponse = await page.request.post(`${apiBaseUrl}/api/projects`, {
      data: {
        name: `Chain出力表示 ${Date.now()}`,
        scientific_identity: {
          identity_kind: "chain",
          chain_revision_id: `${chain!.definition.chain_id}:r${revision.revision}`,
          chain_revision_digest: revision.revision_digest,
        },
      },
    });
    expect(projectResponse.status(), await projectResponse.text()).toBe(201);
    project = await projectResponse.json() as {
      id: string;
      scientific_identity: { identity_kind: string };
    };
  }

  const candidatesResponse = await page.request.get(
    `${apiBaseUrl}/api/projects/${project!.id}/chain/candidates`,
  );
  expect(candidatesResponse.status()).toBe(200);
  let candidates = await candidatesResponse.json() as Array<{
    id: string;
    revision: number;
  }>;
  if (!candidates.length) {
    const contractResponse = await page.request.get(
      `${apiBaseUrl}/api/projects/${project!.id}/chain/candidate-contract`,
    );
    expect(contractResponse.status()).toBe(200);
    const contract = await contractResponse.json() as { starter_candidate: object };
    const createdResponse = await page.request.post(
      `${apiBaseUrl}/api/projects/${project!.id}/chain/candidates`,
      { data: contract.starter_candidate },
    );
    expect(createdResponse.status()).toBe(201);
    candidates = [await createdResponse.json() as { id: string; revision: number }];
  }
  const candidate = candidates[0];
  const executionResponse = await page.request.post(
    `${apiBaseUrl}/api/projects/${project!.id}/chain/candidates/${candidate.id}/executions`,
    {
      data: {
        candidate_revision: candidate.revision,
        request_id: `e2e-chain-output-${Date.now()}`,
        debounce_ms: 0,
      },
    },
  );
  expect(executionResponse.status(), await executionResponse.text()).toBe(200);
  const snapshotResponse = await page.request.post(
    `${apiBaseUrl}/api/projects/${project!.id}/chain/candidates/${candidate.id}/snapshots`,
    { data: { candidate_revision: candidate.revision, debounce_ms: 0 } },
  );
  expect(snapshotResponse.status(), await snapshotResponse.text()).toBe(201);

  await page.goto(`/?view=candidates&project=${project!.id}&candidate=${candidate.id}`);
  const stageB = page.locator(".chain-result-card").filter({ has: page.getByRole("heading", { name: "溶着金属成分" }) });
  const stageC = page.locator(".chain-result-card").filter({ has: page.getByRole("heading", { name: "特性", exact: true }) });
  const tensileRow = stageC.getByRole("row").filter({ has: page.getByRole("rowheader", { name: "引張強さ" }) });

  await expect(tensileRow).toContainText(/[\d,]+\.\d MPa/);
  await expect(tensileRow).toContainText(/モデル由来 ±|区間なし/);
  await expect(stageC.getByRole("rowheader", { name: "吸収エネルギー" })).toBeVisible();
  await expect(stageC.getByText("CHARPY_ENERGY", { exact: true })).toHaveCount(0);
  await expect(stageB.getByRole("rowheader", { name: "溶着金属 C", exact: true })).toBeVisible();
  await expect(stageB).toContainText("mass% deposited metal");

  const snapshotEvidence = page.locator(".chain-snapshot-evidence");
  await snapshotEvidence.locator(":scope > summary").click();
  await snapshotEvidence.getByText("Stage C · 最新", { exact: true }).click();
  await expect(snapshotEvidence.getByRole("rowheader", { name: "引張強さ" })).toBeVisible();
  await expect(snapshotEvidence).toContainText("MPa");
  await expect(snapshotEvidence.getByText("CHARPY_ENERGY", { exact: true })).toHaveCount(0);
});
