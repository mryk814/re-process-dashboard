# domain-neutralな製品境界と利用者向け名称

| 項目 | 内容 |
|---|---|
| 状態 | 決定済み |
| 記録日 | 2026-07-31 |
| 対象 | 製品名、Workbench Core、domain capability、Task／example、配布identity |
| 追跡 | [#543](https://github.com/mryk814/re-process-dashboard/issues/543) |

## 決定

利用者向け製品名を **Evidence Decision Workbench** とする。
短縮名は **Decision Workbench**、日本語では **判断根拠ワークベンチ** と表記する。

製品核は材料専用ではない。
候補、予測、支持範囲、類似実績、実測、検討Runを同じProject文脈で比較し、
判断時点の根拠を再現可能に保存する研究開発向けのローカルアプリである。

材料・製造は最初のdomainであり、同梱Task、教材、科学的制約から材料語彙を
消さない。
製品境界は次の三層として扱う。

```text
Workbench Core
  Project、Dataset／Profile、Task、Package、Candidate、
  Prediction／Support、Design Space／Objective、Run、Snapshot、Actual

Domain capability
  composition、heat program、sparse blend、材料lineage、
  welding Chainなどの型付き・allow-list済み能力

Task／Example
  焼鈍、熱延、溶接、工具摩耗、電池、工程異常等の具体的な縦スライス
```

任意pluginや万能domain schemaは導入しない。
Coreは候補shapeを推測せず、Task contractとallow-list済みadapterが宣言した能力を
Project単位で解決する。

## inventoryと分類

### 利用者向けにrenameする

- application／window／browser title、UI header
- installer、実行ファイル、shortcut、portableの表示名
- FastAPIの公開title
- README、アプリ憲章、現行システム基準、デザインシステム等の現行product docs
- Workspace backupのfile dialog表示名

過去時点の採否を記録するADR、report、acceptance evidenceは機械置換しない。

### Workbench Coreとして維持する

- Dataset Asset、Profile Revision、Dataset Revision、Dataset View Revision
- Prediction Task、Feature Pipeline、Model Package、allow-list済みruntime
- Project、Candidate／Revision、Prediction／Support、Similarity
- Project Design Space、Objective Definition、Proposal、Decision Activity
- Screening／Activity／Prediction Run、Snapshot、Actual、Decision evidence
- Source Lifecycle、Workspace backup／restore

### domain-specificとして維持する

- composition total、mass%／at%
- heat program／heat pattern
- sparse blend、科学master、商用catalog、配合LP／MILP
- Welding Chain A→B→Cと専用Workbench
- 材料向けWorkbook／Observation Profile、lineage、画像証拠
- 材料・製造Task、source data、Model Package、教材

これらはCoreから存在を隠す対象ではない。
Taskまたはdomain capabilityとして明示し、対応しないProjectへ表示しない。

### legacy implementation identityとして維持する

次は利用者向けブランドではなく、import、保存、更新、復元の安定identityである。
製品名変更だけを理由にrenameしない。

- repository名 `re-process-dashboard`
- Python namespace `material_workbench`
- root package名 `material-decision-workbench`
- npm scope `@material-workbench/*`
- Electron app ID `jp.local.material-decision-workbench`
- sidecar名 `material-workbench-sidecar`
- `MATERIAL_WORKBENCH_*` 環境変数
- localStorage key
- schema ID、Task ID、Package ID
- Workspace bundle拡張子 `.mdwb`
- installer版の既定user data path `%LOCALAPPDATA%\Material Decision Workbench`

新しい表示名を理由に、既存identityへ別名fallbackや二重schemaを追加しない。
将来内部identityを変える場合は、利用者向けrenameとは別のmigrationとして扱う。

## 現在の型付き境界

`canonical-candidate/v1` は現在のscalar／材料互換Candidate familyである。
`composition`、`process`、`categorical`、`heat_pattern` を固定shapeとして持つため、
非材料Taskは不要なgroupを空またはnullで保持する。
これはすべてのdomainに通用する万能Candidate schemaではない。

新しい候補shapeは、保存、差分、copy、snapshot、Task validationの意味を持つ
型付きfamilyとして追加する。
今回のrenameで `composition` や `heat_pattern` を曖昧なJSONへ置き換えない。

Chain CoreはStage順序、binding、単位変換、部分再計算、鮮度、provenance、
snapshotを扱う。
候補shape、初期値、妥当性検証、追加revision参照はallow-list済み
candidate adapterへ置く。
現行の`sparse_blend/v1`と材料成分を使うactual-conditioned分析は
domain capabilityであり、Core一般能力とは呼ばない。

## 互換方針

| 対象 | 方針 |
|---|---|
| Workspace DB／migration／Project ID | 変更しない |
| `.mdwb` bundle | schema、拡張子、内容identityを変更しない |
| Task／Package／Profile／Dataset | ID、digest、schemaを変更しない |
| API | pathとpayloadを維持し、OpenAPIの表示titleだけ変更する |
| deep link | 現行のquery parameterを維持する。custom protocolは追加しない |
| Electron app ID | 維持し、既存installerの更新identityを保つ |
| user data | 旧 `%LOCALAPPDATA%\Material Decision Workbench` を明示的に使い続ける |
| localStorage | 既存keyを維持してlayout設定を失わない |
| installer／exe／shortcut | Evidence Decision Workbenchへrenameする |
| portable | 表示名をrenameし、同梱`user-data`の配置は維持する |

配布名の変更では、clean installだけでなく旧版からのupgrade、既存DBの保持、
backup／restoreをpackaged smokeで確認する。

## 非材料journeyによる反証

工具摩耗Task `flank-wear-v1` を非材料journeyの代表とする。
Dataset／Profile／Package、Project、Candidate比較、範囲探索、Activity、
Snapshot、Actual、判断履歴、backup／restoreを通し、Core画面へ材料語彙が
漏れていないことを確認する。

工具摩耗固有の切削条件と測定値はTask語彙であり、漏れではない。
共通header、共通説明、共通model入力surfaceに現れる材料語彙だけを
generic UI leakとして扱う。

## 採用しなかった案

- **Material Decision Workbenchを維持**: 実装済みの非材料TaskとCoreの性格を
  利用者へ誤って伝える。
- **Decision Workbenchのみ**: 呼びやすいが一般名に近く、製品を識別しにくい。
- **Process Decision Workbench**: 工程以外の候補判断を狭める。
- **Model-Assisted Decision Workbench**: human-in-the-loopは伝わるが、
  実測、類似実績、判断履歴という製品の中心をモデルの補助に見せすぎる。
- **repository／Python namespaceも同時rename**: 利用者価値に比べてimport、
  packaging、診断、履歴への破壊範囲が大きい。
