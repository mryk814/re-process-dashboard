# 検証ポリシー初回適用レポート（2026-07-28）

## 結論

通常PR向けの `npm run verify:pr` は、commit `bba2420afafc61d43e198b5a7f8fb7a3b125eb18` で 31.111 秒だった。
直近のRelease受入 `main-acceptance-2026-07-27.json` は 998.265 秒だったため、通常変更の待ち時間は 967.154 秒（96.9%、約32分の1）短くなった。
これは重いgateを削除した結果ではなく、Release／Evidence checkpointへ移した結果である。

## 実行したLevel 1 gate

- docs link／placement check
- Web unit test
- Desktop unit test
- generated contract／typecheck
- application build
- working tree／branch diff check

所要時間の内訳と実行コマンドは `artifacts/verification/latest-pr.json` に保存した。
focused pytestは対象pathを指定しなかったため `not_run`、full pytest、Playwright、migration、Windows配布、Compose、Shared Lab、教材clean build／visual reviewはLevel 1では `not_run` と記録した。

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
