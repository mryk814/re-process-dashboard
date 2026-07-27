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

for (const surface of surfaces) {
  test(`${surface.name}に9px未満の文字がない`, async ({ page }) => {
    await page.goto(surface.url);
    await expect(
      page.getByRole(surface.ready.role, { name: surface.ready.name }).first(),
    ).toBeVisible();

    // 宣言値ではなく計算値で見る。継承やSVGのスケールで小さくなる場合も拾う。
    const tooSmall = await page.evaluate(() => {
      const results: string[] = [];
      for (const element of Array.from(document.querySelectorAll<HTMLElement>("*"))) {
        const text = Array.from(element.childNodes)
          .filter((node) => node.nodeType === Node.TEXT_NODE)
          .map((node) => node.textContent?.trim() ?? "")
          .join("");
        if (!text) continue;
        const style = window.getComputedStyle(element);
        if (style.display === "none" || style.visibility === "hidden") continue;
        const size = Number.parseFloat(style.fontSize);
        // SVG内textは軸目盛りの例外として9pxまで許す。
        const floor = element.ownerSVGElement || element instanceof SVGElement ? 9 : 10;
        if (size < floor) {
          results.push(`${element.tagName.toLowerCase()}.${element.className} ${size}px: ${text.slice(0, 20)}`);
        }
      }
      return results;
    });

    expect(tooSmall).toEqual([]);
  });
}
