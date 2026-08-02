import { expect, test } from "@playwright/test";
import { apiBaseUrl } from "./helpers";

test("a curation-row failure keeps Connector and Raw evidence ready for scoped retry", async ({ page, request }) => {
  const options = await (await request.get(
    `${apiBaseUrl}/api/project-creation-options`,
  )).json();
  const profile = options.datasets[0].profile_revision;
  const suffix = Date.now();
  const connectorResponse = await request.post(
    `${apiBaseUrl}/api/data-lifecycle/connectors`,
    {
      data: {
        schema_version: "source-connector/v1",
        name: `partial-failure-${suffix}`,
        connector_type: "object_storage_json_v1",
        source_locator: `s3://e2e-bucket/partial-failure-${suffix}.json`,
        selection: {
          schema_version: "object-selection/v1",
          format: "json_array",
          primary_key: "id",
          included_fields: [],
        },
        trigger_policy: "manual_only",
        schedule: null,
      },
    },
  );
  expect(connectorResponse.ok()).toBeTruthy();
  const connector = await connectorResponse.json();
  const fetchResponse = await request.post(
    `${apiBaseUrl}/api/data-lifecycle/connectors/${connector.id}/fetch`,
    {
      data: {
        schema_version: "source-fetch-request/v1",
        trigger_kind: "manual",
        object_content: JSON.stringify([{ id: "A-01", value: "12.4" }]),
        object_version: `partial-${suffix}`,
        retry_of: null,
      },
    },
  );
  expect(fetchResponse.ok()).toBeTruthy();
  const fetched = await fetchResponse.json();
  const recipeResponse = await request.post(`${apiBaseUrl}/api/data-lifecycle/recipes`, {
    data: {
      schema_version: "curation-recipe/v1",
      recipe_id: `partial-failure-${suffix}`,
      version: 1,
      name: `partial failure ${suffix}`,
      steps: [{ kind: "required_fields_v1", fields: ["id", "value"] }],
    },
  });
  expect(recipeResponse.ok()).toBeTruthy();
  const recipe = await recipeResponse.json();
  const curationResponse = await request.post(
    `${apiBaseUrl}/api/data-lifecycle/raw-snapshots/${fetched.snapshot.id}/curation-runs`,
    {
      data: {
        recipe_resource_id: recipe.id,
        profile_revision_id: profile.id,
        profile_digest: profile.profile_digest,
      },
    },
  );
  expect(curationResponse.ok()).toBeTruthy();
  const run = await curationResponse.json();

  let curationRowAttempts = 0;
  await page.route("**/api/data-lifecycle/curation-runs/**/rows?*", async (route) => {
    const url = new URL(route.request().url());
    if (
      url.pathname === `/api/data-lifecycle/curation-runs/${run.id}/rows`
      && url.searchParams.get("limit") === "100"
    ) {
      curationRowAttempts += 1;
      if (curationRowAttempts === 1) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "temporary curation rows failure" }),
        });
        return;
      }
    }
    await route.continue();
  });

  await page.goto(
    `/?view=data-library&tab=update&connector=${connector.id}&stage=curation&revision=${run.id}`,
  );
  const section = page.locator(".source-lifecycle-section");
  await expect(section.getByRole("alert").filter({
    hasText: "品質判定行を取得できませんでした",
  })).toBeVisible();
  await expect(section.locator(".source-lifecycle-detail > header")).toContainText(connector.name);
  await expect(section.locator(".source-stage-rail li").nth(0)).toContainText("1版");
  await expect(section.getByText("品質判定 v1", { exact: true })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("stage")).toBe("curation");

  const retry = section.getByRole("button", { name: "品質判定行を再試行" });
  await retry.click();
  const update = section.getByRole("button", { name: "品質判定行を更新" });
  await expect(update).toBeVisible();
  await expect(update).toBeFocused();
  expect(curationRowAttempts).toBe(2);
  expect(new URL(page.url()).searchParams.get("stage")).toBe("curation");
});
