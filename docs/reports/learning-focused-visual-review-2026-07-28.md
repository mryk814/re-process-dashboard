# 教材追記ページ focused visual review（2026-07-28）

## 対象

- `docs/learning/_build/site/chapters/model-package-runtime.html`
- `docs/learning/_build/site/chapters/multi-stage-chain.html`

`docs/learning/build.ps1 -Clean`を完走させた生成物を、Playwright CLIで
1440×1000のdesktop viewportに表示して確認した。
全44 HTML文書と31 PDF入力のclean buildは成功している。

## 確認結果

### Model Package runtime

- `canonical-json/v1`と`canonical-json-finite-float15/v2`の役割が同じ節で対比される。
- v2の有限値、15有効桁、`-0.0`から`0`への正規化が折返しで分断されず読める。
- raw source、行、特徴量名・順序、Profile digestを丸めない境界と、新Package IDを発行する理由が続けて読める。
- 追記による横方向のoverflow、表・code linkとの重なり、見出し階層の崩れはない。

### Multi-stage chain

- commitするMAE／RMSEだけを12有効桁へ正規化する説明が表示される。
- source、Package、Profile digest、cohort、fold assignment、件数をexact照合する境界が同じ段落で読める。
- 前後の入れ子グループ別評価とtarget別cohortの説明から切断されていない。
- 追記による横方向のoverflow、code linkの欠落、段落の重なりはない。

## Browser evidence

- full-page capture:
  - `output/playwright/model-package-runtime-final.png`
  - `output/playwright/multi-stage-chain-final.png`
- focused capture:
  - Model Package runtime: 666×680、SHA-256 `789fd907c430b6789bb0154d1ac04fb54671012bab0c04d4d22007f586d56346`
  - Multi-stage chain: 666×587、SHA-256 `157b97673200f7696ec6b232b5b992b6c8976cd07766e0dfb0ed04b2e72681f6`
- Model Package runtimeで記録されたconsole errorは、教材本文と無関係な`/favicon.ico`の404だけだった。
- 本文の該当語をPlaywright snapshotで検索し、各1件に一意に一致した。

captureは検証時の一時成果物であり、正本はQMDと本レポートである。
既存ページ全体の直近証拠は、PR #432の全page contact-sheet reviewとbrowser interactionを継続参照する。
