# フロントエンドの責務境界

Webフロントエンドは責務ごとに分割し、すべての予測タスクで一つのタスク駆動Workbenchを使います。

```text
main
└─ app
   ├─ features/{admin,lineage,projects,quality,screening,workbench}
   ├─ shared
   └─ generated

features/admin ──────┬─> features/candidates
                     └─> features/quality
features/lineage ──────> features/candidates
features/projects ─────> features/candidates
features/screening ────> features/candidates
features/workbench ────> features/candidates

features/* ─────────────> shared ─> generated
features/candidates ─────────────> generated
```

矢印はimportできる向きを表します。
feature間の依存は図に示した組合せだけを許可し、循環させません。

## 各層の責務

- **`main.tsx`**：Reactの起動だけを担当します。
- **`app/`**：URLの `NavigationIntent`、プロジェクト全体のセッション、画面の構成、候補の作成元からの遷移を担当します。
- **`features/candidates/`**：候補モデル、TaskDefinition駆動の入力UI、編集、保存、比較表を担当します。
- **`features/workbench/`**：選択候補を優先するプレビュー、詳細予測、根拠、応答曲線、曲線ファミリー、スナップショット、実測値を担当します。
- **`features/projects/`**：プロジェクト概要、保存結果、履歴を担当します。
- **`features/quality/`**：データ品質の一覧、filter、系譜への接続を担当します。
- **`features/lineage/`**：工程系譜の探索と、実績からの候補作成を担当します。
- **`features/screening/`**：探索条件、探索run、探索点からの候補作成を担当します。
- **`features/admin/`**：品質、範囲、表示、TaskDefinition、Model Packageの開発用設定を担当します。
- **`shared/`**：複数のfeatureが使うAPIラッパー、リクエストキャッシュ、表示変換、UI部品を担当します。`app/` と `features/` には依存できません。
- **`generated/`**：OpenAPIから生成した型と契約を置きます。手書きのドメイン判断を置きません。

別featureから利用する場合は、対象featureの `index.ts` を公開入口にします。
feature固有のAPIレスポンス変換とCSSは、そのfeature内に置きます。
`app/App.tsx` はルーティングと画面構成に限定し、候補編集や推論表示の詳細を持ちません。

## 境界の検査

`npm run typecheck` はTypeScriptの検査前に `apps/web/scripts/check-import-boundaries.mjs` を実行します。
この検査は次を拒否します。

- `shared/` から `app/` または `features/` への依存
- `features/` から `app/` またはルート直下への依存
- 許可されていないfeature間import
- featureの公開入口を迂回するimport
- feature間の循環依存
- `main.tsx` 以外のルート直下モジュール

CSSは `app/styles.ts` を入口として読み込むため、この入口から各featureのCSSへのimportだけを例外として許可します。
現在はファイルサイズとCSS量の上限を自動検査していません。

共通操作のブラウザ契約は `e2e/shared-workbench.spec.ts` に置きます。
推論結果を無効化する条件と、表示中の予測面に対するリクエスト数は `e2e/inference-p0.spec.ts` で固定します。
