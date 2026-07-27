import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const surfaces = [
  {
    name: "プロジェクト概要",
    url: "/?view=project&project=default",
    ready: { role: "heading" as const, name: "焼鈍条件の候補検討" },
  },
  {
    name: "候補比較",
    url: "/?view=candidates&project=default",
    ready: { role: "heading" as const, name: /候補比較表/ },
  },
  {
    name: "データライブラリ",
    url: "/?view=data-library",
    ready: { role: "heading" as const, name: "データライブラリ" },
  },
];

for (const surface of surfaces) {
  test(`${surface.name}に重大なアクセシビリティ違反がない`, async ({
    page,
  }) => {
    await page.goto(surface.url);
    await expect(
      page.getByRole(surface.ready.role, { name: surface.ready.name }).first(),
    ).toBeVisible();

    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const blocking = result.violations.filter(
      (violation) => violation.impact === "serious"
        || violation.impact === "critical",
    );
    const diagnostics = blocking.map(({ id, impact, nodes }) => ({
      id,
      impact,
      nodes: nodes.map((node) => ({
        target: node.target,
        messages: node.any.map((check) => check.message),
      })),
    }));

    expect(diagnostics).toEqual([]);
  });
}
