# 変更作業の検証予算と停止条件

この文書は、変更の正しさを保ちながら、検証・レビュー・調査を不安から無制限に積み増さないための作業規約です。

[`verification-policy.md`](verification-policy.md) は利用可能なgateとrisk分類を定めます。この文書は、個別作業で**どこまで実行し、何を実行せず、いつ止めるか**を定めます。

## 基本原則

検証量そのものは成果ではありません。

> 変更が壊し得る最も近い境界を一度反証し、必要十分な証拠が得られたら止める。

次を区別します。

- 当該変更の直接証拠
- 同じcommitで既に得られた包含証拠
- 後日のcheckpoint／releaseでまとめて得る横断証拠
- 今回の変更と無関係な失敗
- 不安を減らすだけで、新しい仮説を検証しない反復

後日のcheckpoint証拠が残っていることと、当該PRの実装が失敗していることを同一視しません。

## 作業開始時に検証予算を宣言する

実装、調査、文書変更を始める前に、次の短い予算を作業記録またはPR本文へ残します。小さな変更では数行で構いません。

```yaml
change_class: micro | local | structural | critical
authority: 変更する正本または責務
expected_scope: 想定file／component／contract
verification_budget:
  - 実行するfocused gate
not_planned:
  - 実行しないfull suite／E2E／review
review: self | focused-peer | independent-adversarial
escalation_triggers:
  - 予算を広げる条件
stop_condition:
  - 十分な証拠が得られた状態
```

例:

```yaml
change_class: local
authority: Data Libraryのresource状態表示
expected_scope: React component 2件、unit test 1件
verification_budget:
  - 変更したstate reducerのunit test
  - interactionを変えた場合だけ対象Playwright 1本
  - diff check
not_planned:
  - full pytest
  - default Playwright
  - checkpoint
  - independent review
review: self
escalation_triggers:
  - API response contractも不一致だった
  - stale response identityへ影響した
stop_condition:
  - partial failureとretryが同一commitで一度証明された
```

## 変更class

### micro

意味境界を変えない局所変更です。

例:

- typo
- 文言の誤り
- docs link
- 既存tokenへのCSS置換
- 明らかなlabel／scope／aria属性の欠落
- test expectationだけの更新

既定予算:

- 最も近い直接checkを一つ
- diff check
- self-review

通常不要:

- aggregate `verify:pr`
- full suite
- browser全体
- independent review
- Architecture／UX Skillのfull workflow

### local

一つのauthority内で、挙動または表示を小さく変更します。

例:

- 一つのserviceのbug fix
- 一つのReact state transition
- 既存contract内のfield validation
- 一つのAPI error mapping

既定予算:

- focused unit／pytest
- interactionを変えた場合だけ対象E2E
- 必要な型または生成contract check
- diff check
- self-review

### structural

一つの利用者flowまたは責務境界を複数fileで変更します。

例:

- onboarding flow
- navigation state
- application package境界
- adapter／registry追加
- 共通componentの情報構造

既定予算:

- 変更したauthorityのfocused tests
- 対象journeyのE2Eを一つ以上
- relevant type／contract／architecture guard
- Level 1 plan
- riskに応じたfocused-peer review

default Playwright、full pytest、checkpointは自動では追加しません。複数authorityをまたぐか、checkpoint triggerに一致する場合だけ追加します。

### critical

失敗時に保存データ、security、配布物、科学identityまたは復旧可能性を壊し得る変更です。

例:

- migration／persisted schema
- backup／restore
- artifact loader／hash／path／serialization
- security trust boundary
- installer／upgrade
- Project／Run／Snapshot identity

既定予算:

- focused proof
- compatibility／restart／rollback proof
- independent-adversarial review
- checkpointまたはrelease evidence

criticalはfile pathだけで決めません。実際に上記artifactまたはboundaryを変更したかを確認します。

## Skillの段階

### debugging

- `quick`: 症状が再現し、原因が一つのlayerにあり、最初の仮説を直接検証できる
- `standard`: 原因候補が複数、またはUI／API／persistence等の境界を一度またぐ
- `deep`: 再現困難、複数layer、race／environment、再発、約3回の局所修正失敗

