# 検証gate運用

## 目的

検証件数を成果にせず、変更が壊し得る境界に最も近い証拠を、必要な時点で得ます。

普段の変更は短いloopで確認し、科学的意味、永続化、復旧、Package、配布へ影響する節目だけ重い受入検査を実行します。

個別作業で何を実行し、何を実行せず、いつ止めるかは [`検証予算と停止条件`](verification-budget.md) を正本とします。

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

Level 0は編集、commit、handoffごとの義務ではありません。意味のある一単位を進め、その単位を反証できるfocused testを一度実行します。

### Level 1: Normal change

通常のPRをレビュー可能にします。

```powershell
npm.cmd run verify:pr -- backend/tests/test_changed_contract.py
```

対象pytestを指定しなかった場合、runnerはそれを成功扱いせず`not_run`としてreportへ残します。
変更した画面、API、データ境界に追加のfocused testやfresh Playwrightが必要なら、検証予算へ明示してPR本文へ記録します。

`micro`変更や、aggregate runnerより直接gateの方が明確な変更では、`verify:pr`を実行せず、直接証拠と未実行理由を記録して構いません。

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

適用するgateの機械可読な対応は、正本catalogの`riskMatrix`と`planning`を参照します。
判断の原則は次のとおりです。

| 変更 | 通常確認 | 節目またはreleaseで追加 |
|---|---|---|
| pure docs | link、配置、inventory | 教材editionならclean buildと全page確認 |
| frontend | Web unit、型、対象経路 | 共通flow変更時だけ広いPlaywright |
| FastAPI contract | 対象pytest、API生成型 | failure-state |
| SQLite migration | migration fixture、restart | legacy Workspaceとrelease acceptance |
| backup／restore | focused restore | packaged restore |
| Model Package | builder／verify／smoke | degraded Chain、rollback、配布 |
| application security | local access／Origin／trust-boundary test | dependency auditとfresh browser |
| dependency／packaging | security audit、Desktop | Windows installer／portable |
| Compose／Shared Lab | static contract | isolated Docker integration |

pathは候補分類であり、最終判断ではありません。実際に変更するartifact／authority／failure boundaryから検証予算を決めます。

## verification plan

`verify:pr -- --plan --json`は、changed paths、risk categories、focused tests、selected／skipped gatesと理由、full suite ownerを表示します。

planは実行候補を示すplannerであり、作業者の検証予算を置き換えません。

- path classifierが過大判定した場合は、実際の変更境界と直接証拠を記録する
- plannerを緑にする目的だけにcheckpoint／releaseを実行しない
- unknown pathは、まず短いauthority調査を行い、それでも分類不能な場合だけ広いsuiteへ進む
- backend変更でfocused pathを特定できない場合、即座に`backend/tests`全体へ進む前に、nearest owner／test／contractを確認する

planの`selectedLevel`は横断証拠を含む推奨level、`executionLevel`は今実行するrunner levelです。

現行runnerは後者が前者より低い場合に`incomplete`と非ゼロ終了を返します。この状態は、当該PRの直接証拠が失敗したことを必ずしも意味しません。検証予算により、次を区別して記録します。

- `failed`: 当該変更の直接証拠が失敗した
- `passed`: 当該変更の直接証拠が揃った
- `passed_with_follow_up`: 直接証拠は揃い、横断checkpointを後で実行する
- `blocked`: merge前にcompatibility／release証拠が必須

`migration`等の語を含むpathでも、保存形式や既存recordを実際に変更していなければ、自動的に`blocked`とはしません。

手動でriskを上乗せする場合は理由を必ず残します。

```powershell
npm.cmd run verify:pr -- --plan --json
npm.cmd run verify:pr -- --risk security --reason "Origin boundary is changed by generated integration" -- backend/tests/test_api.py
```

## 検証予算と重複防止

作業開始時に、次を短く宣言します。

```text
change class / authority / expected scope
selected gates / not planned
review mode / escalation triggers / stop condition
```

次を繰り返しません。

- 同一commit、同一input、同一environmentの同じgate
- 上位gateに包含されて成功した下位test
- fresh E2Eで証明済みの同じjourneyを別runnerで再実行
- CIが同一commitで所有するfull suiteのlocal再実行
- docs／PR本文だけの変更後のapplication全再検証

直接focused testを反復に使い、aggregate gateを最終集約に使う場合、それぞれ一度ずつで十分です。

## review

reviewもriskに応じて予算化します。

- `self-review`: micro／local、一つのauthority、identity／securityへ影響しない
- `focused-peer`: structural UI／API／package boundary、共有state owner
- `independent-adversarial`: migration／restore、security／artifact loader、scientific identity、model semantics、複数authority

実装者と異なるreviewを全PRへ儀式として要求しません。

## unrelated failure

検証中の別failureは、変更前再現、変更authority、現在の完了証拠への影響を確認します。

無関係なら事実を記録し、現在作業を広げません。現在の証拠を妨げる場合だけ、予算を更新します。

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
- `invalid`：report自体がfailed、またはgate catalogが変わり、当時の実行定義を現在へ適用できない

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
