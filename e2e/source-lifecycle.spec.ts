import { expect, test } from "@playwright/test";
import { apiBaseUrl } from "./helpers";

test("source refresh stays separate from approval, training and activation", async ({ page, request }) => {
  const optionsBefore = await (await request.get(`${apiBaseUrl}/api/project-creation-options`)).json();
  const profile = optionsBefore.datasets[0].profile_revision;

  await page.goto("/?view=data-library");
  await expect(page.getByRole("tab", { name: "閲覧" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".source-lifecycle-section")).toHaveCount(0);
  await page.getByRole("tab", { name: "データ更新" }).click();
  const emptySection = page.locator(".source-lifecycle-section");
  await expect(emptySection.getByRole("heading", { name: "データ更新" })).toBeVisible();
  await expect(emptySection.getByText("データ取得・承認・再学習・有効化は、それぞれ別の操作です")).toBeVisible();
  await emptySection.locator(".source-create").getByText("＋ 接続先").click();
  await expect(emptySection.getByLabel("名前")).toHaveValue("");
  await expect(emptySection.getByLabel("データの場所")).toHaveValue("");
  await expect(emptySection.getByLabel("行識別キー")).toHaveValue("");
  await expect(emptySection).not.toContainText("example-bucket");

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

  await page.reload();
  await page.getByRole("tab", { name: "データ更新" }).click();
  const section = page.locator(".source-lifecycle-section");
  await expect(section.getByRole("heading", { name: "データ更新" })).toBeVisible();
  await expect(section.getByRole("button", { name: /E2E共有object/ })).toBeVisible();
  await expect(section.locator(".source-validation-mode")).not.toHaveAttribute("open", "");
  await section.locator(".source-validation-mode").getByText("検証モード：JSONを直接入力").click();
  await expect(section.getByLabel("元データの版")).toHaveValue("");
  await expect(section.getByLabel("JSONデータ")).toHaveValue("");
  await section.locator(".source-validation-mode").getByText("検証モード：JSONを直接入力").click();
  await expect(section.locator(".source-stage-rail")).toContainText("1版");
  await expect(section.locator(".source-stage-rail")).toContainText("0版");

  await section.getByRole("button", { name: "品質判定を実行" }).click();
  await expect(section.locator(".source-quality-summary")).toContainText("隔離");
  await expect(section.locator(".source-quality-summary")).toContainText("CHECK-02");
  await expect(section.locator(".source-quality-summary")).toContainText("必須項目がありません");
  await expect(section.getByRole("button", { name: "隔離行を除いて承認" })).toBeVisible();
  await expect(section.getByText("このローカルワークスペースの利用者")).toBeVisible();
  await expect(section.getByLabel("Actor")).toHaveCount(0);

  await section.locator(".source-override-panel").getByText("判定を上書きして採用する行を選ぶ").click();
  const check02Override = section.getByRole("checkbox", { name: /CHECK-02/ });
  await check02Override.check();
  const overrideApproval = section.getByRole("button", { name: "上書きを含めて承認" });
  await expect(overrideApproval).toBeDisabled();
  await section.getByLabel("CHECK-02の上書き理由").fill("測定担当者に確認済み");
  await expect(overrideApproval).toBeDisabled();
  await section.getByLabel("承認理由（必須）").fill("既知の測定限界として採用");
  await expect(overrideApproval).toBeEnabled();

  const optionsUnapproved = await (await request.get(`${apiBaseUrl}/api/project-creation-options`)).json();
  expect(optionsUnapproved.datasets).toEqual(optionsBefore.datasets);
  expect(optionsUnapproved.model_packages).toEqual(optionsBefore.model_packages);

  await overrideApproval.click();
  await expect(section.getByRole("button", { name: "学習用スナップショットを作成" })).toBeVisible();
  await expect(section).toContainText("再学習・有効化は行っていません");
  const approvedDetail = await (await request.get(
    `${apiBaseUrl}/api/data-lifecycle/connectors/${connector.id}`,
  )).json();
  const approvedRevision = approvedDetail.canonical_revisions.at(-1);
  expect(approvedRevision.actor).toBe("local-workspace-user");
  expect(approvedRevision.reason).toBe("既知の測定限界として採用");
  expect(approvedRevision.overrides).toEqual([
    { row_key: "CHECK-02", reason: "測定担当者に確認済み" },
  ]);
  expect(approvedRevision.approved_row_keys).toContain("CHECK-02");

  await section.getByRole("button", { name: "学習用スナップショットを作成" }).click();
  await expect(section.getByText("学習用スナップショット作成済み")).toBeVisible();
  await expect(section).toContainText("2行");
  await expect(section).toContainText("再学習・モデル検証・有効化は別の操作です");

  const optionsAfter = await (await request.get(`${apiBaseUrl}/api/project-creation-options`)).json();
  expect(optionsAfter.model_packages).toEqual(optionsBefore.model_packages);

  const secondFetch = await request.post(`${apiBaseUrl}/api/data-lifecycle/connectors/${connector.id}/fetch`, {
    data: {
      schema_version: "source-fetch-request/v1",
      trigger_kind: "manual",
      object_content: JSON.stringify([
        { id: "A-01", value: "12.8", target: "98.4" },
        { id: "A-04", value: "16.2", target: "99.0" },
      ]),
      object_version: "e2e-etag-2",
      retry_of: null,
    },
  });
  expect(secondFetch.ok()).toBeTruthy();
  const secondRaw = (await secondFetch.json()).snapshot;
  const secondCurationResponse = await request.post(
    `${apiBaseUrl}/api/data-lifecycle/raw-snapshots/${secondRaw.id}/curation-runs`,
    {
      data: {
        recipe_resource_id: (await recipeResponse.json()).id,
        profile_revision_id: profile.id,
        profile_digest: profile.profile_digest,
      },
    },
  );
  expect(secondCurationResponse.ok()).toBeTruthy();
  const secondRun = await secondCurationResponse.json();
  const secondApprovalResponse = await request.post(
    `${apiBaseUrl}/api/data-lifecycle/curation-runs/${secondRun.id}/approve`,
    { data: { reason: "定期更新として承認", overrides: [] } },
  );
  expect(secondApprovalResponse.ok()).toBeTruthy();
  const secondApproval = await secondApprovalResponse.json();
  const secondTrainingResponse = await request.post(
    `${apiBaseUrl}/api/data-lifecycle/canonical-dataset-revisions/${secondApproval.id}/training-snapshots`,
    { data: { purpose: "更新版の再評価", selection_policy: null } },
  );
  expect(secondTrainingResponse.ok()).toBeTruthy();

  await page.reload();
  const historySection = page.locator(".source-lifecycle-section");
  await expect(historySection).toBeVisible();
  await expect(historySection.locator(".source-stage-rail li").nth(0)).toContainText("2版");
  await expect(historySection.locator(".source-stage-rail li").nth(1)).toContainText("2版");
  await expect(historySection.locator(".source-stage-rail li").nth(2)).toContainText("2版");
  await expect(historySection.locator(".source-stage-rail li").nth(3)).toContainText("2版");

  await historySection.locator(".source-stage-rail li").nth(2).getByRole("button").click();
  const approvalHistory = historySection.locator(".source-history");
  await expect(approvalHistory.locator(".source-history-list").getByRole("button")).toHaveCount(2);
  await approvalHistory.locator(".source-history-list").getByRole("button").filter({ hasText: "v1" }).click();
  await expect(approvalHistory).toContainText("既知の測定限界として採用");
  await expect(approvalHistory).toContainText("CHECK-02");
  await expect(approvalHistory).toContainText("測定担当者に確認済み");
  await expect.poll(() => new URL(page.url()).searchParams.get("stage")).toBe("approval");
  await expect.poll(() => new URL(page.url()).searchParams.get("revision")).toBe(approvedRevision.id);

  const auditUrl = page.url();
  await page.goto(auditUrl);
  await expect(page.locator(".source-history")).toContainText("既知の測定限界として採用");
  await expect(page.locator(".source-history-list").getByRole("button").filter({ hasText: "v1" })).toHaveAttribute("aria-pressed", "true");
});
