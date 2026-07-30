import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  inspectJapaneseProse,
  visibleProse,
} from "./check-japanese-prose.mjs";

const stripped = visibleProse(`---
title: "uncertainty"
---

# 不確かさ

本文の uncertainty は検出する。
\`uncertainty\` はコード上の識別子。

\`\`\`python
uncertainty = 1
\`\`\`

$$
uncertainty
$$
`);
assert.match(stripped.map(({ text }) => text).join("\n"), /uncertainty/u);
assert.doesNotMatch(
  stripped.map(({ text }) => text).join("\n"),
  /uncertainty\s*=\s*1/u,
);

const root = fs.mkdtempSync(path.join(os.tmpdir(), "japanese-prose-"));
try {
  fs.mkdirSync(path.join(root, "chapters"), { recursive: true });
  fs.writeFileSync(
    path.join(root, "chapters", "sample.qmd"),
    "# 見出し\n\nquantile と uncertainty を確認する。\n",
  );
  const prohibited = inspectJapaneseProse({
    learningRoot: root,
  });
  assert.equal(prohibited.errors.length, 2);
  assert.match(prohibited.errors.join("\n"), /quantile/u);
  assert.match(prohibited.errors.join("\n"), /uncertainty/u);

  fs.writeFileSync(
    path.join(root, "chapters", "sample.qmd"),
    "# 見出し\n\n既存の API に novelword を追加する。\n",
  );
  const unknownWord = inspectJapaneseProse({ learningRoot: root });
  assert.deepEqual(unknownWord.errors, []);
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}

console.log("日本語本文検査のfixture testに成功しました。");
