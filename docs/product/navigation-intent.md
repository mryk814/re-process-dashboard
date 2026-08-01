<!--
document-status: current
verified-commit: 50e403c697910b699a95cf7aa3082baec30a8b42
owner: web navigation
source-of-truth: URL, history, and NavigationIntent semantics
-->

<!-- current-contract:navigation-views:project,project-settings,candidates,candidate-review,workspace,quality,lineage,explore,data-library,profile-workbench -->
<!-- current-contract:navigation-query:activity,activity_run,admin,base_dataset,candidate,candidate_section,connector,developer_guide,developer_tab,entity,onboarding,project,project_settings,quality_issue,quality_key,quality_sheet,quality_type,revision,screening,snapshot,stage,tab,view -->
<!-- current-contract:navigation-fallback:project -->

# NavigationIntent と候補の作成元

画面の意味はReact内の選択状態だけに置かず、URL queryの `NavigationIntent` を正本にします。
解析と直列化は `apps/web/src/app/navigation.ts` が担当します。

## URL query

| query | 意味 |
|---|---|
| `view` | 表示画面。`project`、`project-settings`、`candidates`、`candidate-review`、`workspace`、`quality`、`lineage`、`explore`、`data-library`、`profile-workbench` |
| `project` | 対象プロジェクトID |
| `candidate` | 比較または履歴で選択する候補ID |
| `entity` | 工程系譜で選択するentity key |
| `quality_issue` | 調査元の品質issue ID |
| `quality_type` | 品質一覧の検出種別filter |
| `quality_sheet` | 品質一覧の元シートfilter |
| `quality_key` | 品質一覧のキーfilter |
| `screening` | 範囲探索で開くrun ID |
| `snapshot` | プロジェクト履歴で開くsnapshot ID |
| `admin` | 開発・管理画面のsection。`developer`、`ranges`、`display`、`task`、`model` |
| `activity` / `activity_run` | `candidate-review`で開くActivityとRun |
| `candidate_section` | `candidates`で開く候補section。現在は`actuals`だけを明示する |
| `developer_tab` / `developer_guide` | `workspace`のdeveloper sectionで開くtabとguide |
| `project_settings` | `project-settings`で開くsection。`general`、`targets`、`scientific`、`ranges`、`display`、`task`、`evidence` |
| `tab` | Data Libraryの表示。省略は`browse`、`update`はデータ更新を開く |
| `connector` / `stage` / `revision` | Data Libraryの更新履歴で開く接続先、段階（`raw`、`curation`、`approval`、`training`）、不変resource ID |
| `onboarding` / `base_dataset` | Data LibraryまたはProfile Workbenchの追加導線（`revision`、`mapping`、`new-task`）と更新元Dataset revision |

未知の`view`は`project`へ、未知のenum値・依存先のないresource指定は省略へ正規化します。
たとえば`revision`は有効な`connector`と`stage`があるData Library更新画面だけで意味を持ちます。
`browse`は既定値なのでURLへ書かず、`update`と選択済みresourceだけを直列化します。

解析・直列化・正規化の唯一の実装は`apps/web/src/app/navigation.ts`です。
Navigation owner（`apps/web/src/app/App.tsx`）だけが`history.pushState`、`replaceState`、`popstate`を扱います。
feature componentは型付きlocationを`onNavigate`でownerへ渡し、URL文字列やhistory APIを直接扱いません。

利用者が戻りたい画面・tab・resource選択は`pushState`で記録します。
起動時またはbrowser back／forwardで検出した旧URL、未知値、既定resourceの補完は`replaceState`で正規化し、不要な履歴entryを増やしません。
`popstate`ではserver stateを再作成せず、URLにある選択文脈だけを復元します。

画面を切り替えるときは、その画面で意味を持つqueryだけを引き継ぎます。
品質条件は `quality` と `lineage`、`entity` は `lineage`、`screening` は `explore`、`snapshot` は `project`、`admin` は `settings` で保持します。
対象を復元できない場合は、別の対象へ暗黙に切り替えず、未解決の参照として表示します。

NavigationIntentへqueryを追加するときは、parse → serialize → parseのround-trip unit testを追加する。
画面操作を追加するときは、fresh browserで共有URL、reload、back／forwardを実証する。

## 候補の作成元

候補の `provenance` は作成時に固定し、通常の候補更新では変更できません。

- **`direct`**：比較画面で直接作成した候補
- **`lineage`**：`entity_type`、`entity_key`、任意の `data_source_digest` から作成した候補
- **`screening`**：`run_id`、`point_id`、`point_index` から作成した候補
- **`copy`**：`project_id`、`candidate_id`、作成時の `candidate_revision` を持つ複製
- **`snapshot`**：`snapshot_id` から復元した候補
- **`manual`**：移行済みデータのうち、作成元を特定できない手入力候補

archive済みの作成元は `include_archived=true` で読み取り専用の参照対象として復元します。
作成元が物理削除済み、別projectから参照不能、または不整合である場合はbroken reference状態を表示します。
