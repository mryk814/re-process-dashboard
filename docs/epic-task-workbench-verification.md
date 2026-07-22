# タスク駆動WorkbenchのEpic検証

Epic #3は、次のリポジトリ契約を基準に受け入れ済みです。
Issueのチェックボックスは要約であり、実行可能な検査結果を完了の根拠とします。

| 受入範囲 | 根拠 |
|---|---|
| 全本番タスクの候補、プレビュー、詳細予測、スナップショット、実測値 | `e2e/shared-workbench.spec.ts`, `backend/tests/test_hot_rolling.py`, `backend/tests/test_flank_wear.py`, `backend/tests/test_step4_to_6.py` |
| プロジェクト作成、探索、保存、比較、予測保存、振り返りの操作 | `e2e/project-hub.spec.ts`, `e2e/screening-workbench.spec.ts`, `e2e/navigation-intent.spec.ts` |
| 系譜、スクリーニング、複製の来歴と戻り先 | `e2e/navigation-intent.spec.ts`, `backend/tests/test_api.py`, `backend/tests/test_project_history.py` |
| 外部列名の柔軟性と、必須対応と単位の厳密な検証 | `backend/tests/test_dataset_profile.py`, `backend/tests/test_importer.py` |
| TaskDefinition、Feature Pipeline、Package、実行環境の境界 | `backend/tests/test_task_contracts.py`, `backend/tests/test_feature_pipeline.py`, `backend/tests/test_model_packages.py`, `backend/tests/test_task_registry.py` |
| 学習、検証、有効化、ロールバック、不変スナップショット | `backend/tests/test_model_lifecycle.py`, `backend/tests/test_hot_rolling_model_package_builder.py` |
| 候補の版、アーカイブ、ドメインエラー、移行時のデータ保持 | `backend/tests/test_candidate_safety.py`, `backend/tests/test_candidate_migration.py` |
| OpenAPIから生成するフロントエンド契約とfeature境界 | `backend/tests/test_openapi_contract.py`, `apps/web/tests/importBoundaries.test.mjs` |
| 変更候補と表示中の予測面だけを対象にした推論 | `backend/tests/test_inference_work_graph.py`, `e2e/inference-p0.spec.ts`, `docs/inference-execution.md` |

削除確認も完了条件に含みます。

- `HotRollingWorkbench.tsx` と `/api/hot-rolling/*` ルートが存在しないこと。
- 複数形の応答曲線エンドポイントがなく、本番コードが生成済みクライアントの通信層を迂回して `fetch` を直接呼ばないこと。
- 旧 `hot_rolling_candidates` が、一度だけ実行する保護付き移行処理の読込と削除の経路以外に現れないこと。
- タスクの出力、入力グループ、組成欄、工程欄、カテゴリ選択肢が、解決済みタスク契約から描画されること。

出力の妥当範囲と表示範囲（Issue #43）、追加のモデル実行例（Issue #45と子Issue）などの独立した後続作業は、製品を改善するための別課題です。
これらの後続作業によって、統合Workbenchの構造やEpic #3の受入境界を再び未完了にはしません。
