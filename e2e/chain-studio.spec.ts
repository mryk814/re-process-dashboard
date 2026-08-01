import { expect, test } from "@playwright/test";

import { expectNoBlockingAxeViolations } from "./axe";

test("scalar Chain Studio validates typed bindings and publishes an immutable revision", async ({ page }) => {
  await page.goto("/?view=chain-studio");

  await expect(page.getByRole("heading", { name: "予測Taskを固定したChainとして公開する" })).toBeVisible();
  const stageSelects = page.locator(".chain-studio-stages select");
  await expect(stageSelects).toHaveCount(2);
  await stageSelects.nth(0).selectOption("welding-consumable-stage-b-v1");
  await stageSelects.nth(1).selectOption("welding-consumable-stage-b-v1");
  await page.locator('input').nth(0).fill(`studio-e2e-${Date.now()}`);
  await page.locator('input').nth(1).fill("scalar Chain UI smoke");

  await expect(page.locator(".chain-studio-summary div").nth(1)).toContainText("candidateの既定namespace以外は拒否します");

  await page.getByRole("button", { name: "draftを検証" }).click();
  await expect(page.locator(".chain-studio-success")).toContainText("Task contract、binding、unit／basis、Package／Dataset固定を確認しました。");
  await page.getByRole("button", { name: "immutable Revisionを公開" }).click();
  await expect(page.locator(".chain-studio-success")).toContainText("r1 として公開しました");
  await expectNoBlockingAxeViolations(page);
});
