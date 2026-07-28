# 検証ポリシー初回適用レポート（2026-07-28）

## 結論

最終の `npm run verify:pr` は、backend差分を検出したcommit `be47b1bc18f75bd046d4b76205e661d709b79be5` でfull pytestへ昇格し、295.616秒だった。
直近のRelease受入 `main-acceptance-2026-07-27.json` は998.265秒だったため、安全側へ昇格したPRでも待ち時間は702.649秒（70.4%、約3分の1）短くなった。
初期実装では31.111秒だったが、敵対的レビューでbackend変更時にもpytestを省略できる穴を検出したため、この値を最終効果としては採用しない。
これは重いgateを削除した結果ではなく、Release／Evidence checkpointへ移した結果である。

## 実行したLevel 1 gate

- docs link／placement check
- backend差分検出によるfull pytest
- Web unit test
- Desktop unit test
- generated contract／typecheck
- application build
- working tree／branch diff check

所要時間の内訳と実行コマンドは `artifacts/verification/latest-pr.json` に保存した。
focused pytestは対象pathを指定しなかったため `not_run` とし、代わりにfull pytestを自動選択した。
Playwright、migration、Windows配布、Compose、Shared Lab、教材clean build／visual reviewはLevel 1では `not_run` と記録した。

## 比較条件

比較元の `docs/reports/main-acceptance-2026-07-27.json` は、backend pytest、Web／Desktop unit、typecheck、build、Playwright、legacy Workspace、Windows installer／portable smokeなどを含むRelease相当の受入である。
用途が異なるため同一条件の性能比較ではない。
この差は「通常PRではRelease証拠を毎回再生成しない」という運用効果を示す。

## 次回以降の運用

- 通常PRはLevel 1を基本に、変更境界へ最も近いfocused testやfresh Playwrightだけを追加する。
- migration、backup／restore、active Package、security、distribution変更ではrisk matrixが要求するgateを省略しない。
- 複数PRをまとめた節目ではLevel 2を実行する。
- 実利用前、配布、schema／restore、Package、教材editionの節目ではLevel 3を実行する。
- 実行しないgateは成功扱いせず、`not_run` と理由を残す。
