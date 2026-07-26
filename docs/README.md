# ドキュメント索引

開発者は最初に [Developer Start Here](developer-start-here.md) で変更の種類と影響範囲を判断してください。その後、[現行システム基準](current-system-baseline.md)、該当する個別契約文書、実装Skillの順で読みます。

`docs/` 直下には、現在の実装や運用を規定する文書だけを置きます。
過去の判断は `decisions/`、特定時点の計測結果は `benchmarks/` に分離します。
完了済みIssueの受入記録はIssue、Pull Request、Git履歴を正本とし、常設文書として複製しません。

## プロダクトとUI

- [app-charter.md](app-charter.md) — プロダクトの性格、安全原則、対象外、導入条件
- [current-system-baseline.md](current-system-baseline.md) — 現在のProject mode、実装済み能力、再利用境界、v1固有前提
- [decision-activities.md](decision-activities.md) — 判断に必要な問いを実行単位にするActivity契約
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
- [welding-consumable-sample-dataset.md](welding-consumable-sample-dataset.md) — 多段構造の検証用合成データと接続済み観測Profile
- [welding-stage-b-package.md](welding-stage-b-package.md) — 材料成分＋溶接条件から溶着金属成分を学習するStage B Task / Package
- [task-inventory.json](task-inventory.json) — 現行Task、source、Profile、active Package、capabilityの自動生成一覧。直接編集しない

## 構造の測定

- [architecture/extensibility-inventory.md](architecture/extensibility-inventory.md) — 概念ごとの正本と登録点、Profile family差分、Chain Coreに残る溶接固有symbolの測定結果
- [architecture/extensibility-spikes.md](architecture/extensibility-spikes.md) — 拡張性の反証ケース設計、実測値、証拠にもとづくIssue分割
- [architecture/candidate-shape-policy.md](architecture/candidate-shape-policy.md) — 候補入力形状を追加するときの方針と着手条件

`architecture/` は契約ではなく測定記録です。実装の正本としては使わず、リファクタリング判断の根拠として参照します。

## 実行、Chain、配布

- [inference-execution.md](inference-execution.md) — 単段推論の識別、共有、キャッシュ方針
- [chain-execution.md](chain-execution.md) — Chainの段別実行、部分再計算、鮮度、Snapshot、明示的な分布伝播
- [chain-evaluation.md](chain-evaluation.md) — 段単体精度と通し精度の評価契約
- [windows-distribution.md](windows-distribution.md) — Windows向け成果物の作成とsmoke確認

## 履歴資料

- [decisions/](decisions/) — 採否を含む設計判断。現在の実装一覧としては使わない
- [decisions/data-library-project-references.md](decisions/data-library-project-references.md) — Dataset、Task、Package、Project参照境界の採用理由
- [decisions/multistage-chain-architecture.md](decisions/multistage-chain-architecture.md) — 溶接材料A→B→Cを最初の縦切りにしたChain設計判断
- [benchmarks/](benchmarks/) — 計測条件に依存する性能記録。現行値としては使わない

## 更新規則

- 実装状態の一覧は、手書きのTask／Runtime一覧より `task-inventory.json` を優先します。
- Project modeやv1固有前提が変わった場合は、`app-charter.md` と `current-system-baseline.md` を確認します。
- 新しい契約family、Candidate Shape、Activity、immutable Runを追加した場合は `developer-start-here.md` とこの索引を確認します。
- FastAPI契約を変更した場合はOpenAPIとfrontend生成型を再生成し、drift checkを通します。
- 日付と計測環境に依存する数値は、現役契約へ直接固定しません。
- 完了済みIssue番号を「後続作業」として残しません。
- 文書から実在しない画像、ファイル、コマンドを参照しません。
