# AGENTS.md

Evidence Decision Workbench（判断根拠ワークベンチ）は、入力候補、予測、実測、不確かさ、支持範囲、provenanceを分離して科学的な意思決定を支援するローカルアプリです。

- `backend/` — FastAPI、domain、persistence、model runtime
- `apps/web/` — React + TypeScript + Vite UI
- `apps/desktop/` — Electron shell
- `models/packages/` — safeなdata-only Model Package
- `data/source/` — 読取専用の元データ正本
- `docs/` — product、contract、decision、operationの正本

## 最初に作業modeを決める

### Data use

既存のTask、Profile family、Model Runtimeを使い、外部Excel／CSVを登録、学習、Project利用する作業です。

入口は[自分のデータで使い始める](docs/operations/data-contributor-start-here.md)です。必要なら[`data-contributor`](.agents/skills/data-contributor/SKILL.md)を使います。

このmodeでは、既存のProfile検証、`model:diagnose`、Package build内の契約検証、実Projectでのsmokeを使います。アプリコード、追跡済みProfile、同梱Dataset／Package、TaskDefinition、API、UI、migrationを変え始めた時点でApp developmentへ移ります。

既定ではIssue、branch、PR、アプリ向けunit test／E2E、全体gateを要求しません。

### App development

アプリ本体、contract、共通tooling、同梱contentを変更する作業です。

入口は[Developer Start Here](docs/developer-start-here.md)です。日常作業の短い進め方とGPT-5.6向けprofile例は[Agent throughput guide](docs/developer/agent-throughput.md)を参照します。

- `answer`、`explain`、`review`、`diagnose`、`plan`では、関連箇所を確認して報告します。明示されない限り実装しません。
- `change`、`build`、`fix`、`implement`では、scope内の変更、依頼されたrepository内のbranch／commit／PR、非破壊的な直接検証まで進めます。
- merge、deploy、release、resource削除、外部message、秘密情報の送信、費用発生、scopeの大幅拡張は確認を取ります。

## 読む範囲

最初に読むのは次だけです。

1. 利用者の依頼と関連Issue
2. 変更対象の正本またはowner
3. 対象コード
4. 最寄りのtest
5. 対象directoryまでの最寄り`AGENTS.md`

全docs、全Issue、全Skill、repository全体を先に棚卸ししません。追加authorityへ実際に到達した時だけ読込範囲を広げます。

## Fast path

`micro`／`local`変更は、次を既定とします。

1. 一つの変更authorityを特定する
2. 最小の完全な差分を実装する
3. 最も近いtestまたはstatic checkを一度実行する
4. diffを確認する
5. 新しい原因仮説がなければ止める

`micro`／`local`では原則として次を行いません。

- written verification budgetの作成
- subagentへの委任
- repository全体のarchitecture／UX監査
- full pytest、default Playwright、checkpoint、release acceptance
- independent-adversarial review
- 同一commit・同一input・同一environmentの同じgateの再実行

browser behaviorを変えた場合だけ、そのinteractionを反証する対象journeyをfresh環境で一度確認します。

詳細な検証規約は[検証予算と停止条件](docs/operations/verification-budget.md)と[検証gate運用](docs/operations/verification-policy.md)を正本とします。

## 昇格条件

次のいずれかを確認した場合だけ`structural`／`critical`へ昇格します。

- 複数authorityを実際にまたいだ
- public API、persistence、migration、restore、securityを変更する
- Model Package artifact／runtime semanticsを変更する
- Project、Run、Snapshot等のpersisted scientific identityを変更する
- 最初の原因仮説が外れた、再現が不安定、または再発している
- focused evidenceでは共有stateを観測できない
- 利用者の科学判断を誤らせる新しいriskが見つかった

`structural`／`critical`では、[`verification-budget-planner`](.agents/skills/verification-budget-planner/SKILL.md)と必要なrepo Skillを使います。

## Skillを使う条件

- [`systematic-debugging`](.agents/skills/systematic-debugging/SKILL.md) — 原因layerが不明、候補が複数、再現不安定、再発、または最初の局所仮説が外れた時。明白な一authority修正ではfast pathを使う。
- [`frontend-ux-architect`](.agents/skills/frontend-ux-architect/SKILL.md) — screen、navigation、form構造、情報順序、主要handoffを変える時。typo、token置換、既存構造内の局所表示修正では使わない。
- [`re-process-architecture-review`](.agents/skills/re-process-architecture-review/SKILL.md) — package authority、registry、adapter、transaction、migration、dependency directionを変える時。通常のfeature追加や行数だけの分割では使わない。
- [`scenario-journey-evaluator`](.agents/skills/scenario-journey-evaluator/SKILL.md) — 複数画面の判断journeyを実Actorとして評価する時。

外部Skillの固定版と安全境界は[Agent Skills inventory](docs/developer/agent-skills-inventory.md)を正本とします。

## Non-negotiable invariants

- `data/source/`を変更しない。
- Model Packageからarbitrary Python code、pickle、joblibを読み込まない。新runtimeはallow-list adapterとして追加する。
- 保存済みProject、Package、Run、Snapshot、prediction identityを黙って変更・再計算しない。
- prediction、actual、uncertainty、support、provenanceを同じ意味として扱わない。
- Task ID、材料名、元データ列名、model class名による中央分岐を増やさない。
- typed error、stale-response rejection、transaction owner、failure containmentをfallbackやretryで隠さない。
- compatibility shim、旧経路、並行V2実装を理由なく長期並存させない。
- testは科学的誤判断、データ破損、再現性崩壊、復旧不能、accessibility阻害、実際の回帰を防ぐものへ絞る。

## Scoped authorities

- [backend rules](backend/AGENTS.md)
- [Web rules](apps/web/AGENTS.md)
- [Web implementation rules](apps/web/src/AGENTS.md)
- [Web test rules](apps/web/tests/AGENTS.md)
- [E2E rules](e2e/AGENTS.md)
- [docs rules](docs/AGENTS.md)
- [tooling rules](scripts/AGENTS.md)

## Stop rule

変更したbehavior、contract、state transitionが現在commitで一度証明され、diffがscope内で、新しい未検証仮説が残っていなければ止めます。

上位gateが下位testを包含して成功した後、その下位testを証拠目的に再実行しません。CIが同一commitのfull-suite ownerならlocal full suiteを重複実行しません。未実行gateは`not_run`として正確に記録します。