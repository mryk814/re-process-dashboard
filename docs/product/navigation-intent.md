# NavigationIntent と候補の作成元

画面の意味はReact内の選択状態だけに置かず、URL queryの `NavigationIntent` を正本にします。
解析と直列化は `apps/web/src/app/navigation.ts` が担当します。

## URL query

| query | 意味 |
|---|---|
| `view` | 表示画面。`project`、`candidates`、`settings`、`quality`、`lineage`、`explore`、`data-library`、`profile-workbench` |
| `project` | 対象プロジェクトID |
| `candidate` | 比較または履歴で選択する候補ID |
| `entity` | 工程系譜で選択するentity key |
| `quality_issue` | 調査元の品質issue ID |
| `quality_type` | 品質一覧の検出種別filter |
| `quality_sheet` | 品質一覧の元シートfilter |
| `quality_key` | 品質一覧のキーfilter |
| `screening` | 範囲探索で開くrun ID |
| `snapshot` | プロジェクト履歴で開くsnapshot ID |
| `admin` | 開発・管理画面のsection。`quality`、`ranges`、`display`、`task`、`model` |

未知の `view` は `candidates` として読み取ります。
画面遷移は `history.pushState`、同じ画面内の選択同期は `replaceState` を使い、`popstate` で復元します。

画面を切り替えるときは、その画面で意味を持つqueryだけを引き継ぎます。
品質条件は `quality` と `lineage`、`entity` は `lineage`、`screening` は `explore`、`snapshot` は `project`、`admin` は `settings` で保持します。
対象を復元できない場合は、別の対象へ暗黙に切り替えず、未解決の参照として表示します。

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
