import { expect, test } from "@playwright/test";

import { apiBaseUrl } from "./helpers";

test("Chain outputs use the pinned labels, units, decimals, and uncertainty wording", async ({ page }) => {
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
  const projectBody = await projectResponse.text();
  expect(projectResponse.status(), projectBody).toBe(201);
  const project = JSON.parse(projectBody) as { id: string };

  const candidatesResponse = await page.request.get(
    `${apiBaseUrl}/api/projects/${project.id}/chain/candidates`,
  );
  expect(candidatesResponse.status()).toBe(200);
  let candidates = await candidatesResponse.json() as Array<{
    id: string;
    revision: number;
  }>;
  if (!candidates.length) {
    const contractResponse = await page.request.get(
      `${apiBaseUrl}/api/projects/${project.id}/chain/candidate-contract`,
    );
    expect(contractResponse.status()).toBe(200);
    const contract = await contractResponse.json() as { starter_candidate: object };
    const createdResponse = await page.request.post(
      `${apiBaseUrl}/api/projects/${project.id}/chain/candidates`,
      { data: contract.starter_candidate },
    );
    expect(createdResponse.status()).toBe(201);
    candidates = [await createdResponse.json() as { id: string; revision: number }];
  }
  const candidate = candidates[0];
  const executionResponse = await page.request.post(
    `${apiBaseUrl}/api/projects/${project.id}/chain/candidates/${candidate.id}/executions`,
    {
      data: {
        candidate_revision: candidate.revision,
        request_id: `e2e-chain-output-${Date.now()}`,
        debounce_ms: 0,
      },
    },
  );
  const executionBody = await executionResponse.text();
  expect(executionResponse.status(), executionBody).toBe(200);
  const execution = JSON.parse(executionBody) as {
    stages: Array<{
      stage_id: string;
      result: { predictions?: Record<string, { value?: number }> } | null;
    }>;
  };
  const snapshotResponse = await page.request.post(
    `${apiBaseUrl}/api/projects/${project.id}/chain/candidates/${candidate.id}/snapshots`,
    { data: { candidate_revision: candidate.revision, debounce_ms: 0 } },
  );
  const snapshotBody = await snapshotResponse.text();
  expect(snapshotResponse.status(), snapshotBody).toBe(201);
  const snapshot = JSON.parse(snapshotBody) as { snapshot_id: string };
  const stageBPredictions = execution.stages.find((stage) => stage.stage_id === "B")?.result?.predictions ?? {};
  const measuredStageB = Object.fromEntries(
    Object.entries(stageBPredictions).map(([key, prediction]) => [key, prediction.value]),
  );
  expect(Object.values(measuredStageB).every((value) => typeof value === "number" && Number.isFinite(value))).toBe(true);
  const variantResponse = await page.request.post(
    `${apiBaseUrl}/api/projects/${project.id}/chain/candidates/${candidate.id}/analysis-variants`,
    {
      data: {
        candidate_revision: candidate.revision,
        comparison_snapshot_id: snapshot.snapshot_id,
        actual_records: [{ actual_id: "e2e-measured-stage-b", values: measuredStageB }],
      },
    },
  );
  expect(variantResponse.status(), await variantResponse.text()).toBe(201);

  await page.goto(`/?view=candidates&project=${project.id}&candidate=${candidate.id}`);
  const stageB = page.locator(".chain-result-card").filter({ has: page.getByRole("heading", { name: "溶着金属成分" }) });
  const stageC = page.locator(".chain-result-card").filter({ has: page.getByRole("heading", { name: "特性", exact: true }) });
  const tensileRow = stageC.getByRole("row").filter({ has: page.getByRole("rowheader", { name: "引張強さ" }) });
  const carbonRow = stageB.getByRole("row").filter({ has: page.getByRole("rowheader", { name: "溶着金属 C", exact: true }) });

  await expect(tensileRow).toContainText(/[\d,]+\.\d MPa/);
  await expect(tensileRow).toContainText(/標準偏差 ±|区間なし/);
  await expect(tensileRow.locator("td").nth(1)).not.toHaveText("—");
  await expect(tensileRow.locator("td").nth(1)).toContainText("MPa");
  await expect(stageC.getByRole("rowheader", { name: "吸収エネルギー" })).toBeVisible();
  await expect(stageC.getByText("CHARPY_ENERGY", { exact: true })).toHaveCount(0);
  await expect(carbonRow).toBeVisible();
  await expect(carbonRow.locator("td").nth(1)).toContainText("mass% deposited metal");

  const snapshotEvidence = page.locator(".chain-snapshot-evidence");
  await snapshotEvidence.locator(":scope > summary").click();
  const snapshotStageC = snapshotEvidence.locator("details").filter({ has: page.getByText("Stage C · 最新", { exact: true }) });
  await snapshotStageC.getByText("Stage C · 最新", { exact: true }).click();
  await expect(snapshotStageC.getByRole("rowheader", { name: "引張強さ" })).toBeVisible();
  await expect(snapshotStageC).toContainText(/[\d,]+\.\d MPa/);
  await expect(snapshotStageC).not.toContainText("CHARPY_ENERGY");
});
