# npm advisory triage — 2026-07-28

## 結論

`npm audit --omit=dev --json`は0件です。
現行lockfileのproduction dependencyには既知advisoryが報告されていません。

通常の`npm audit --json`に残るhighは、開発時のElectron packagingとOpenAPI型生成が使うglob処理とYAML parserへ集約されます。
既知の直接advisoryは`GHSA-mh99-v99m-4gvg`と`GHSA-52cp-r559-cp3m`です。

## 実施した更新

- `electron-builder`、`app-builder-lib`、`dmg-builder`、Squirrel builderを26.15.7へ更新
- `minimatch` 10系を10.2.6、`brace-expansion` 5系を5.0.8へ更新
- `tar`、`sax`、`undici`、`fs-extra`をlockfile上の修正版へ更新

## 残る例外

| 項目 | 内容 |
|---|---|
| advisory | `GHSA-mh99-v99m-4gvg` / `GHSA-52cp-r559-cp3m` |
| 影響 | crafted glob patternによるprocess memory exhaustion / YAML merge keyのquadratic blowup |
| 到達経路 | Electron packaging、OpenAPI型生成などdevelopment dependencyのみ |
| 現行入力 | repositoryで管理するpath、pattern、package設定 |
| production audit | 0件 |
| owner | repository maintainers |
| 期限 | 2026-08-31 |
| 解消条件 | `electron-builder` / `openapi-typescript`の依存更新で旧minimatch系を除去できること |

旧minimatchを10.2.6へ一律overrideしません。
3系から10系ではexportとNode要件が変わり、packagerが期待するAPIを壊し得るためです。
`@redocly/openapi-core`が固定する`js-yaml` 4.2.0も一律overrideしません。
OpenAPI生成時に読むのはrepository管理下のschemaであり、修正版を要求できる上流更新まで期限付き例外として追跡します。
`electron-builder`を25系または22系へdowngradeする`npm audit fix --force`も、現行Electron 43 packagingとの組合せを後退させるため採用しません。

## 再検出

`npm run security:audit`は次を検査します。

- production dependencyのadvisoryが0件
- 呼出元のnpm設定にかかわらずdevelopment dependencyを監査対象に含める
- residual advisoryが上記二件だけ
- 影響package集合が既知のdevelopment-only集合と一致
- criticalまたは新規advisoryをallow-listしない
- 期限超過後は必ず失敗する

dependency treeが改善して0件になった場合も成功し、例外削除を促します。
package集合またはadvisoryが変化した場合は、件数が減っていても再triageを要求します。

## 検証

- clean `npm ci`後のinstalled tree: `electron-builder` / `app-builder-lib` / `dmg-builder` 26.15.7
- `npm run security:audit:test`: 6 adversarial cases passed
- `npm_config_omit=dev`を設定した呼出しでも18 development-only recordsを検査
- `uv run python -m pytest`: 951 passed, 4 skipped
- `npm run typecheck`: passed
- `npm run build`: passed
- `npm run package:windows`: folder ZIPとinstallerの起動、API lifecycle、終了、install/uninstall、user database保持を確認
- adversarial review: blocking finding 0
