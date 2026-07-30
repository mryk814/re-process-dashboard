import { expect, test } from "@playwright/test";
import { apiBaseUrl } from "./helpers";

test("overview questions deep-link to each decision activity and actual entry", async ({ page }) => {
  const overview = "/?view=project&project=default";
  const questions = [
    ["入力ばらつきに強いか", "robustness-analysis-v1"],
    ["2案の差は何が効いているか", "candidate-difference-v1"],
    ["目標へ届くには何を変えるか", "counterfactual-target-reach-v1"],
  ] as const;

  for (const [question, activityId] of questions) {
    await page.goto(overview);
    await page.getByRole("button", { name: new RegExp(question) }).click();
    await expect(page).toHaveURL(new RegExp(`view=candidate-review.*activity=${activityId}`));
    await expect(page.getByRole("heading", { name: question })).toBeVisible();
    await expect(page.locator(".decision-activity-panel")).toBeVisible();
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
