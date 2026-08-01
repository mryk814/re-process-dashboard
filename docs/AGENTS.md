# docs 変更ルール

ルートの [`AGENTS.md`](../AGENTS.md) に加えて、文書変更には [`検証予算と停止条件`](operations/verification-budget.md) を適用します。

## pure docs

current contract、command、path、generated inventoryを変えない文章修正は`micro`または`local`です。

既定予算:

- 対象文書のlink／structure check
- diff check
- self-review

application build、full pytest、Playwright、independent reviewは実行しません。

## contractに接続するdocs

command一覧、navigation inventory、Task／Package inventory、generated OpenAPI、verification policy等の正本を変える場合は、対応する生成／drift checkだけを追加します。

教材edition、PDF全page、reference registry等の成果物を実際に変更する場合だけ、learning clean buildやvisual reviewへ昇格します。`docs/learning/**`というpathだけで、局所typoへrelease相当の作業を要求しません。

## stop

対象文書のlink、正本との整合、diffを一度確認したら止めます。同じ文書を複数の代理reviewで繰り返し確認しません。