最初は最小のmodeから開始し、昇格条件を満たした場合だけ広げます。

### frontend UX

- `local`: 情報順序を変えない局所表示・interaction修正
- `structural`: screen／form／navigation／handoffの構造変更
- `journey`: 複数画面の判断flow、resume、failure recoveryを含む変更

すべてのUI変更に13項目のbriefや全状態のActor journeyを要求しません。

### architecture

- `local-boundary`: 既存authority内の小さな責務移動
- `structural`: package／registry／adapter／transaction boundary変更
- `audit`: 実装なしの棚卸し

ファイル行数やAIの読みやすさだけではmodeを昇格しません。

## 検証を広げる条件

次のいずれかが起きた場合だけ、予算を更新して広げます。

- 最初の原因仮説が外れた
- 変更が想定外のauthorityへ波及した
- API／persistence／scientific identityが追加で変わった
- focused testでは観測できない共有状態が見つかった
- 過去に同じ回帰が再発している
- security、migration、restore、artifact safetyへ到達した
- 利用者が誤った判断をする危険が新たに確認された
- CIまたは既存testが、変更と因果関係のある別の失敗を示した

予算を広げるときは、追加gateと理由を一行で記録します。

## 停止条件

次を満たしたら、その作業の検証を止めます。

- 変更したbehavior／contract／state transitionが現在commitで一度証明された
- 直接影響するerror／recovery pathが必要範囲で確認された
- diffが想定scope内にある
- 新しい未検証仮説が残っていない
- critical boundaryを変更した場合は必要なcompatibility証拠がある

次は停止を妨げません。

- 後日のcheckpointで横断確認する項目が残っている
- CIが同一commitのfull-suite ownerである
- 変更と無関係な既知failureがある
- 実行しなかったgateが`not_run`として正確に記録されている

## 重複実行を避ける

- 上位gateが下位testを包含して成功した後、同じcommitで下位testを証拠目的に再実行しない
- 同じcommitへ同じgateを、入力・仮説・環境を変えずに繰り返さない
- fresh E2Eが成功した後、同じjourneyを別runnerで重複確認しない
- CIが同一commitでfull suiteを完走した場合、local full suiteを再実行しない
- review後にdocsまたはPR本文だけが変わった場合、application testを最初からやり直さない
- direct focused testとaggregate runnerの両方を使う場合、前者は反復、後者は最終集約として一度ずつにする

## unrelated failure

検証中に別のfailureを見つけた場合、次を確認します。

1. 変更前にも再現するか
2. 変更したauthorityを通るか
3. 当該変更の完了証拠を妨げるか

無関係なら、事実を記録して現在作業を広げません。修正が必要なら別作業として扱います。

## review予算

### self-reviewで十分

- micro／local変更
- 一つのauthority内
- persisted identity、security、migrationを変えない
- 利用者の科学判断を変えない

### focused-peer review

- structural UI／API／package境界
- 複数componentのstate owner変更
- 新しいtyped adapter／registry
- test infrastructureの共有owner変更

### independent-adversarial review

- migration／restore
- security／artifact loader
- persisted scientific identity
- model runtime semantics
- 利用者の判断を誤らせ得る表示契約
- 複数authorityを横断するarchitecture変更

レビューはriskを検査するために行います。全PRへ儀式として追加しません。

## verification plannerとの関係

`verify:pr -- --plan --json`は候補gateを示すplannerです。path classifierの結果を、実際の変更境界より優先しません。

- over-classificationを確認した場合、実際のboundary、直接証拠、延期するcheckpointを記録する
- plannerの`requiredFollowUp`は横断証拠の予定であり、当該PRの直接証拠が失敗したことを意味しない
- release artifactを実際に変更していないPRで、plannerを緑にする目的だけにrelease gateを実行しない
- unknown pathでは、短いauthority調査を先に行い、それでも分類不能な場合だけ広いsuiteへ進む

未実行gateを成功扱いしません。一方、未実行であることだけを理由に、必要十分な直接証拠を持つ変更を無期限に止めません。
