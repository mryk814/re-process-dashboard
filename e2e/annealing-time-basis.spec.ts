import { expect, test } from "@playwright/test";
import { createProjectWithCandidate } from "./helpers";

test("annealing candidate keeps line-speed and elapsed-time editing semantics aligned", async ({ page, request }) => {
  const project = await createProjectWithCandidate(
    request,
    "annealed-properties-v1",
    `時間基準E2E ${Date.now()}`,
    "基準候補",
  );
  await page.goto(`/?view=candidates&project=${project.id}`);
  await expect(page.getByRole("heading", { name: /候補比較表/ })).toBeVisible();

  const timeBasis = page.getByRole("combobox", { name: "ヒートパターンの時間基準" });
  const lineSpeed = page.getByRole("spinbutton", { name: "基準候補 LSの数値" });
  const secondTime = page.getByRole("spinbutton", { name: "点2の時間（分）" });
  await expect(timeBasis).toHaveValue("line_speed");
  await expect(secondTime).toBeDisabled();

  const originalSpeed = Number(await lineSpeed.inputValue());
  const originalTime = Number(await secondTime.inputValue());
  const speedSave = page.waitForResponse((response) => (
    response.request().method() === "PUT"
    && new URL(response.url()).pathname.includes(`/api/projects/${project.id}/candidates/`)
  ));
  await lineSpeed.fill(String(originalSpeed + 10));
  expect((await speedSave).status()).toBe(200);
  await expect.poll(async () => Number(await secondTime.inputValue())).toBeLessThan(originalTime);

  const basisSave = page.waitForResponse((response) => (
    response.request().method() === "PUT"
    && new URL(response.url()).pathname.includes(`/api/projects/${project.id}/candidates/`)
  ));
  await timeBasis.selectOption("elapsed_time");
  expect((await basisSave).status()).toBe(200);
  await expect(secondTime).toBeEnabled();

  const directTime = Number(await secondTime.inputValue()) + 0.25;
  const timeSave = page.waitForResponse((response) => (
    response.request().method() === "PUT"
    && new URL(response.url()).pathname.includes(`/api/projects/${project.id}/candidates/`)
  ));
  await secondTime.fill(String(directTime));
  expect((await timeSave).status()).toBe(200);
  await expect(secondTime).toHaveValue(String(directTime));

  const elapsedSpeed = Number(await lineSpeed.inputValue());
  const independentSpeedSave = page.waitForResponse((response) => (
    response.request().method() === "PUT"
    && new URL(response.url()).pathname.includes(`/api/projects/${project.id}/candidates/`)
  ));
  await lineSpeed.fill(String(elapsedSpeed + 5));
  expect((await independentSpeedSave).status()).toBe(200);
  await expect(secondTime).toHaveValue(String(directTime));
});
