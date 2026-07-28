import { expect, test } from "@playwright/test";
import { apiBaseUrl } from "./helpers";

test("first launch lands on an identified bundled sample overview", async ({ page, request }) => {
  const projectsResponse = await request.get(`${apiBaseUrl}/api/projects`);
  expect(projectsResponse.ok()).toBeTruthy();
  const starterCount = ((await projectsResponse.json()) as Array<{ starter: boolean }>)
    .filter((project) => project.starter)
    .length;
  expect(starterCount).toBeGreaterThan(1);

  await page.goto("/");

  await expect(page).toHaveURL(/view=project/);
  const sampleGroup = page.locator(".project-list-group.bundled-samples");
  const sampleToggle = sampleGroup.getByRole("button", { name: /同梱サンプル/ }).first();
  await expect(sampleToggle).toHaveAttribute("aria-expanded", "true");
  await expect(sampleGroup.locator(".project-list-item")).toHaveCount(starterCount);
  await expect(page.locator(".project-list-items > .project-list-group:not(.bundled-samples) .project-list-name b")).toHaveCount(0);
  await expect(page.locator('.project-list-item[aria-current="page"]')).toBeVisible();
  await expect(page.locator(".project-hub-header")).toContainText("同梱サンプル");
  const notice = page.getByRole("region", { name: "同梱サンプルの案内" });
  await expect(notice).toContainText("これは動作確認用の同梱サンプルです");
  await page.setViewportSize({ width: 800, height: 900 });
  await expect.poll(() => sampleGroup.locator(".project-list-group-projects").evaluate(
    (element) => element.scrollWidth > element.clientWidth,
  )).toBe(true);
  await notice.getByRole("button", { name: "自分のデータで始める" }).click();
  await expect(page).toHaveURL(/view=data-library/);
  await expect(page.getByRole("heading", { name: "データライブラリ" })).toBeVisible();
});

test("overview questions deep-link to each decision activity and actual entry", async ({ page }) => {
  const overview = "/?view=project&project=default";
  const questions = [
    ["入力ばらつきに強いか", "robustness-analysis-v1", "ロバストネス／公差解析"],
    ["2案の差は何が効いているか", "candidate-difference-v1", "候補差分の要因分解"],
    ["目標へ届くには何を変えるか", "counterfactual-target-reach-v1", "目標へ届く最小変更"],
  ] as const;

  for (const [question, activityId, panelHeading] of questions) {
    await page.goto(overview);
    await page.getByRole("button", { name: new RegExp(question) }).click();
    await expect(page).toHaveURL(new RegExp(`activity=${activityId}`));
    await expect(page.getByRole("heading", { name: panelHeading })).toBeVisible();
    await expect(page.locator(".comparison-panel-toggle")).toContainText(question);
  }

  await page.goto(overview);
  await page.getByRole("button", { name: /実測を記録する/ }).click();
  await expect(page).toHaveURL(/candidate_section=actuals/);
  await expect(page.getByRole("region", { name: "予測と実測の照合" })).toBeVisible();
});

test("candidate questions are disabled with a reason in an empty project", async ({ page }) => {
  const reference = await (await page.request.get(`${apiBaseUrl}/api/projects/default`)).json() as {
    task_id: string;
    dataset_view_revision_id: string;
    model_package_ref_id: string;
  };
  const createdResponse = await page.request.post(`${apiBaseUrl}/api/projects`, {
    data: {
      name: `空の導線確認 ${Date.now()}`,
      task_id: reference.task_id,
      dataset_view_revision_id: reference.dataset_view_revision_id,
      model_package_ref_id: reference.model_package_ref_id,
    },
  });
  expect(createdResponse.status()).toBe(201);
  const created = await createdResponse.json() as { id: string };

  await page.goto(`/?view=project&project=${created.id}`);
  const questions = page.locator(".project-action-groups section").filter({
    has: page.getByRole("heading", { name: "候補を確かめる" }),
  });
  await expect(questions.getByRole("button")).toHaveCount(3);
  for (const button of await questions.getByRole("button").all()) {
    await expect(button).toBeDisabled();
    await expect(button).toContainText("先に候補が必要です");
  }

  expect((await page.request.delete(`${apiBaseUrl}/api/projects/${created.id}`)).status()).toBe(204);
});
