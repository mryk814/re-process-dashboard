# npm依存関係監査 2026-07-27

## 判定範囲

2026年7月27日の`main`系統で、`npm audit --json`はHigh 18件、Critical 0件を報告した。
同じ出力をPowerShellの`Out-String`でUTF-8化したSHA-256は`482b602e419cdc67c53af6c01915bcdfd1032f43fab72568d26442c8fe565723`である。

18件は、二つの開発時入口から到達する推移依存である。
アプリのproduction dependencyから到達する経路はなかった。

| 入口 | 用途 | 主な報告対象 |
|---|---|---|
| `electron-builder@26.15.3` | Windows installerとフォルダZIPの生成 | `@electron/asar`、`@electron/universal`、`minimatch`、`brace-expansion`、`glob`、`rimraf`、`ejs`、`jake`、`temp` |
| `openapi-typescript@7.13.0` | リポジトリ内OpenAPIからTypeScript型を生成 | `@redocly/openapi-core@1.34.17`、`js-yaml`、`minimatch` |

## 更新判断

`openapi-typescript`は`7.9.1`から2026年7月27日時点の最新安定版`7.13.0`へ更新した。
API生成と生成差分検査を通して互換性を確認する。

`electron-builder`は同日時点の最新安定版`26.15.3`である。
`npm audit fix --force`は`25.1.8`へのmajor downgradeを提示するため採用しない。
この提案は脆弱な推移依存を除く更新ではなく、現在検証済みのpackaging toolchainを古い系列へ戻す操作だからである。

`@electron/asar`などをmajor overrideする案も採用しない。
親packageが宣言していないmajor版へ差し替えると、監査件数は減ってもpackaging APIの互換性を保証できない。

## 到達性と受容条件

現時点では18件をbuild-time riskとして受容する。
受容は無期限ではなく、次の条件に限る。

- packagingは、管理下のリポジトリにある設定、manifest、resourceだけを入力にする。
- OpenAPI型生成は、同じcheckoutでFastAPIから生成したJSONだけを入力にする。
- `package-lock.json`を固定し、CIとローカル検証で`npm install`による任意の版選択を行わない。
- 配布物にaudit対象packageがproduction runtimeとして同梱されていないことをWindows packaging smokeで確認する。
- 外部から受け取ったElectron Builder設定、EJS template、YAML、glob patternをこのtoolchainへ渡さない。

この受容は「DoS advisoryが誤検出である」という判断ではない。
攻撃者が入力を制御する経路を現在の製品と配布手順が持たないため、利用者が配布アプリを操作して到達する脆弱性としては扱わないという判断である。

## 再確認条件

次のいずれかが起きた時点で受容を解除し、再監査する。

- `electron-builder`が`26.15.3`より新しい安定版で`@electron/asar@4`以降を採用する。
- `openapi-typescript`が`@redocly/openapi-core@2`以降または修正版1.xを採用する。
- packagingまたはOpenAPI生成へ、利用者や外部サービスが作成した設定を渡す。
- advisoryの影響がDoS以外へ変更される。

依存版は月次またはpackaging変更時に`npm audit --json`と`npm outdated --json`で確認する。

## 検証記録

判定では次を実行した。

```powershell
npm run api:generate
npm run api:check
npm run verify:full
npm run package:windows
npm run smoke:packaged
npm audit --json
```

OpenAPI生成と生成差分検査は`openapi-typescript@7.13.0`で成功した。
Windows installerとfolder ZIPも生成できた。

最初のpackaging smokeは、検証scriptがLifecycle APIへ現在禁止されている`actor`を送ったため422で停止した。
これは[Issue #402](https://github.com/mryk814/re-process-dashboard/issues/402)として記録し、server-owned actor契約へ追従させた。
修正後はportable版とper-user installer版の双方で起動、1,000行Lifecycle、backup／restore、uninstallまで成功した。
最終ソースからのclean packaging、portable smoke、installer smoke、展開directory削除は404.5秒で完了した。

展開済み配布物には`node_modules` directoryがなく、上表のaudit対象package名もなかった。
Electron Builderのpackaging logも「no node modules returned」と記録した。
したがって、18件は配布アプリのproduction runtimeへ同梱されていない。

| 生成物 | byte | SHA-256 |
|---|---:|---|
| `Material-Decision-Workbench-Setup-0.1.0.exe` | 164,252,969 | `3F27451E2DAC69AAAD15CF8110102CFCD6D40B3EFB81C6346F49AD16BB17974A` |
| `Material-Decision-Workbench-folder-0.1.0.zip` | 223,100,803 | `D62F02295C800B09FB998F9354CDD2AC55D6936372B6083F90FBF67F66402574` |

`npm audit --json`の最終値はHigh 18件、Critical 0件である。
件数を偽って減らすoverrideは行わず、上記の到達性、入力制限、再確認条件を受容記録とする。
