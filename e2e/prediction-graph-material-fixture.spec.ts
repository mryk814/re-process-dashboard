import { expect, test } from "@playwright/test";

import { apiBaseUrl } from "./helpers";

test("material Graph fixtures expose split Packages and synthetic evidence", async ({ page }) => {
  await page.goto("/?view=chain-studio");

  const catalog = page.getByRole("group", { name: "Node一覧" }).locator("select");
  await expect(catalog.locator("option", { hasText: "Graph比較用: 引張強さ" })).toHaveCount(1);
  await expect(catalog.locator("option", { hasText: "Graph比較用: 吸収エネルギー" })).toHaveCount(1);
  await expect(catalog.locator("option", { hasText: "Graph比較用: 腐食速度" })).toHaveCount(1);
  await expect(catalog.locator("option", { hasText: "Graph比較用: 溶着効率proxy" })).toHaveCount(1);

  const response = await page.request.get(`${apiBaseUrl}/api/chains`);
  expect(response.status(), await response.text()).toBe(200);
  const templates = await response.json() as Array<{
    definition: {
      graph_id?: string;
      chain_id?: string;
      stages?: Array<{ stage_id: string; contract_id: string }>;
      decision_outputs?: Array<{
        output_id: string;
        group: string;
        evidence?: {
          evidence_kind: string;
          unit_or_scale: string;
          production_use: string;
          causal_claim: string;
        } | null;
      }>;
    };
    revisions: Array<{
      revision_digest: string;
      graph_id: string;
      revision: number;
    }>;
    is_default: boolean;
    default_revision_id: string | null;
    latest_revision_id: string | null;
  }>;
  const multiTemplate = templates.find(({ definition, is_default }) => (
    definition.graph_id === "welding-material-multi-output-demo-v1"
    && is_default
  ));
  const splitTemplate = templates.find(({ definition, is_default }) => (
    definition.graph_id === "welding-material-split-output-demo-v1"
    && is_default
  ));
  const multi = multiTemplate?.definition;
  const split = splitTemplate?.definition;

  expect(multi?.stages).toEqual(expect.arrayContaining([
    expect.objectContaining({ stage_id: "C", contract_id: "welding-stage-c-properties-v1" }),
  ]));
  expect(split?.stages).toEqual(expect.arrayContaining([
    expect.objectContaining({ stage_id: "T", contract_id: "welding-graph-tensile-ts-v1" }),
    expect.objectContaining({ stage_id: "U", contract_id: "welding-graph-toughness-v1" }),
    expect.objectContaining({ stage_id: "R", contract_id: "welding-graph-corrosion-v1" }),
  ]));
  expect(split?.decision_outputs?.map(({ output_id }) => output_id).sort()).toEqual(
    multi?.decision_outputs?.map(({ output_id }) => output_id).sort(),
  );
  const workability = split?.decision_outputs?.find(({ output_id }) => (
    output_id === "deposition-efficiency"
  ));
  expect(workability).toEqual(expect.objectContaining({
    group: "processability",
    evidence: expect.objectContaining({
      evidence_kind: "synthetic_demonstration",
      unit_or_scale: "%",
      production_use: "prohibited",
      causal_claim: "none",
    }),
  }));

  expect(splitTemplate?.default_revision_id).toBe(
    "welding-material-split-output-demo-v1:r2",
  );
  expect(splitTemplate?.latest_revision_id).toBe(
    splitTemplate?.default_revision_id,
  );
  const revision = splitTemplate!.revisions.find(
    ({ graph_id, revision }) => `${graph_id}:r${revision}` === splitTemplate!.default_revision_id,
  )!;
  const projectResponse = await page.request.post(`${apiBaseUrl}/api/prediction-graphs/projects`, {
    data: {
      project: {
        name: "Split material fixture E2E",
        purpose: "Decision Output evidence",
        description: "",
        notes: "",
        task_id: "",
        task_contract_digest: "",
        model_package_manifest_digest: "",
        response_curve_points: 17,
        continuation_reason: "",
        decision_candidate_id: "",
        decision_snapshot_id: "",
        decision_note: "",
      },
      graph_revision_id: `${revision.graph_id}:r${revision.revision}`,
      graph_revision_digest: revision.revision_digest,
      project_binding_revision: 1,
      project_binding_values: {},
    },
  });
  expect(projectResponse.status(), await projectResponse.text()).toBe(201);
  const project = await projectResponse.json() as { id: string };
  const legacyTemplate = templates.find(({ definition }) => (
    definition.chain_id === "welding-consumable-a-b-c-v1"
  ));
  const legacyRevision = legacyTemplate!.revisions[0];
  const legacyProjectResponse = await page.request.post(`${apiBaseUrl}/api/projects`, {
    data: {
      name: "Prediction Graph fixture starter",
      scientific_identity: {
        identity_kind: "chain",
        chain_revision_id: `${legacyTemplate!.definition.chain_id}:r${legacyRevision.revision}`,
        chain_revision_digest: legacyRevision.revision_digest,
      },
    },
  });
  expect(legacyProjectResponse.status(), await legacyProjectResponse.text()).toBe(201);
  const legacyProject = await legacyProjectResponse.json() as { id: string };
  const contractResponse = await page.request.get(
    `${apiBaseUrl}/api/projects/${legacyProject.id}/chain/candidate-contract`,
  );
  expect(contractResponse.status(), await contractResponse.text()).toBe(200);
  const starter = (await contractResponse.json() as {
    starter_candidate: {
      inputs: {
        process: Record<string, number>;
        categorical: Record<string, string>;
      };
      [key: string]: unknown;
    };
  }).starter_candidate;
  Object.assign(starter.inputs.process, {
    heat_input_kj_per_mm: 1.43,
    voltage_v: 28.36,
    gas_flow_l_per_min: 25.4,
    wire_feed_speed_m_per_min: 7.5,
    preheat_temp_c: 80,
    test_temperature_c: -20,
  });
  Object.assign(starter.inputs.categorical, {
    shielding_gas: "100%CO2",
    welding_position: "下向",
    test_solution: "5%H2SO4",
  });
  const candidateResponse = await page.request.post(
    `${apiBaseUrl}/api/prediction-graphs/projects/${project.id}/candidates`,
    { data: starter },
  );
  expect(candidateResponse.status(), await candidateResponse.text()).toBe(201);
  const candidate = await candidateResponse.json() as { id: string; revision: number };
  const executionResponse = await page.request.post(
    `${apiBaseUrl}/api/prediction-graphs/projects/${project.id}/candidates/${candidate.id}/executions`,
    { data: { candidate_revision: candidate.revision, request_id: "fixture-ui", debounce_ms: 0 } },
  );
  expect(executionResponse.status(), await executionResponse.text()).toBe(200);

  const executionRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith(`/candidates/${candidate.id}/execution`)) {
      executionRequests.push(request.url());
    }
  });
  await page.goto(`/?view=chain-graph&project=${project.id}&candidate=${candidate.id}`);
  await expect(page.getByRole("heading", { name: "Decision Output summary" })).toBeVisible();
  await expect(page.locator(".chain-graph-output")).toHaveCount(5);
  await expect(page.getByText("synthetic demonstration", { exact: true })).toHaveCount(5);
  await expect(page.getByText("production利用: 不可", { exact: true })).toHaveCount(5);
  await expect(page.locator(".chain-graph-node.latest")).toHaveCount(6);
  expect(executionRequests).toEqual([
    expect.stringContaining("/api/prediction-graphs/projects/"),
  ]);
  await expect(page.locator(".chain-graph-output").filter({ hasText: "溶着効率proxy" })).toContainText("processability");

  await page.goto(`/?view=candidates&project=${project.id}&candidate=${candidate.id}`);
  await expect(page.getByRole("heading", { name: "候補とDecision Outputを一つの面で比較" })).toBeVisible();
  await expect(page.locator(".graph-decision-workspace textarea")).toHaveCount(0);
  await expect(page.getByRole("group", { name: "設計変数" })).toBeVisible();
  await expect(page.getByRole("group", { name: /評価context/ })).toBeVisible();
  await expect(page.getByText(/primary_objective/).first()).toBeVisible();
  await expect(page.getByText(/synthetic_demonstration/).first()).toBeVisible();
  await expect(page.getByRole("row", { name: /引張強さ/ })).toContainText(/\d/);
  await page.getByLabel(/電圧/).fill("28.37");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "Graph inspector" }).click();
  await expect(page.getByRole("heading", { name: "候補とDecision Outputを一つの面で比較" })).toBeVisible();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("候補条件を保存しました。実行はまだ行っていません。")).toBeVisible();
  await page.getByRole("button", { name: "実行", exact: true }).click();
  await expect(page.getByText("Prediction Graphを実行しました。")).toBeVisible();
  await page.getByRole("button", { name: "Snapshotを固定" }).click();
  await expect(page.getByText("現在のPrediction Graph結果をSnapshotとして固定しました。")).toBeVisible();
  await page.getByLabel("Output").selectOption("tensile-strength");
  await page.getByLabel("実測値").fill("720");
  await expect(page.getByLabel("単位")).toHaveValue("MPa");
  await page.getByLabel("測定ID").fill("fixture-actual-001");
  await page.getByRole("button", { name: "Actualを記録" }).click();
  await expect(page.getByText("実測を固定Prediction Snapshotへ記録しました。")).toBeVisible();
  await expect(page.getByText(/tensile-strength: 720 MPa/)).toBeVisible();

  const candidatesResponse = await page.request.get(`${apiBaseUrl}/api/projects/${project.id}/candidates`);
  const currentCandidate = (await candidatesResponse.json() as Array<Record<string, any>>)
    .find((item) => item.id === candidate.id)!;
  const externalUpdate = await page.request.put(
    `${apiBaseUrl}/api/prediction-graphs/projects/${project.id}/candidates/${candidate.id}`,
    { data: { ...currentCandidate, expected_revision: currentCandidate.revision } },
  );
  expect(externalUpdate.status(), await externalUpdate.text()).toBe(200);
  await page.getByLabel(/電圧/).fill("28.38");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("button", { name: "最新revisionを再読込" })).toBeVisible();
  await page.getByRole("button", { name: "最新revisionを再読込" }).click();
  await expect(page.getByText(/過去のcandidate revision/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Actualを記録" })).toBeDisabled();
});
