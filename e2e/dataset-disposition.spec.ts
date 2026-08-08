import { expect, test } from "@playwright/test";
import { spawnSync } from "node:child_process";


test("Profile Workbench keeps partial heat-series disposition through registration and Data Library", async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  const workbook = testInfo.outputPath("unresolved-heat-series.xlsx");
  const built = spawnSync(
    process.platform === "win32" ? "uv.exe" : "uv",
    [
      "run",
      "python",
      "e2e/helpers/build-profile-workbench-fixture.py",
      workbook,
      "--fixture",
      "unresolved-heat-series",
    ],
    { cwd: process.cwd(), encoding: "utf8" },
  );
  expect(built.status, built.stderr || built.stdout).toBe(0);

  await page.goto("/?view=profile-workbench");
  await page.locator('input[accept^=".xlsx"]').setInputFiles(workbook);
  const profileSelect = page.getByLabel("データセットプロファイル");
  const processProfile = profileSelect.locator("option").filter({
    hasText: "process-v1",
    hasNotText: "自分のProfile",
  }).first();
  const processDigest = await processProfile.getAttribute("value");
  expect(processDigest).toBeTruthy();
  await profileSelect.selectOption(processDigest!);

  const inspectionResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/profile-workbench/inspect"
  ));
  await page.getByRole("button", { name: "内容を確認" }).click();
  expect((await inspectionResponse).status()).toBe(200);
  await expect(page.getByRole("heading", { name: "Canonical preview" })).toBeVisible();
  await expect(page.getByText(/ヒートパターンを作れない工程条件/)).toBeVisible();
  await expect(page.getByText("登録後のDataset扱い: 一部操作対象外")).toBeVisible();
  await expect(page.getByText("Training").first()).toBeVisible();
  await expect(page.getByText("必要系列なしで対象外").first()).toBeVisible();

  const registrationResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/profile-workbench/register"
  ));
  await page.getByRole("button", { name: "この内容で登録" }).click();
  expect((await registrationResponse).status()).toBe(200);
  await expect(page.getByRole("status")).toContainText("登録済み・一部操作対象外");

  const datasetsResponse = page.waitForResponse((response) => (
    response.request().method() === "GET"
    && new URL(response.url()).pathname === "/api/data-library/datasets"
  ));
  await page.getByRole("button", { name: "データライブラリで確認" }).click();
  await expect(page.getByRole("heading", { name: "データライブラリ" })).toBeVisible();
  expect((await datasetsResponse).status()).toBe(200);
  const card = page.locator(".dataset-card").filter({ hasText: "unresolved-heat-series.xlsx" }).first();
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: /詳細を表示/ }).click();
  await expect(page.getByRole("heading", { name: "登録後の扱い" })).toBeVisible();
  await expect(page.locator(".dataset-disposition-panel")).toContainText("登録済み・一部操作対象外");
  await expect(page.locator(".dataset-disposition-panel")).toContainText("必要系列なしで対象外");
  await expect(page.locator(".dataset-disposition-panel")).toContainText("改善候補");
});
