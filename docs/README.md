# ドキュメント索引

開発者は最初に [Developer Start Here](developer-start-here.md) で変更の種類と影響範囲を判断してください。その後、該当する個別契約文書、[教材ガイド](tutorial-data-pipeline.md)、実装Skillの順で読みます。

`docs/` 直下には、現在の実装や運用を規定する文書だけを置きます。
過去の判断は `decisions/`、特定時点の計測結果は `benchmarks/` に分離します。
完了済みIssueの受入記録はIssue、Pull Request、Git履歴を正本とし、常設文書として複製しません。

## プロダクトとUI

- [app-charter.md](app-charter.md) — 対象範囲、対象外、将来候補
- [design-system.md](design-system.md) — 表示トークン、画面構造、操作と表の規則
- [navigation-intent.md](navigation-intent.md) — URLと画面遷移の契約
- [frontend-boundaries.md](frontend-boundaries.md) — フロントエンドの責務境界と検査方法

## データ、特徴量、モデル

- [developer-start-here.md](developer-start-here.md) — やりたい変更から契約、成果物、検証を判断する入口
- [dataset-input-profile.md](dataset-input-profile.md) — Workbookをアプリ共通入力へ対応付ける手順
- [feature-engineering.md](feature-engineering.md) — 特徴量パイプラインの定義
- [model-package-contract.md](model-package-contract.md) — Model Packageの安全境界と読込契約
- [model-package-lifecycle.md](model-package-lifecycle.md) — Packageの検証、作成、有効化、ロールバック
- [model-runtime-examples/index.md](model-runtime-examples/index.md) — Runtimeの実装例と採用状態
- [task-inventory.json](task-inventory.json) — 現行タスクとactive Packageの自動生成一覧。直接編集しない

## 実行と配布

- [inference-execution.md](inference-execution.md) — 推論処理の識別、共有、キャッシュ方針
- [windows-distribution.md](windows-distribution.md) — Windows向け成果物の作成とsmoke確認

## 履歴資料

- [decisions/](decisions/) — 採否を含む設計判断。現行機能一覧としては使わない
- [benchmarks/](benchmarks/) — 計測条件に依存する性能記録。現行値としては使わない

## 更新規則

- 実装状態の一覧は、手書きのRuntime一覧より `task-inventory.json` を優先します。
- 日付と計測環境に依存する数値は、現役契約へ直接固定しません。
- 完了済みIssue番号を「後続作業」として残しません。
- 文書から実在しない画像、ファイル、コマンドを参照しません。
