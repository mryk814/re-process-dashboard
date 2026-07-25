# アプリ憲章

## 性格分類

研究開発の意思決定を支える、デスクトップ向けの作業ツール／ダッシュボード。

## データの重さ

元Excelを正本として読み取り、アプリは候補、予測スナップショット、範囲探索run、検討アクティビティrun、実測値をローカルSQLiteへ保存する。正規化したデータセットは実行時に構築し、元Excelは変更しない。

## 利用者・配布

材料研究者が自分のWindows PCで使うローカルアプリ。Electron、React、FastAPIの境界を維持し、将来のWeb化より現在の検討速度を優先する。

## ローカルAPIの信頼境界

FastAPIはloopbackだけで待ち受けるが、loopbackであることだけを認証の代わりにはしない。

- Electronは起動ごとのlaunch tokenをsidecarとrenderer通信へ固定する。
- `npm run dev`も起動ごとのtokenを発行し、Vite proxyがAPI requestへ付与する。tokenをbrowser bundleへ埋め込まない。
- `file://`由来の`Origin: null`はDesktop launch tokenがある場合だけ許可する。
- tokenなしでAPIを単独起動した場合、browser originはloopbackまたは明示設定したoriginだけを許可する。Originを持たないlocal CLIとtest clientは利用できる。
- インターネット向け公開、別PCからの接続、共有サーバー運用は対象外であり、この境界をそのまま流用しない。

## 標準からの逸脱

- 画面はデスクトップ中心。モバイルは内容確認できる縮退表示までとする。
- 応答曲線や区間は、実モデルまたは実データから計算できる情報だけを表示する。
- `relation` の一行を学習行へ変換しない。工程条件と反復観測を分離する。

## 予測タスクの構成

production taskはそれぞれを独立した縦スライスとして持ち、入力スキーマ、特徴量パイプライン、モデルPackage、支持度参照、候補比較を混在させない。現行の登録内容、ソース、能力、active Packageは [生成済みTask inventory](task-inventory.json) を唯一の件数・構成一覧とする。

## 対象外とするもの

- アプリ内でのモデル学習、ハイパーパラメータ最適化、ベイズ最適化による自動候補提案。
- 認証・監査・高可用性などのエンタープライズ品質、汎用プラグイン基盤、汎用EDA・BIビルダー。
- 元データ（Excel）の直接修正と学習データの自動更新。アプリは問題の発見・確認・一覧出力まで。
- 複数特性の同時達成確率。各特性の達成確率を個別に表示する。
- 候補の「検討中」「実験予定」などの厳密なステータス管理。

## 採用済みの拡張方向

- Data AssetとDataset Input Profileを不変なDataset RevisionとしてData Libraryへ登録する。
- ProjectはDataset View、Prediction Task、Model Packageを固定し、過去の判断を自動更新しない。
- 探索データ、目的変数別の学習コホート、モデル支持範囲、Project内の類似条件を分離する。
- 複数Projectは任意の検討グループへ束ね、所属は後から変更できる。前後関係はグループとは独立して記録する。
- 判断に必要な問いを検討アクティビティとして定義し、候補revisionと実行条件を固定した結果を保存する。最初のアクティビティはロバストネス／公差解析とする。

詳細は [Data LibraryとProject参照境界](decisions/data-library-project-references.md) を参照する。
検討アクティビティの契約は [検討アクティビティ](decision-activities.md) を参照する。
起動時の障害境界と、利用停止中Taskで保存履歴を守る方針は
[Task単位のdegraded startup](decisions/degraded-task-startup.md) を参照する。

## 将来候補（導入条件つき）

- 二変量応答面：描画・計算負荷を確認してから。
- ライン速度・設備長・ゾーン条件からのヒートパターン生成：内部の「入力方式」と「正規化済み時間温度列」の分離を保っていれば追加できる。
- 一つのProject内に複数Prediction Taskを持たせること：Taskごとの候補契約と判断単位を統合する具体的ニーズが確認できてから。
