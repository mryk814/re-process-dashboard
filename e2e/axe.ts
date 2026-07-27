import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

const acceptanceTags = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

export async function expectNoBlockingAxeViolations(
  page: Page,
  context: string,
) {
  const result = await new AxeBuilder({ page })
    .withTags(acceptanceTags)
    .analyze();
  const diagnostics = result.violations
    .filter((violation) => (
      violation.impact === "serious" || violation.impact === "critical"
    ))
    .map(({ id, impact, nodes }) => ({
      context,
      id,
      impact,
      nodes: nodes.map((node) => ({
        target: node.target,
        messages: node.any.map((check) => check.message),
      })),
    }));

  expect(diagnostics).toEqual([]);
}

export async function expectNoUndersizedText(page: Page) {
  // Inspect computed values so inherited styles and SVG scaling are covered.
  const tooSmall = await page.evaluate(() => {
    const results: string[] = [];
    for (const element of Array.from(document.querySelectorAll<HTMLElement>("*"))) {
      const text = Array.from(element.childNodes)
        .filter((node) => node.nodeType === Node.TEXT_NODE)
        .map((node) => node.textContent?.trim() ?? "")
        .join("");
      if (!text) continue;
      const style = window.getComputedStyle(element);
      if (
        style.display === "none"
        || style.visibility === "hidden"
        || element.getClientRects().length === 0
      ) continue;
      const size = Number.parseFloat(style.fontSize);
      // SVG axes may use 9px labels; all other readable text has a 10px floor.
      const floor = element.ownerSVGElement || element instanceof SVGElement ? 9 : 10;
      if (size < floor) {
        results.push(
          `${element.tagName.toLowerCase()}.${element.className} ${size}px: ${text.slice(0, 20)}`,
        );
      }
    }
    return results;
  });

  expect(tooSmall).toEqual([]);
}
