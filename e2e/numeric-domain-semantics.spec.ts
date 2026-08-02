import { expect, test } from "@playwright/test";

import { apiBaseUrl, createProjectWithCandidate, openCandidateInputs, starterCandidate } from "./helpers";

test("integer and log Task domains are enforced by API and shown by the candidate editor", async ({ page }) => {
  const project = await createProjectWithCandidate(
    page.request,
    "battery-degradation-v1",
    `numeric domain ${Date.now()}`,
    "数値domain候補",
  );
  const definitionResponse = await page.request.get(
    `${apiBaseUrl}/api/projects/${project.id}/task-definition`,
  );
  expect(definitionResponse.status()).toBe(200);
  const definition = await definitionResponse.json() as {
    task_definition: { input_groups: Array<{ fields: Array<{ path: string; numeric_domain_kind: string; search_scale: string }> }> };
  };
  const fields = definition.task_definition.input_groups.flatMap((group) => group.fields);
  expect(fields.find((field) => field.path === "process.cycle_index")).toMatchObject({
    numeric_domain_kind: "integer",
  });
  expect(fields.find((field) => field.path === "process.discharge_rate_c")).toMatchObject({
    search_scale: "log",
  });

  const starter = await starterCandidate(page.request, "battery-degradation-v1") as {
    inputs: { process: Record<string, number> };
  };
  const invalid = await page.request.post(`${apiBaseUrl}/api/projects/${project.id}/candidates`, {
    data: {
      ...starter,
      name: "小数cycleは拒否",
      inputs: {
        ...starter.inputs,
        process: { ...starter.inputs.process, cycle_index: 10.5 },
      },
    },
  });
  expect(invalid.status()).toBe(422);
  await expect(invalid.text()).resolves.toContain("integer");

  const candidatesResponse = await page.request.get(`${apiBaseUrl}/api/projects/${project.id}/candidates`);
  expect(candidatesResponse.status()).toBe(200);
  const [candidate] = await candidatesResponse.json() as Array<{ id: string; revision: number }>;
  const curveParameters = new URLSearchParams({
    expected_revision: String(candidate.revision),
    target: "capacity_percent",
    variable: "process.cycle_index",
    points: "15",
  });
  const curve = await page.request.get(
    `${apiBaseUrl}/api/projects/${project.id}/candidates/${candidate.id}/response-curve?${curveParameters}`,
  );
  expect(curve.status(), await curve.text()).toBe(200);
  const curveValues = (await curve.json() as { points: Array<{ x: number }> }).points.map((point) => point.x);
  expect(curveValues).toEqual([...new Set(curveValues)]);
  expect(curveValues.every((value) => Number.isInteger(value))).toBeTruthy();

  const curveFamily = await page.request.get(
    `${apiBaseUrl}/api/projects/${project.id}/candidates/${candidate.id}/curve-family?expected_revision=${candidate.revision}&target=capacity_percent&vary=process.discharge_rate_c&levels=3&points=15`,
  );
  expect(curveFamily.status(), await curveFamily.text()).toBe(200);
  const familyBody = await curveFamily.json() as {
    series: Array<{ level: number; points: Array<{ x: number }> }>;
  };
  const familyValues = familyBody.series.flatMap((series) => series.points.map((point) => point.x));
  expect(familyValues.every((value) => Number.isInteger(value))).toBeTruthy();
  expect(familyBody.series.map((series) => series.level)).toEqual([
    0.5,
    0.70711,
    1,
  ]);

  const contourParameters = new URLSearchParams({
    expected_revision: String(candidate.revision),
    target: "capacity_percent",
    x_variable: "process.cycle_index",
    y_variable: "process.discharge_rate_c",
    points: "11",
  });
  const contour = await page.request.get(
    `${apiBaseUrl}/api/projects/${project.id}/candidates/${candidate.id}/response-contour?${contourParameters}`,
  );
  expect(contour.status(), await contour.text()).toBe(200);
  const contourBody = await contour.json() as { x_values: number[]; y_values: number[] };
  expect(contourBody.x_values.every((value) => Number.isInteger(value))).toBeTruthy();
  expect(contourBody.y_values.every((value) => value > 0)).toBeTruthy();

  await page.goto(`/?view=candidates&project=${project.id}`);
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await openCandidateInputs(page);
  await expect(page.getByRole("spinbutton", { name: "数値domain候補 サイクル数", exact: true })).toHaveAttribute("step", "1");
  await expect(page.getByRole("slider", { name: "数値domain候補 放電レート", exact: true })).toHaveAttribute("step", "0.1");
  await expect(page.getByText("対数スケール")).toBeVisible();
});
