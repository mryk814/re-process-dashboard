import { expect, test } from "@playwright/test";
import { resolve } from "node:path";


test("single-table repeats become an Observation Profile and Dataset Revision", async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  const source = resolve(
    "backend/tests/fixtures/observation_authoring/weld-tensile-repeats.csv",
  );

  await page.goto("/?view=profile-workbench");
  const panel = page.getByRole("region", {
    name: "反復測定をObservationとして登録",
  });
  await expect(panel).toBeVisible();
  await panel.getByLabel("予測タスク").selectOption("welding-graph-tensile-ts-v1");
  await panel.getByLabel("CSV").setInputFiles(source);
  await panel.getByLabel("Dataset名").fill("外部引張反復測定");
  await panel.getByLabel("1行が表す観測").fill("同一溶接条件から採取した個別引張試験片");
  await expect(panel.getByRole("combobox", { name: "観測ID", exact: true })).toHaveValue("specimen_id");
  await expect(panel.getByRole("combobox", { name: "分割group", exact: true })).toHaveValue("condition_id");
  await expect(panel.getByRole("code").filter({ hasText: /^composition\.C$/ })).toBeVisible();
  await expect(panel.getByRole("code").filter({ hasText: /^TS$/ })).toBeVisible();
  await panel.getByRole("checkbox").check();

  const authored = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/profile-workbench/observation-authoring"
  ));
  const registered = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/profile-workbench/register"
  ));
  await panel.getByRole("button", {
    name: "Profileを検証してDataset Revisionを登録",
  }).click();
  expect((await authored).status()).toBe(200);
  expect((await registered).status()).toBe(200);
  await expect(page.getByText("Data Libraryへ登録しました")).toBeVisible();
  await expect(page.getByRole("button", { name: "データライブラリで確認" })).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("observation-authoring-registered.png"),
    fullPage: true,
  });
});
