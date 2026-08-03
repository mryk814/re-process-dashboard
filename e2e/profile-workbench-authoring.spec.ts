import { expect, test } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { join } from "node:path";


test("unknown workbook names become a saved Profile and registered Dataset", async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  const workbook = testInfo.outputPath("renamed-source.xlsx");
  const built = spawnSync(
    process.platform === "win32" ? "uv.exe" : "uv",
    ["run", "python", "e2e/helpers/build-profile-workbench-fixture.py", workbook],
    { cwd: process.cwd(), encoding: "utf8" },
  );
  expect(built.status, built.stderr || built.stdout).toBe(0);

  await page.goto("/?view=profile-workbench");
  await page.locator('input[accept^=".xlsx"]').setInputFiles(workbook);
  await page.getByRole("button", { name: "内容を確認" }).click();

  const editor = page.getByRole("region", { name: "Excel側の名前を対応付ける" });
  await expect(editor).toBeVisible();
  await expect(editor).toContainText("Taskとrelation構造はBase Profileのまま");
  await expect(editor).toContainText("relation");
  await expect(editor).toContainText("補助データ · 未対応でも登録可能");
  await expect(editor).toContainText("→");
  await editor.screenshot({ path: testInfo.outputPath("profile-binding-review.png") });

  const pendingRows = editor.locator(".profile-binding-row.pending.required");
  expect(await pendingRows.count()).toBeGreaterThan(0);
  await editor.getByLabel("hot_rollingのExcel側シート").selectOption("熱延条件（設備B）");
  await editor.getByLabel("均熱温度[℃]のExcel側列").selectOption("加熱温度[℃]");
  await expect(editor.getByLabel("均熱温度[℃]のExcel側単位")).toHaveValue("℃");
  await expect(editor.getByText("登録に必要な対応は確定")).toBeVisible();

  await editor.getByRole("button", { name: "Profileを保存して再検査" }).click();
  await expect(page.getByRole("status")).toContainText("自分のProfileとして保存しました");
  await expect(page.getByRole("heading", { name: "Canonical preview" })).toBeVisible();

  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "JSONを出力" }).click();
  expect((await download).suggestedFilename()).toMatch(/\.json$/);

  const registrationResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/profile-workbench/register"
  ));
  await page.getByRole("button", { name: "この内容で登録" }).click();
  expect((await registrationResponse).status()).toBe(200);
  await expect(page.getByRole("button", { name: "このDatasetでプロジェクト作成" })).toBeVisible();
  const creationOptionsResponse = page.waitForResponse((response) => (
    response.request().method() === "GET"
    && new URL(response.url()).pathname === "/api/project-creation-options"
  ));
  await page.getByRole("button", { name: "このDatasetでプロジェクト作成" }).click();
  expect((await creationOptionsResponse).status()).toBe(200);
  await expect(page.getByRole("heading", { name: "新しいプロジェクト" })).toBeVisible();
  await expect(page.getByLabel("Dataset")).not.toHaveValue("");
});
