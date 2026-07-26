import { expect, test } from "@playwright/test";
import { apiBaseUrl } from "./helpers";

test("source refresh stays separate from approval, training and activation", async ({ page, request }) => {
  const optionsBefore = await (await request.get(`${apiBaseUrl}/api/project-creation-options`)).json();
  const profile = optionsBefore.datasets[0].profile_revision;
  const connectorResponse = await request.post(`${apiBaseUrl}/api/data-lifecycle/connectors`, {
    data: {
      schema_version: "source-connector/v1",
      name: "E2E共有object",
      connector_type: "object_storage_json_v1",
      source_locator: "s3://e2e-bucket/material/latest.json",
      selection: {
        schema_version: "object-selection/v1",
        format: "json_array",
        primary_key: "id",
        included_fields: [],
      },
      trigger_policy: "manual_only",
      schedule: null,
    },
  });
  expect(connectorResponse.ok()).toBeTruthy();
  const connector = await connectorResponse.json();
  const fetchResponse = await request.post(`${apiBaseUrl}/api/data-lifecycle/connectors/${connector.id}/fetch`, {
    data: {
      schema_version: "source-fetch-request/v1",
      trigger_kind: "manual",
      object_content: JSON.stringify([
        { id: "A-01", value: "12.4", target: "98.1" },
        { id: "CHECK-02", value: "", target: "97.5" },
        { id: "A-03", value: "15.1" },
      ]),
      object_version: "e2e-etag-1",
      retry_of: null,
    },
    headers: { "X-Source-Credential": "E2E-SECRET-MUST-DISAPPEAR" },
  });
  expect(fetchResponse.ok()).toBeTruthy();
  expect(await fetchResponse.text()).not.toContain("E2E-SECRET-MUST-DISAPPEAR");
  const recipeResponse = await request.post(`${apiBaseUrl}/api/data-lifecycle/recipes`, {
    data: {
      schema_version: "curation-recipe/v1",
      recipe_id: "e2e-json-quality",
      version: 1,
      name: "E2E JSON品質判定",
      steps: [
        { kind: "trim_strings_v1", fields: ["id", "value", "target"] },
        { kind: "coerce_number_v1", fields: ["value", "target"] },
        { kind: "required_fields_v1", fields: ["id", "value"] },
        { kind: "target_eligibility_v1", fields: ["target"] },
      ],
    },
  });
  expect(recipeResponse.ok()).toBeTruthy();

  await page.goto("/?view=data-library");
  const section = page.locator(".source-lifecycle-section");
  await expect(section.getByRole("heading", { name: "Source更新" })).toBeVisible();
  await expect(section.getByText("Source refresh ≠ retraining ≠ activation")).toBeVisible();
  await expect(section.getByRole("button", { name: /E2E共有object/ })).toBeVisible();
  await expect(section.locator(".source-stage-rail")).toContainText("1 revision");
  await expect(section.locator(".source-stage-rail")).toContainText("未実行");

  await section.getByRole("button", { name: "品質判定を実行" }).click();
  await expect(section.locator(".source-quality-summary")).toContainText("隔離");
  await expect(section.locator(".source-quality-summary")).toContainText("CHECK-02");
  await expect(section.locator(".source-quality-summary")).toContainText("missing_required");
  await expect(section.getByRole("button", { name: "隔離行を除いて承認" })).toBeVisible();

  const optionsUnapproved = await (await request.get(`${apiBaseUrl}/api/project-creation-options`)).json();
  expect(optionsUnapproved.datasets).toEqual(optionsBefore.datasets);
  expect(optionsUnapproved.model_packages).toEqual(optionsBefore.model_packages);

  await section.getByRole("button", { name: "隔離行を除いて承認" }).click();
  await expect(section.getByRole("button", { name: "Training Snapshotを作成" })).toBeVisible();
  await expect(section).toContainText("再学習・active化は行っていません");

  await section.getByRole("button", { name: "Training Snapshotを作成" }).click();
  await expect(section.getByText("Training Snapshot ready")).toBeVisible();
  await expect(section).toContainText("1行");
  await expect(section).toContainText("再学習・Package検証・active化は別操作です");

  const optionsAfter = await (await request.get(`${apiBaseUrl}/api/project-creation-options`)).json();
  expect(optionsAfter.model_packages).toEqual(optionsBefore.model_packages);
});
