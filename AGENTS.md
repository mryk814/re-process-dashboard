# AGENTS.md

材料組成・工程条件の候補を比較し、予測特性・不確かさ・類似過去実験を確認するローカルアプリ（Material Decision Workbench）。

- `apps/web` — React + TypeScript + Vite（UI）
- `apps/desktop` — Electron shell（Python APIを同時起動）
- `backend` — FastAPI + Python（データ、特徴量、モデルruntime、SQLite）
- `models/packages` — 学習済みモデルPackage（データ成果物、コードなし）
- `data/source` — 元Excel。読取専用の正本

## セットアップと検証

```powershell
uv sync --extra dev
npm install
npm run dev   # Web UI: 127.0.0.1:5180 / API: 127.0.0.1:8765
```

変更後は次の3つを通してから完了とする。

```powershell
uv run pytest
npm run typecheck
npm run build
```

## 進め方

- GPT-5.6-lunaなどのサブエージェントにタスクを適切に委任しながら進める
- 敵対的レビューによって実装の穴をつぶす
- UIの細かい部分はユーザーが実際にさわってFBします

## 原則

1. このリポジトリで扱うデータは指示がない限りどれもデモ用に作った合成データです、過度に精度を求める価値はなく、形式やUI上の見え方といった、アプリケーションを扱う際の問題点を探すために仮として使っている点に留意してください。
2. `relation` の一行を学習行として直接使わない。工程条件と反復観測を分離する。
3. プレビューと詳細予測を分け、入力変更時は変更候補だけを更新する。
4. 過度な最適化をしない。実測して遅い箇所だけ改善する。
5. 将来のWeb化は境界を保つことで対応し、今からクラウド構成を作らない。
6. プロダクト品質を目指して開発期間を膨らませない。検証速度を常に優先する。
7. 元Excel（`data/source/`）は読取専用の正本。アプリ・スクリプト・テストのどこからも変更しない。
8. モデルPackageからPythonコード・pickle・joblibを読み込まない。新しいモデル種類はallow-listされたadapterをアプリ本体へ追加して対応する。
9. 保存済み予測スナップショットは不変。予測結果にはモデル・特徴量パイプライン・学習データの版を必ず残し、最新モデルで自動再計算しない。
10. テストは網羅カバレッジを狙わず、モデル契約テスト・特徴量ゴールデン・Package smoke・一本のE2Eなど、科学的な誤判断や再現性崩壊につながる箇所へ絞る。
11. UIの基本言語は日本語。不確実性は専門用語のまま出さず、判断に使える表現へ翻訳する。UI上で予測値と実測値を混同させない。
12. 元データにあるシート名や列名などに関する名称などをコード内にハードコーディングすることなどは極力避ける

## 詳細ドキュメント

- [docs/README.md](docs/README.md) — 現役文書、設計判断、ベンチ記録の索引
- [docs/app-charter.md](docs/app-charter.md) — 対象範囲、対象外、将来候補
- [docs/model-package-contract.md](docs/model-package-contract.md) — モデルPackageの契約と読込手順
- [docs/feature-engineering.md](docs/feature-engineering.md) — 特徴量パイプラインの定義
- [docs/design-system.md](docs/design-system.md) — UIデザインシステム

## 注意

- すいません、いまgithub actionsは月の制限にかかっているので使えません
