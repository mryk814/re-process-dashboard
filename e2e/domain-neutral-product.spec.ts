import { expect, test } from "@playwright/test";
import {
  apiBaseUrl as api,
  createProjectWithBinding,
  starterCandidate,
} from "./helpers";

test("flank-wear uses the shared workbench under the domain-neutral product identity", async ({
  page,
  request,
}, testInfo) => {
  const project = await createProjectWithBinding(
    request,
    "flank-wear-v1",
    `製品境界E2E ${Date.now()}`,
    {
      datasetFilename: "cutting_tool_flank_wear_synthetic_dataset.xlsx",
      includeGallery: true,
    },
  );
  const starter = await starterCandidate(request, "flank-wear-v1");
  const createdCandidate = await request.post(
    `${api}/api/projects/${project.id}/candidates`,
    {
      data: {
        ...starter,
        name: "工具摩耗の基準候補",
      },
    },
  );
  expect(
    createdCandidate.status(),
    await createdCandidate.text(),
  ).toBe(201);
  const candidate = await createdCandidate.json() as { id: string };

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(
    `/?view=candidates&project=${project.id}&candidate=${candidate.id}`,
  );

  await expect(page).toHaveTitle("Evidence Decision Workbench");
  await expect(
    page.locator('[aria-label="Evidence Decision Workbench"]'),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: /製品境界E2E/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "工具摩耗の基準候補の候補名" }),
  ).toBeVisible();
  await expect(page.locator(".comparison-prediction-table")).toBeVisible();
  await expect(page.locator(".comparison-prediction-table thead")).toContainText(
    "VB平均",
  );
  await expect(page.locator(".comparison-prediction-table thead")).toContainText(
    "VB最大",
  );
  await expect(page.getByText("材料的な見方", { exact: true })).toHaveCount(0);
  await expect(page.getByText("新しい材料特性", { exact: true })).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("flank-wear-candidate-workbench.png"),
    fullPage: false,
  });

  await page.goto(
    `/?view=explore&project=${project.id}&candidate=${candidate.id}`,
  );
  await expect(page.getByRole("heading", { name: "範囲探索" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: /工具摩耗の基準候補/ }).first(),
  ).toBeVisible();
  await expect(page.locator(".screening-variable-editor")).toBeVisible();
  await expect(
    page.locator(".screening-run-footer").getByRole("button", {
      name: /点を評価/,
    }),
  ).toBeEnabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth
        <= document.documentElement.clientWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("flank-wear-explore-workbench.png"),
    fullPage: false,
  });

  await page.goto("/?view=data-library");
  await page.getByRole("button", { name: "新しい予測問題" }).click();
  const scaffold = page.getByRole("region", { name: "完全に新しいTaskを準備" });
  await expect(scaffold).toBeVisible();
  await expect(scaffold).toContainText("my-prediction-task-v1");
  await expect(scaffold).not.toContainText("my-material-property-v1");
});
