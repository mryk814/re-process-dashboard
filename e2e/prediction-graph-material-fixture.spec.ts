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
  }>;
  const multiTemplate = templates.find(({ definition }) => (
    definition.graph_id === "welding-material-multi-output-demo-v1"
  ));
  const splitTemplate = templates.find(({ definition }) => (
    definition.graph_id === "welding-material-split-output-demo-v1"
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

  const revision = splitTemplate!.revisions[0];
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
  await page.goto(`/?view=chain-graph&project=${project.id}`);
  await expect(page.getByRole("heading", { name: "Decision Output summary" })).toBeVisible();
  await expect(page.locator(".chain-graph-output")).toHaveCount(5);
  await expect(page.getByText("synthetic demonstration · production利用不可")).toHaveCount(5);
  await expect(page.locator(".chain-graph-output").filter({ hasText: "溶着効率proxy" })).toContainText("processability");
});
