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
    "# 見出し\n\n既存のAPIに新しい値を追加する。\n",
  );
  const unknownWord = inspectJapaneseProse({ learningRoot: root });
  assert.deepEqual(unknownWord.errors, []);

  fs.writeFileSync(
    path.join(root, "chapters", "sample.qmd"),
    "# 見出し\n\n裸の checkpoint を本文へ置かず、`checkpoint`という識別子として示す。\n",
  );
  const bareEnglishWord = inspectJapaneseProse({ learningRoot: root });
  assert.equal(bareEnglishWord.errors.length, 1);
  assert.match(bareEnglishWord.errors[0], /checkpoint/u);

  fs.writeFileSync(
    path.join(root, "index.qmd"),
    "# はじめに\n\n本文の robustness は検出する。\n",
  );
  const rootManuscript = inspectJapaneseProse({ learningRoot: root });
  assert.match(rootManuscript.errors.join("\n"), /index\.qmd:3/u);

  fs.writeFileSync(
    path.join(root, "references.qmd"),
    "# 参考文献\n\n**Example, *Robustness in Practice* [@example]**\n",
  );
  const originalTitle = inspectJapaneseProse({ learningRoot: root });
  assert.doesNotMatch(originalTitle.errors.join("\n"), /references\.qmd/u);

  fs.writeFileSync(
    path.join(root, "glossary.qmd"),
    "# 用語集\n\n### 頑健性（robustness）\n\nrobustnessを評価する。\n",
  );
  const generatedGlossary = inspectJapaneseProse({ learningRoot: root });
  assert.match(generatedGlossary.errors.join("\n"), /glossary\.qmd:5/u);

  fs.writeFileSync(
    path.join(root, "glossary.qmd"),
    "# 用語集\n\n### 頑健性（robustness）\n\n**別名と検索語**：robust analysis\n",
  );
  const glossarySearchTerms = inspectJapaneseProse({ learningRoot: root });
  assert.doesNotMatch(glossarySearchTerms.errors.join("\n"), /glossary\.qmd/u);
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}

console.log("日本語本文検査のfixture testに成功しました。");
