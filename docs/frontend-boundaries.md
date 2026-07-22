# フロントエンドの責務境界

Webフロントエンドは責務ごとに分割し、すべての予測タスクで一つのタスク駆動Workbenchを使います。

```text
app -> features/{projects,quality,lineage,screening,workbench,admin}
workbench -> candidates -> shared -> generated
```

- `app/` は、ナビゲーションの意図とアプリ全体の来歴ルーティングを担当します。
- `features/candidates/` は、候補モデル、タスク駆動の入力UI、編集と保存のライフサイクルを担当します。利用側は公開された `index.ts` から読み込みます。
- `shared/api/` は、生成済みクライアントのラッパーとリクエストキャッシュを担当します。app層やfeature層には依存できません。
- `features/workbench/` は、予測面の状態、選択候補を優先するプレビュー読込、根拠、応答曲線、スナップショット、実測値を担当します。
- そのほかの画面featureは、APIレスポンスから表示用データへの変換と、そのfeature内のスタイルを担当します。
- `app/App.tsx` はルーティングと構成だけを担当します。ルートの `src` には起動点と共有スタイルの起点だけを置き、ドメインモジュールは置きません。

## 境界の検査

`npm run typecheck` は、TypeScriptの検査前に `scripts/check-import-boundaries.mjs` を実行します。
この検査は、逆向きの依存、禁止されたfeature間import、featureの公開入口を迂回するimport、循環依存、ルート直下のドメインモジュールを拒否します。
現在はファイルサイズとCSS量の上限を強制していません。
大きなモジュールは構造レビューと対象を絞ったリファクタリングで縮小します。

共通操作のブラウザ契約は `e2e/shared-workbench.spec.ts` に置きます。
推論結果を無効化する条件と、表示中の予測面に対するリクエスト数は `e2e/inference-p0.spec.ts` で固定します。
