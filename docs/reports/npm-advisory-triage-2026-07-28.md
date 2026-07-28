# npm advisory解消記録 — 2026-07-29

## 結論

`npm audit --json`と`npm audit --omit=dev --json`はいずれも0件です。
期限付き例外としていた`GHSA-mh99-v99m-4gvg`と`GHSA-52cp-r559-cp3m`は削除しました。

## 依存元と対応

| 依存元 | 問題 | 対応 |
|---|---|---|
| `electron-builder@26.15.7` | 旧`@electron/asar`、`@electron/universal`、Squirrel peer、`ejs`経由で脆弱なglob/YAML依存へ到達 | 修正版を含む`27.0.0-alpha.6`へ更新。使っていないSquirrel peerは`.npmrc`の`omit=peer`で導入せず、実際に使うNSIS/DMG依存は直接依存のまま保持 |
| `ejs@3.1.10` | `jake@10`→`filelist@1`→旧`minimatch` | `jake@12.10.1`へ固定 |
| `openapi-typescript@7.13.0` | `@redocly/openapi-core@1.34.17`が`js-yaml@4.2.0`と旧`minimatch`を固定 | OpenAPI coreの公開API互換を保ち、内部依存だけ`js-yaml@4.3.0`と`minimatch@10.2.6`へ固定 |

`npm audit fix --force`が提示するElectron builder 25系へのdowngradeは採用していません。
Electron 43との配布経路を後退させず、修正版の依存木へ進めています。

## 監査policy

`scripts/check-npm-audit.mjs`は例外リストを廃止し、次の両方が0件でなければ失敗します。

- production dependency
- development dependencyを含む全依存

呼出元に`npm_config_omit=dev`が設定されていても全依存の監査を省略しません。

## 配布経路の追従

同梱サンプルを初期Workspaceへ全件入れない契約へ変わったため、packaged smokeは必要な4サンプルだけをSample Gallery APIで追加してから予測曲線を検証します。
初期画面を重く戻さず、folder版とinstaller版の配布検証を維持しています。

## 検証

- `npm run security:audit:test`: 3 adversarial cases passed
- `npm run security:audit`: production 0 / development 0
- `npm run api:check`: passed
- `npm run build`: passed
- `npm run package:windows`: folder ZIP、NSIS installer、portable/installed smoke、API lifecycle、Sample Gallery追加、4種の予測曲線、install/uninstall、user database保持を確認
- packaged portable launch to first usable: 27.8秒（sidecar初回起動を含む）
