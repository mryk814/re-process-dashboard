import { expect, test } from "@playwright/test";
import { createProjectWithCandidate } from "./helpers";

test("Screening and Response Curve expose interval meaning without guessing 90%", async ({
  page,
  request,
}) => {
  const project = await createProjectWithCandidate(
    request,
    "annealed-properties-v1",
    `区間意味E2E ${Date.now()}`,
    "探索基準",
  );
  await page.goto(`/?view=explore&project=${project.id}`);
  await page.locator(".screening-mode-options").getByRole("button", { name: /有望候補を探す/ }).click();
  await page.getByLabel(/主目標: .*の下限/).fill("500");
  const screeningResponse = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/screening"
  ));
  await page.locator(".screening-run-footer .primary-button").click();
  expect((await screeningResponse).status()).toBe(201);

  const screeningLabel = "Bayesian予測区間（90%）";
  await expect(page.locator(".screening-results-table").first()).toContainText(screeningLabel);
  await page.getByRole("button", { name: "地図", exact: true }).click();
  const screeningPoint = page.locator(".screen-map-point circle[role=button]").first();
  await screeningPoint.focus();
  await expect(screeningPoint).toHaveAttribute("aria-label", new RegExp(screeningLabel));
  await expect(page.locator(".screen-map .svg-chart-tooltip")).toContainText(screeningLabel);
  await expect(page.getByRole("main")).not.toContainText("90%区間");

  await page.goto("/?view=candidates&project=hot-rolling-default");
  await expect(page.locator(".response-curves-panel .inference-surface-status")).toHaveText("最新");
  const curvePoint = page.locator(".response-curve-card .svg-chart-hit-target").first();
  const curveLabel = "Bayesian予測区間（90%）";
  await expect(curvePoint).toHaveAttribute("aria-label", new RegExp(curveLabel));
  await curvePoint.hover({ force: true });
  await expect(page.locator(".response-curve-card .svg-chart-tooltip")).toContainText(curveLabel);
  await expect(page.locator(".response-curve-card")).not.toContainText("90%区間");
});
