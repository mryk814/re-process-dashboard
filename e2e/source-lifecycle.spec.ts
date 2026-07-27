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
  await expect(section.locator(".source-quality-summary")).toContainText("A-03");
  await expect(section.locator(".source-quality-summary")).toContainText("目的変数がありません");
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
  await expect(section.getByLabel("分割group field")).toHaveAttribute("placeholder", "id");
  await expect(section.getByLabel("fold数")).toHaveValue("2");
  await expect(section).toContainText("再学習・有効化は行っていません");
  const approvedDetail = await (await request.get(
    `${apiBaseUrl}/api/data-lifecycle/connectors/${connector.id}`,
  )).json();
  const approvedRevision = approvedDetail.canonical_revisions.at(-1);
  expect(approvedRevision.actor).toBe("local-workspace-user");
  expect(approvedRevision.reason).toBe("既知の測定限界として採用");
  expect(approvedRevision.override_count).toBe(1);
  expect(approvedRevision.approved_row_count).toBe(3);

  await section.getByRole("button", { name: "学習用スナップショットを作成" }).click();
  await expect(section.getByText("学習用スナップショット作成済み")).toBeVisible();
  await expect(section).toContainText("2行");
  await expect(section).toContainText("再学習・モデル検証・有効化は別の操作です");
  const trainingDetail = await (await request.get(
    `${apiBaseUrl}/api/data-lifecycle/connectors/${connector.id}`,
  )).json();
  const trainingSnapshot = trainingDetail.training_snapshots.at(-1);
  expect(trainingSnapshot.schema_version).toBe("approved-training-snapshot/v2");
  expect(trainingSnapshot.target_cohorts).toHaveLength(1);
  expect(trainingSnapshot.target_cohorts[0].target_key).toBe("target");
  expect(trainingSnapshot.target_cohorts[0].split_group_count).toBe(2);

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
  await page.reload();
  const repeatedSection = page.locator(".source-lifecycle-section");
  await repeatedSection.getByRole("button", { name: "品質判定を実行" }).click();
  await repeatedSection.getByLabel(/^承認理由/).fill("定期更新として承認");
  await repeatedSection.getByRole("button", { name: "正規データセットを承認" }).click();
  await repeatedSection.getByLabel("用途").fill("更新版の再評価");
  await repeatedSection.getByRole("button", { name: "学習用スナップショットを作成" }).click();

  await page.reload();
  const historySection = page.locator(".source-lifecycle-section");
  await expect(historySection).toBeVisible();
  await expect(historySection.locator(".source-stage-rail li").nth(0)).toContainText("2版");
  await expect(historySection.locator(".source-stage-rail li").nth(1)).toContainText("2版");
  await expect(historySection.locator(".source-stage-rail li").nth(2)).toContainText("2版");
  await expect(historySection.locator(".source-stage-rail li").nth(3)).toContainText("2版");

  await historySection.locator(".source-stage-rail li").nth(3).getByRole("button").click();
  const trainingHistory = historySection.locator(".source-history");
  await expect(trainingHistory).toContainText("approved-training-snapshot/v2");
  await expect(trainingHistory).toContainText("分割group field");
  await expect(trainingHistory).toContainText("id");
  await expect(trainingHistory).toContainText("target · 2行");
  await trainingHistory.getByText("target · 2行").click();
  await expect(trainingHistory).toContainText("2 groupの割当を固定");
  await expect(trainingHistory).toContainText("cohort digest");
  await expect(trainingHistory).toContainText("split digest");

  await historySection.locator(".source-stage-rail li").nth(2).getByRole("button").click();
  const approvalHistory = historySection.locator(".source-history");
  await expect(approvalHistory.locator(".source-history-list").getByRole("button")).toHaveCount(2);
  await approvalHistory.locator(".source-history-list").getByRole("button").filter({ hasText: "v1" }).click();
  await expect(approvalHistory).toContainText("既知の測定限界として採用");
  await expect(approvalHistory).toContainText("上書き1行");
  await expect.poll(() => new URL(page.url()).searchParams.get("stage")).toBe("approval");
  await expect.poll(() => new URL(page.url()).searchParams.get("revision")).toBe(approvedRevision.id);

  const auditUrl = page.url();
  await page.goto(auditUrl);
  await expect(page.locator(".source-history")).toContainText("既知の測定限界として採用");
  await expect(page.locator(".source-history-list").getByRole("button").filter({ hasText: "v1" })).toHaveAttribute("aria-pressed", "true");
});

