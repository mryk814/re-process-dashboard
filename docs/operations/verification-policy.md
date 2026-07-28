# 検証gate運用

## 目的

検証件数を成果にせず、変更が壊し得る境界に最も近い証拠を、必要な時点で得ます。

普段の変更は短いloopで確認し、科学的意味、永続化、復旧、Package、配布へ影響する節目だけ重い受入検査を実行します。

gateのID、command、目的、概算時間、platform、risk category、生成する証拠の唯一の正本は [`scripts/verification-gates.json`](../../scripts/verification-gates.json) です。
この文書、README、AGENTS、CIへ個別gate一覧を複製しません。

```powershell
npm run verification:gates
node scripts/verify.mjs --list --json
```

## 4段階

### Level 0: Edit loop

実装中に、変更した契約へ最短でフィードバックします。

```powershell
npm.cmd run verify:edit -- backend/tests/test_screening_score.py
```

対象pytestと型・生成契約だけを確認します。
server再利用を含む反復結果は、merge時のfreshな証拠には使いません。

### Level 1: Normal change

通常のPRをレビュー可能にします。

```powershell
npm.cmd run verify:pr -- backend/tests/test_changed_contract.py
```

対象pytestを指定しなかった場合、runnerはそれを成功扱いせず`not_run`としてreportへ残します。
変更した画面、API、データ境界に追加のfocused testやfresh Playwrightが必要なら、PR本文へ別に記録します。

### Level 2: Main checkpoint

複数PRをまとめた節目や、共通境界を広く変更した時に実行します。

```powershell
npm run verify:checkpoint
```

通常のPRごとには要求しません。
依存監査、全backend test、unit test、型・生成契約、build、failure-state、文書、diffをまとめて確認するcheckpointです。

### Level 3: Release / Evidence checkpoint

実際に別PCへ配る場合、Workspace migration／restore、active Package、security、distributionなどの高リスク境界を変更した場合に実行します。

```powershell
npm run acceptance:release -- -ReportPath docs/reports/main-acceptance-YYYY-MM-DD.json
```

Windows配布を含むbaselineを実行し、JSON reportとartifact SHA-256を残します。
Compose、Shared Lab、教材clean buildなどは、変更riskに応じて`-IncludeGate`で追加します。
manual reviewは自動gateへ偽装せず、別の証拠として記録します。

## 変更risk

適用するgateの機械可読な対応は、正本catalogの`riskMatrix`を参照します。
判断の原則は次のとおりです。

| 変更 | 通常確認 | 節目またはreleaseで追加 |
|---|---|---|
| pure docs | link、配置、inventory | 教材editionならclean buildと全page確認 |
| frontend | Web unit、型、build、対象経路 | 全default Playwright |
| FastAPI contract | 対象pytest、API生成型 | failure-state |
| SQLite migration | migration fixture、restart | legacy Workspaceとrelease acceptance |
| backup／restore | focused restore | packaged restore |
| Model Package | builder／verify／smoke | degraded Chain、rollback、配布 |
| dependency／packaging | security audit、Desktop | Windows installer／portable |
| Compose／Shared Lab | static contract | isolated Docker integration |

未知の変更領域は軽いgateへ決め打ちせず、`partial`として追加判断を要求します。

## reportとstaleness

受入reportは、成功した時点の証拠であり、未来のcommitへ自動継承しません。

```powershell
npm run acceptance:status -- docs/reports/main-acceptance-YYYY-MM-DD.json
```

status checkerは次を区別します。

- `current`：tested commitと現在のcommitが同じ
- `still_applicable`：後続差分が受入reportなどの証拠だけ
- `stale`：実装、契約、文書、配布などのrisk差分がある
- `partial`：未知path、dirty worktree、履歴分岐などで証拠の適用範囲を確定できない
- `invalid`：gate catalog自体が変わり、当時の実行定義を現在へ適用できない

reportは展開後command、所要時間、`passed`／`failed`、選択しなかったgateの`not_run`と理由、catalog digestを保持します。
`skipped`、`blocked`、`not_run`を`passed`へ読み替えません。

## cadence

Level 2／3は固定した毎PRではなく、次の節目で実行します。

- schema migration、backup／restore変更後
- active Package更新後
- dependency／distribution変更後
- 大きな共通component／API変更後
- 教材editionを確定する時
- 実際に仕事で使う前
- 明示的なcheckpointを切る時

GitHub Actionsの利用可否にかかわらず同じrisk policyを使います。
Actionsが開始されない場合は、実行したローカルgateと不足する外部証拠をPRへ明記します。
