import { basename } from "node:path";

import { expect, test } from "@playwright/test";
import { expectNoBlockingAxeViolations } from "./axe";

const brokenPackage = process.env.PLAYWRIGHT_BROKEN_TASK_PACKAGE;
const brokenProjectId = process.env.PLAYWRIGHT_BROKEN_PROJECT_ID;
const apiPort = Number(process.env.PLAYWRIGHT_API_PORT ?? 8875);

test("an unavailable task keeps fixed references and read-only diagnostics accessible", async ({
  page,
  request,
}) => {
  test.skip(
    !brokenPackage || !brokenProjectId,
    "PLAYWRIGHT_BROKEN_TASK_PACKAGE and PLAYWRIGHT_BROKEN_PROJECT_ID are required",
  );

  const projectsResponse = await request.get(`http://127.0.0.1:${apiPort}/api/projects`);
  expect(projectsResponse.ok()).toBe(true);
  const projects = await projectsResponse.json() as Array<{
    id: string;
    task_id: string;
    dataset_view_revision_id: string | null;
    model_package_ref_id: string | null;
    model_package_manifest_digest: string | null;
  }>;
  const project = projects.find((item) => item.id === brokenProjectId);
  expect(project).toBeTruthy();
  expect(project?.task_id).toBe("heat-treatment-tradeoff-v1");
  expect(project?.dataset_view_revision_id).toBeTruthy();
  expect(project?.model_package_ref_id).toBeTruthy();
  expect(project?.model_package_manifest_digest).toBeTruthy();

  const taskDefinitionUrl = `${apiPort}/api/projects/${project!.id}/task-definition`;
  const taskDefinitionResponse = page.waitForResponse((response) => (
    response.url().includes(taskDefinitionUrl)
    && response.request().method() === "GET"
  ));
  await page.goto(`/?view=project&project=${project!.id}`);
  const resolvedTaskDefinition = await taskDefinitionResponse;
  expect(resolvedTaskDefinition.status()).toBe(200);
  expect((await resolvedTaskDefinition.json() as {
    availability: { status: string };
  }).availability.status).toBe("unavailable");
  await expect(page.getByText("この予測タスクは一時的に利用できません").first()).toBeVisible();
  await expect(page.getByRole("alert").filter({ hasText: "固定参照を確認できません" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "設定", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "設定", exact: true }).click();
  await page.getByRole("navigation", { name: "Project設定カテゴリ" })
    .getByRole("button", { name: "証拠・管理" }).click();
  await page.locator(".project-reference-identity > summary").click();
  await expect(page.getByText(project!.dataset_view_revision_id!, { exact: false }).first()).toBeVisible();
  await expect(page.getByText(project!.model_package_ref_id!, { exact: false }).first()).toBeVisible();
  await expect(page.getByText(project!.model_package_manifest_digest!, { exact: false }).first()).toBeVisible();
  await expectNoBlockingAxeViolations(page, "Task unavailable");

  await page.goto(`/?view=candidates&project=${project!.id}`);
  await page.getByRole("button", { name: "参照状態を確認する" }).click();
  await expect(page).toHaveURL(/view=project-settings.*project_settings=task/);
  const diagnostic = page.getByRole("status", { name: "予測タスクの利用停止診断" });
  await expect(diagnostic).toBeVisible();
  await expect(diagnostic.getByText(
    "heat-treatment-tradeoff-v1",
    { exact: false },
  ).first()).toBeVisible();
  await expect(diagnostic.getByText("モデルPackage", { exact: true })).toBeVisible();
  await expect(diagnostic.getByText(basename(brokenPackage!), { exact: false })).toBeVisible();
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

  await page.goto(`/?view=project-settings&project=${project!.id}&project_settings=display`);
  await expect(page.getByRole("status", { name: "予測タスクの利用停止診断" })).toBeVisible();
  await expect(page.locator(".display-decimal-settings input")).not.toHaveCount(0);
  await expect.poll(() => page.locator(".display-decimal-settings input").evaluateAll(
    (items) => items.every((item) => (item as HTMLInputElement).disabled),
  )).toBe(true);
  for (const name of ["デフォルトに戻す", "保存"]) {
    await expect(page.getByRole("button", { name, exact: true })).toBeDisabled();
  }
});
