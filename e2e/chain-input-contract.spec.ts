import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { apiBaseUrl } from "./helpers";

type ChainInputDefinition = {
  external_path: string;
  candidate_path: string;
  kind: "number" | "categorical" | "sparse_blend";
  label: string;
  unit: string | null;
  editable: boolean;
  allowed_range: { min: number; max: number } | null;
  choices: string[];
  first_affected_stage_id: string;
};

async function createChainCandidate(request: APIRequestContext) {
  const chainsResponse = await request.get(`${apiBaseUrl}/api/chains`);
  expect(chainsResponse.status()).toBe(200);
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
      name: `Chain入力契約 ${Date.now()}`,
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
    external_inputs: ChainInputDefinition[];
    starter_candidate: object;
  };
  const candidateResponse = await request.post(
    `${apiBaseUrl}/api/projects/${project.id}/chain/candidates`,
    { data: contract.starter_candidate },
  );
  expect(candidateResponse.status(), await candidateResponse.text()).toBe(201);
  const candidate = await candidateResponse.json() as {
    id: string;
    revision: number;
  };
  const executionResponse = await request.post(
    `${apiBaseUrl}/api/projects/${project.id}/chain/candidates/${candidate.id}/executions`,
    {
      data: {
        candidate_revision: candidate.revision,
        request_id: `e2e-chain-input-${Date.now()}`,
        debounce_ms: 0,
      },
    },
  );
  expect(executionResponse.status(), await executionResponse.text()).toBe(200);
  return { project, candidate, contract };
}

function stage(page: Page, stageId: "A" | "B" | "C") {
  return page.locator(".chain-stage-node").filter({
    has: page.locator(":scope > b", { hasText: stageId }),
  });
}

