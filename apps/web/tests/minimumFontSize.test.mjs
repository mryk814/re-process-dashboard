import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { globSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

const source = fileURLToPath(new URL("../src/", import.meta.url));
const files = globSync("**/*.css", { cwd: source });

/**
 * docs/product/design-system.md: 補助ラベルは11px以上、badgeは10px以上。SVGの軸目盛りなど
 * 面積制約が明確な要素だけを例外にする。例外はここへ理由付きで足す。
 */
const allowed = new Set([]);

function smallRules(file) {
  const text = readFileSync(new URL(`../src/${file}`, import.meta.url), "utf8");
  return text
    .split("\n")
    .map((line, index) => ({ line: line.trim(), number: index + 1 }))
    .flatMap(({ line, number }) => {
      const match = /font-size:\s*(\d+(?:\.\d+)?)(px|rem)/.exec(line);
      if (!match) return [];
      // ルート16px前提。remで書いても下限は同じ10px。
      const pixels = match[2] === "rem" ? Number(match[1]) * 16 : Number(match[1]);
      if (pixels >= 10) return [];
      return [`${file}:${number} ${line}`];
    })
    .filter((entry) => !allowed.has(entry));
}

test("no stylesheet renders text below the design system minimum", () => {
  const violations = files.flatMap(smallRules);
  assert.deepEqual(violations, []);
});

test("SVG text stays at the axis-tick exception floor, never below it", () => {
  const components = globSync("**/*.tsx", { cwd: source });
  const violations = components.flatMap((file) => {
    const text = readFileSync(new URL(`../src/${file}`, import.meta.url), "utf8");
    return [...text.matchAll(/fontSize="(\d+(?:\.\d+)?)"/g)]
      .filter((match) => Number(match[1]) < 9)
      .map((match) => `${file}: fontSize="${match[1]}"`);
  });
  assert.deepEqual(violations, []);
});

test("the out-of-range warning badge is not the smallest text on the screen", () => {
  const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
  const badge = /\.output-warning-badge\s*\{([^}]*)\}/.exec(styles);
  assert.ok(badge, ".output-warning-badge rule not found");
  const size = /font-size:\s*(\d+(?:\.\d+)?)px/.exec(badge[1]);
  assert.ok(size, ".output-warning-badge must declare a font-size");
  assert.ok(Number(size[1]) >= 11, `warning badge font-size is ${size[1]}px`);
  assert.match(badge[1], /font-weight:\s*(700|bold)/);
});