test("late connector detail cannot replace the selected connector", async ({ page, request }) => {
  const createConnector = async (name: string) => {
    const response = await request.post(`${apiBaseUrl}/api/data-lifecycle/connectors`, {
      data: {
        schema_version: "source-connector/v1",
        name,
        connector_type: "object_storage_json_v1",
        source_locator: `s3://e2e-bucket/${name}.json`,
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
    expect(response.ok()).toBeTruthy();
    return response.json();
  };
  const slow = await createConnector(`遅い接続先-${Date.now()}`);
  const selected = await createConnector(`選択接続先-${Date.now()}`);
  await page.route(`**/api/data-lifecycle/connectors/${slow.id}`, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 500));
    await route.continue();
  });

  await page.goto(`/?view=data-library&tab=update&connector=${slow.id}`);
  await page.getByRole("button", { name: new RegExp(selected.name) }).click();
  const detailHeader = page.locator(".source-lifecycle-detail > header");
  await expect(detailHeader).toContainText(selected.name);
  await page.waitForTimeout(700);
  await expect(detailHeader).toContainText(selected.name);
  await expect(detailHeader).not.toContainText(slow.name);
});

test("reason audit loads a blocked row beyond the first hundred without quarantine", async ({ page, request }) => {
  const options = await (await request.get(`${apiBaseUrl}/api/project-creation-options`)).json();
  const profile = options.datasets[0].profile_revision;
  const suffix = Date.now();
  const connectorResponse = await request.post(`${apiBaseUrl}/api/data-lifecycle/connectors`, {
    data: {
      schema_version: "source-connector/v1",
      name: `理由監査-${suffix}`,
      connector_type: "object_storage_json_v1",
      source_locator: `s3://e2e-bucket/reason-audit-${suffix}.json`,
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
  const rows = Array.from({ length: 100 }, (_, index) => ({
    id: `accepted-${index.toString().padStart(3, "0")}`,
    value: index,
  }));
  rows.push({ id: "accepted-000", value: 101 });
  const fetchResponse = await request.post(
    `${apiBaseUrl}/api/data-lifecycle/connectors/${connector.id}/fetch`,
    {
      data: {
        schema_version: "source-fetch-request/v1",
        trigger_kind: "manual",
        object_content: JSON.stringify(rows),
        object_version: "101",
        retry_of: null,
      },
    },
  );
  expect(fetchResponse.ok()).toBeTruthy();
  const fetched = await fetchResponse.json();
  const recipeResponse = await request.post(`${apiBaseUrl}/api/data-lifecycle/recipes`, {
    data: {
      schema_version: "curation-recipe/v1",
      recipe_id: `reason-audit-${suffix}`,
      version: 1,
      name: "理由監査",
      steps: [{ kind: "required_fields_v1", fields: ["id"] }],
    },
  });
  expect(recipeResponse.ok()).toBeTruthy();
  const recipe = await recipeResponse.json();
  const runResponse = await request.post(
    `${apiBaseUrl}/api/data-lifecycle/raw-snapshots/${fetched.snapshot.id}/curation-runs`,
    {
      data: {
        recipe_resource_id: recipe.id,
        profile_revision_id: profile.id,
        profile_digest: profile.profile_digest,
      },
    },
  );
  expect(runResponse.ok()).toBeTruthy();

  await page.goto(`/?view=data-library&tab=update&connector=${connector.id}`);
  const summary = page.locator(".source-quality-summary");
  await expect(summary).toContainText("隔離0");
  await expect(summary).toContainText("停止2");
  await summary.getByText("理由付きの行").click();
  await expect(summary.getByText("行識別キーが重複しています").first()).toBeVisible();
  await expect(summary.getByText(/accepted-000/).first()).toBeVisible();
});
