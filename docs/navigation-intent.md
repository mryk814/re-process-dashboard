# NavigationIntent と候補 provenance

画面の意味は React 内の選択状態ではなく、URL query の `NavigationIntent` を正本にする。

| query | 意味 |
|---|---|
| `view` | 表示画面。`project` / `candidates` / `quality` / `lineage` / `explore` など |
| `project` | 対象プロジェクト ID |
| `candidate` | 比較・履歴で選択する候補 ID |
| `entity` | 工程系譜で選択する entity key |
| `quality_issue` | 調査元の品質 issue ID |
| `quality_type` | 品質一覧の検出種別 filter |
| `quality_sheet` | 品質一覧の元シート filter |
| `quality_key` | 品質一覧のキー filter |
| `screening` | 範囲探索で開く run ID |
| `snapshot` | プロジェクト履歴で開く snapshot ID |

`apps/web/src/navigation.ts` が parse と serialize を担当する。画面遷移は `history.pushState`、同じ画面内の選択同期は `replaceState` を使い、`popstate` で復元する。

候補の `provenance` は作成時に固定し、通常の候補更新では変更できない。

- `direct`: 比較画面で直接作成
- `lineage`: `entity_type` / `entity_key` / optional `data_source_digest`
- `screening`: `run_id` / `point_id` / `point_index`
- `copy`: `project_id` / `candidate_id` / 作成時の `candidate_revision`
- `snapshot`: `snapshot_id`
- `manual`: 移行済みデータの由来不明な手入力

archive済みの作成元は `include_archived=true` で読み取り専用の参照対象として復元する。物理削除済み、別projectから参照不能、または不整合なら、暗黙に別対象へ切り替えずbroken reference状態を表示する。

## 後続 Issue の接続点

- #11 は品質 issue の `focus_entity_key` を `entity` に渡し、`quality_*` を保持して品質一覧へ戻す。
- #2 は `entity` を初期選択ノードとして受け取り、ノード選択時に同じ intent を更新する。
- #12 は保存・選択した探索 run を `screening` に、stock した候補を `candidate` に渡す。