test("Chain candidate editor exposes and persists every external input from its contract", async ({
  page,
}) => {
  const { project, candidate, contract } = await createChainCandidate(page.request);
  expect(contract.external_inputs).toHaveLength(9);
  expect(
    contract.external_inputs.some(
      (definition) => definition.candidate_path === "categorical.test_solution",
    ),
  ).toBe(true);

  await page.route("**/executions", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 650));
    await route.continue();
  });
  await page.goto(
    `/?view=candidates&project=${project.id}&candidate=${candidate.id}`,
  );
  await expect(page.locator(".chain-stage-node.latest")).toHaveCount(3);

  const surfaces = page.locator("[data-chain-external-path]");
  await expect(surfaces).toHaveCount(contract.external_inputs.length);
  const renderedPaths = await surfaces.evaluateAll((elements) => elements.map(
    (element) => element.getAttribute("data-chain-external-path"),
  ));
  expect([...renderedPaths].sort()).toEqual(
    contract.external_inputs.map((definition) => definition.external_path).sort(),
  );

  for (const definition of contract.external_inputs) {
    const surface = page.locator(
      `[data-chain-external-path="${definition.external_path}"]`,
    );
    await expect(surface).toContainText(definition.label);
    if (definition.kind === "number") {
      const input = surface.locator('input[type="number"]');
      await expect(input).toHaveAttribute("min", String(definition.allowed_range!.min));
      await expect(input).toHaveAttribute("max", String(definition.allowed_range!.max));
      if (definition.editable) await expect(input).toBeEnabled();
      else await expect(input).toBeDisabled();
      if (definition.unit) await expect(surface).toContainText(definition.unit);
    } else if (definition.kind === "categorical") {
      const select = surface.locator("select");
      await expect(select.locator("option")).toHaveCount(definition.choices.length);
      if (definition.editable) await expect(select).toBeEnabled();
      else await expect(select).toBeDisabled();
    }
  }

  const voltage = contract.external_inputs.find(
    (definition) => definition.candidate_path === "process.voltage_v",
  )!;
  const voltageValue = (
    voltage.allowed_range!.min
    + (voltage.allowed_range!.max - voltage.allowed_range!.min) * 0.35
  );
  const voltageSurface = page.locator(
    `[data-chain-external-path="${voltage.external_path}"]`,
  );
  const voltageSaved = page.waitForResponse((response) => (
    response.request().method() === "PUT"
    && response.url().includes(`/chain/candidates/${candidate.id}`)
  ));
  const voltageExecuted = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && response.url().endsWith(`/chain/candidates/${candidate.id}/executions`)
  ));
  await voltageSurface.locator('input[type="number"]').fill(String(voltageValue));
  await expect(stage(page, "A")).toHaveClass(/latest/);
  await expect(stage(page, "B")).toHaveClass(/stale/);
  await expect(stage(page, "C")).toHaveClass(/stale/);
  const voltageSaveResponse = await voltageSaved;
  expect(voltageSaveResponse.status(), await voltageSaveResponse.text()).toBe(200);
  const voltageCandidate = await voltageSaveResponse.json() as {
    inputs: { process: Record<string, number> };
  };
  expect(voltageCandidate.inputs.process.voltage_v).toBeCloseTo(voltageValue);
  expect((await voltageExecuted).status()).toBe(200);
  await expect(page.locator(".chain-stage-node.latest")).toHaveCount(3);

  const shielding = contract.external_inputs.find(
    (definition) => definition.candidate_path === "categorical.shielding_gas",
  )!;
  const shieldingSurface = page.locator(
    `[data-chain-external-path="${shielding.external_path}"]`,
  );
  const currentShielding = await shieldingSurface.locator("select").inputValue();
  const nextShielding = shielding.choices.find((choice) => choice !== currentShielding)!;
  const shieldingSaved = page.waitForResponse((response) => (
    response.request().method() === "PUT"
    && response.url().includes(`/chain/candidates/${candidate.id}`)
  ));
  const shieldingExecuted = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && response.url().endsWith(`/chain/candidates/${candidate.id}/executions`)
  ));
  await shieldingSurface.locator("select").selectOption(nextShielding);
  await expect(stage(page, "A")).toHaveClass(/latest/);
  await expect(stage(page, "B")).toHaveClass(/stale/);
  await expect(stage(page, "C")).toHaveClass(/stale/);
  const shieldingCandidate = await (await shieldingSaved).json() as {
    inputs: { categorical: Record<string, string> };
  };
  expect(shieldingCandidate.inputs.categorical.shielding_gas).toBe(nextShielding);
  expect((await shieldingExecuted).status()).toBe(200);
  await expect(page.locator(".chain-stage-node.latest")).toHaveCount(3);

  const solution = contract.external_inputs.find(
    (definition) => definition.candidate_path === "categorical.test_solution",
  )!;
  const solutionSurface = page.locator(
    `[data-chain-external-path="${solution.external_path}"]`,
  );
  const currentSolution = await solutionSurface.locator("select").inputValue();
  const nextSolution = solution.choices.find((choice) => choice !== currentSolution)!;
  const solutionSaved = page.waitForResponse((response) => (
    response.request().method() === "PUT"
    && response.url().includes(`/chain/candidates/${candidate.id}`)
  ));
  const solutionExecuted = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && response.url().endsWith(`/chain/candidates/${candidate.id}/executions`)
  ));
  await solutionSurface.locator("select").selectOption(nextSolution);
  await expect(stage(page, "A")).toHaveClass(/latest/);
  await expect(stage(page, "B")).toHaveClass(/latest/);
  await expect(stage(page, "C")).toHaveClass(/stale/);
  const solutionCandidate = await (await solutionSaved).json() as {
    inputs: { categorical: Record<string, string> };
  };
  expect(solutionCandidate.inputs.categorical.test_solution).toBe(nextSolution);
  expect((await solutionExecuted).status()).toBe(200);
  await expect(page.locator(".chain-stage-node.latest")).toHaveCount(3);
});
