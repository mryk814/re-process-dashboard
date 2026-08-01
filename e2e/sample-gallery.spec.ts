import { expect, test } from "@playwright/test";

import { apiBaseUrl } from "./helpers";

test("fresh workspace can add, remove, and restore bundled samples", async ({ page, request }) => {
  const projectsResponse = await request.get(`${apiBaseUrl}/api/projects`);
  expect(projectsResponse.ok()).toBeTruthy();
  const initialProjects = await projectsResponse.json() as Array<{ id: string; starter: boolean }>;
  expect(initialProjects).toEqual([
    expect.objectContaining({ id: "default", starter: true }),
  ]);

  const initialDatasets = await (
    await request.get(`${apiBaseUrl}/api/data-library/datasets`)
  ).json() as unknown[];
  expect(initialDatasets).toHaveLength(1);

  await page.goto("/");
  await expect(page).toHaveURL(/view=project.*project=default/);
  const sampleGroup = page.locator(".project-list-group.bundled-samples");
  await expect(
    sampleGroup.getByRole("button", { name: /クイックスタート/ }),
  ).toHaveAttribute("aria-expanded", "true");
  await expect(sampleGroup.locator(".project-list-item")).toHaveCount(1);
  const quickstartName = (
    await page.locator(".project-hub-header h2").innerText()
  ).split("\n")[0];

  await page.getByRole("button", { name: "サンプルから始める" }).click();
  const gallery = page.locator(".sample-gallery-panel");
  await expect(gallery.locator(".sample-gallery-card")).toHaveCount(3);
  await expect(gallery.getByText("公開データ", { exact: true }).first()).toBeVisible();
  await expect(gallery.getByText("合成データ", { exact: true })).toBeVisible();
  const firstAvailable = gallery.locator(".sample-gallery-card")
    .filter({ has: page.getByRole("button", { name: "このサンプルで始める", exact: true }) })
    .first();
  const sampleName = await firstAvailable.locator("h3").innerText();
  await firstAvailable.getByRole("button", { name: "このサンプルで始める", exact: true }).click();
  await expect(page.locator(".project-hub-header h2")).toContainText(sampleName);
  await expect(sampleGroup.locator(".project-list-item")).toHaveCount(2);

  await page.getByRole("button", { name: "サンプルから始める" }).click();
  const installedSample = gallery.locator(".sample-gallery-card")
    .filter({ hasText: sampleName });
  await expect(installedSample.getByRole("button", { name: "取り除く" })).toBeEnabled();
  await installedSample.getByRole("button", { name: "取り除く" }).click();
  await expect(page).toHaveURL(/view=project.*project=default/);
  await expect(page.locator(".project-hub-header h2")).toContainText(quickstartName);
  await expect(sampleGroup.locator(".project-list-item")).toHaveCount(1);
  await page.getByRole("button", { name: "サンプルから始める" }).click();
  await expect(installedSample.getByRole("button", { name: "このサンプルで始める", exact: true })).toBeEnabled();

  await installedSample.getByRole("button", { name: "このサンプルで始める", exact: true }).click();
  await expect(page.locator(".project-hub-header h2")).toContainText(sampleName);
  await expect(sampleGroup.locator(".project-list-item")).toHaveCount(2);

  const visibleDatasets = await (
    await request.get(`${apiBaseUrl}/api/data-library/datasets`)
  ).json() as unknown[];
  expect(visibleDatasets.length).toBeGreaterThan(initialDatasets.length);
});
