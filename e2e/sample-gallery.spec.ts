import { expect, test } from "@playwright/test";

import { apiBaseUrl } from "./helpers";

test("fresh workspace starts with one Quickstart and installs samples on demand", async ({ page, request }) => {
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

  const gallery = page.locator(".sample-gallery-list");
  await gallery.locator("summary").click();
  await expect(gallery.locator(".sample-gallery-item")).toHaveCount(10);
  const firstAvailable = gallery.locator(".sample-gallery-item")
    .filter({ has: page.getByRole("button", { name: "追加", exact: true }) })
    .first();
  const sampleName = await firstAvailable.locator("strong").innerText();
  await firstAvailable.getByRole("button", { name: "追加", exact: true }).click();
  await expect(page.locator(".project-hub-header h2")).toContainText(sampleName);
  await expect(sampleGroup.locator(".project-list-item")).toHaveCount(2);

  await expect(gallery.locator(".sample-gallery-item")).toHaveCount(9);
  await gallery.getByRole("button", { name: "利用可能なサンプルを一括追加" }).click();
  await expect(sampleGroup.locator(".project-list-item")).toHaveCount(11);
  await expect(page.locator(".sample-gallery-list")).toHaveCount(0);

  const visibleDatasets = await (
    await request.get(`${apiBaseUrl}/api/data-library/datasets`)
  ).json() as unknown[];
  expect(visibleDatasets.length).toBeGreaterThan(initialDatasets.length);
});
