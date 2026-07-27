import { expect, test } from "@playwright/test";
import { expectNoBlockingAxeViolations } from "./axe";

const brokenPackage = process.env.PLAYWRIGHT_BROKEN_TASK_PACKAGE;
const apiPort = Number(process.env.PLAYWRIGHT_API_PORT ?? 8875);

test("an unavailable task keeps fixed references and read-only diagnostics accessible", async ({
  page,
  request,
}) => {
  test.skip(!brokenPackage, "PLAYWRIGHT_BROKEN_TASK_PACKAGE is required");

  const projectsResponse = await request.get(`http://127.0.0.1:${apiPort}/api/projects`);
  expect(projectsResponse.ok()).toBe(true);
  const projects = await projectsResponse.json() as Array<{
    id: string;
    task_id: string;
    dataset_view_revision_id: string | null;
    model_package_ref_id: string | null;
    model_package_manifest_digest: string | null;
  }>;
  const project = projects.find((item) => item.task_id === "heat-treatment-tradeoff-v1");
  expect(project).toBeTruthy();
  expect(project?.dataset_view_revision_id).toBeTruthy();
  expect(project?.model_package_ref_id).toBeTruthy();
  expect(project?.model_package_manifest_digest).toBeTruthy();

  await page.goto(`/?view=project&project=${project!.id}`);
  await expect(page.getByText("この予測タスクは一時的に利用できません").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "設定を編集" })).toBeVisible();
  await expect(page.getByText(project!.dataset_view_revision_id!, { exact: false }).first()).toBeVisible();
  await expect(page.getByText(project!.model_package_ref_id!, { exact: false }).first()).toBeVisible();
  await expect(page.getByText(project!.model_package_manifest_digest!, { exact: false }).first()).toBeVisible();
  await expectNoBlockingAxeViolations(page, "Task unavailable");

  await page.goto(`/?view=candidates&project=${project!.id}`);
  await page.getByRole("button", { name: "参照状態を確認する" }).click();
  await expect(page).toHaveURL(/view=project.*project_settings=task/);
  const diagnostic = page.getByRole("status", { name: "予測タスクの利用停止診断" });
  await expect(diagnostic).toBeVisible();
  await expect(diagnostic.getByText(
    "heat-treatment-tradeoff-v1",
    { exact: false },
  ).first()).toBeVisible();
  await expect(diagnostic.getByText("モデルPackage", { exact: true })).toBeVisible();
  await expect(diagnostic.getByText(brokenPackage!, { exact: false })).toBeVisible();
  await expect(diagnostic.getByText("復旧の手掛かり", { exact: false })).toBeVisible();

  await page.getByRole("navigation", { name: "Project設定メニュー" })
    .getByRole("button", { name: "入力範囲" }).click();
  await expect(page.locator(".input-range-settings input")).not.toHaveCount(0);
  await expect.poll(() => page.locator(".input-range-settings input").evaluateAll(
    (items) => items.every((item) => (item as HTMLInputElement).disabled),
  )).toBe(true);
  for (const name of ["初期値に戻す", "保存"]) {
    await expect(page.getByRole("button", { name, exact: true })).toBeDisabled();
  }

  await page.goto(`/?view=project&project=${project!.id}&project_settings=display`);
  await expect(page.getByRole("status", { name: "予測タスクの利用停止診断" })).toBeVisible();
  await expect(page.locator(".display-decimal-settings input")).not.toHaveCount(0);
  await expect.poll(() => page.locator(".display-decimal-settings input").evaluateAll(
    (items) => items.every((item) => (item as HTMLInputElement).disabled),
  )).toBe(true);
  for (const name of ["デフォルトに戻す", "保存"]) {
    await expect(page.getByRole("button", { name, exact: true })).toBeDisabled();
  }
});
