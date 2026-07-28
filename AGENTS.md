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

`npm run dev` の既定DBは `.dev-workspaces/<branch名>-<短いhash>.db` であり、
`data/workbench.db` は開かない。固定レビュー状態へ戻すときはserverを止めて
`npm run workspace:seed` を使う。本物の判断台帳を開く場合だけ
`npm run dev:main-workspace` または `WORKBENCH_DB_PATH=data/workbench.db` を
明示する。`workspace:seed`は環境変数で指定したDBを拒否し、branch既定Workspace
だけを初期化する。起動前のread-only整合検査は `npm run workspace:check` で
単独実行できる。

実装中は、変更した契約や挙動に対応するLevel 0を使う。

```powershell
npm.cmd run verify:edit -- backend/tests/test_screening_score.py
```

通常のPRはLevel 1を使い、対象pytestを`--`以降へ渡す。

```powershell
npm.cmd run verify:pr -- backend/tests/test_screening_score.py
```

複数PRをまとめた節目は`npm run verify:checkpoint`、配布・migration・restore・Packageなど高リスクの受入は`npm run acceptance:release`を使う。通常PRごとにLevel 2／3を要求しない。

4段階の選択、risk matrix、gateの唯一の正本は`docs/verification-policy.md`と`scripts/verification-gates.json`に置く。未実行gateを成功扱いしない。

画面または操作経路を変えた場合は、変更リスクに対応するPlaywright specもfresh serverで実行する。

反復中のserver再利用は高速化のためのfocused loopであり、merge前の証拠には使わない。

`pythonpath` と `testpaths` は `pyproject.toml` でリポジトリ直下基準に固定してある。
`backend/` から `pytest` を実行すると `ModuleNotFoundError: No module named 'backend'`
と複数の失敗が出るが、これは cwd 違いであって実際の失敗ではない。

E2EはPlaywrightで実行する。Level 2にはAPI offlineとaccessibility smokeを扱うfailure-state laneが含まれる。

通常の画面経路と専用runtimeを必要とするspecは、変更内容に応じて別途実行する。

ポートは環境変数で移せる。

既定の `npx playwright test` は `e2e/` 全体を対象にするが、専用configを持つ
`chain-degraded.spec.ts` だけは除外している（`playwright.chain-degraded.config.ts`の
fixtureとportが要る）。専用runnerは次の3つ。

```powershell
npm run test:e2e:degraded-task     # degraded-task.spec.ts
npm run test:e2e:failure-states    # api-offline / accessibility-smoke
npx playwright test --config playwright.chain-degraded.config.ts
```

Projectのbindingはspecから固定Package IDで指すのではなく、`resolveProjectBinding`へ
`{ datasetFilename }` を渡す。Packageは不変で契約や学習データが変わるたび新しいIDになるため、
IDを直接書くとその改版で落ちる。

```powershell
npx playwright test
$env:PLAYWRIGHT_API_PORT=9001; $env:PLAYWRIGHT_WEB_PORT=5321; npx playwright test e2e/navigation-intent.spec.ts
```

既定では毎回 API と Web を起動する。1回あたり25〜30秒かかり、specを1件ずつ
直すループではこれが支配的になる（3件のspecで計46秒のうち約40秒が起動）。
`PLAYWRIGHT_REUSE_SERVER=1` を付けると常駐サーバに接続する（同じspecが8秒）。

```powershell
npm run dev   # 別ターミナルで常駐させたまま
$env:PLAYWRIGHT_REUSE_SERVER=1; $env:PLAYWRIGHT_API_PORT=8765; $env:PLAYWRIGHT_WEB_PORT=5180; npx playwright test e2e/navigation-intent.spec.ts
```

**再利用は反復ループ専用。完了判定には使わない。** 理由は2つある。

- specは `default` などの共有Projectを書き換えるため、同じDBに対する2回目の
  実行では落ちる（実測：既定パス0件失敗 → 同一サーバへの2回目で8件失敗）。
  既定パスは実行ごとに `material-workbench-e2e-<pid>.db` を作るのでこれが起きない
- 常駐サーバは起動時にbindしたDataset revisionを持ち続ける。データセットや
  Packageを変更したときは、常駐サーバを再起動しないと古い版を見たままになる

## 進め方

- 独立して検証できる作業はサブエージェントへ委任し、共有worktreeの所有権を明示する
- merge前に、実装者と異なる観点の敵対的レビューで穴をつぶす
- UIの細かい部分はユーザーが実際にさわってFBします

### 教材review

`docs/learning/`の章を完成扱いにする前に、`docs/learning/reviews/`へ観点別の記録を残す。

- 全章でimplementation、pedagogy、accessibilityを確認する。
- 数理章はstatistics、材料判断へ接続する章はdomain、trust boundaryを扱う章はsecurityを追加する。
- Level A（Editorial）は誤字・link・組版、Level B（Technical）はcode path・挙動・test、Level C（Conceptual）はarchitecture・統計・domain・教育、Level D（Adversarial）は誤読・edge case・unsafe shortcut・隠れた仮定を扱う。数理と安全境界はLevel Dまで確認する。
- AIは用語、定義、code reference、cross-reference、曖昧さ、演習整合、敵対的質問の候補を出せるが、実装意図、統計・domainの妥当性、severity、実読者の理解を単独で承認しない。
- 代理reader taskは`proxy: true`と限界を記録し、実読者・支援技術・専門reviewの未実施を隠さない。

## 原則

1. データは二つの軸で判定する。provenance／license軸では実測か合成か、公開か非公開かを確認する。用途軸ではproduction、reference、教材データ、test用の合成fixtureを区別する。Task inventory、Profile metadata、license、provenanceを正本とし、明示のないデータを合成fixtureと決めつけない。
2. `relation` の一行を学習行として直接使わない。工程条件と反復観測を分離する。
3. プレビューと詳細予測を分け、入力変更時は変更候補だけを更新する。
4. 過度な最適化をしない。実測して遅い箇所だけ改善する。
5. productionはローカルファーストとし、明示的な探索Issueで隔離したspikeを除いてクラウド構成を追加しない。
6. 過度な作り込みは避ける。ただし、科学的な誤判断、データ破損、復旧不能、accessibilityの阻害につながる品質は検証速度より優先する。
7. 元Excel（`data/source/`）は読取専用の正本。アプリ・スクリプト・テストのどこからも変更しない。
8. モデルPackageからPythonコード・pickle・joblibを読み込まない。新しいモデル種類はallow-listされたadapterをアプリ本体へ追加して対応する。
9. 保存済み予測スナップショットは不変。予測結果にはモデル・特徴量パイプライン・学習データの版を必ず残し、最新モデルで自動再計算しない。
10. テストは網羅カバレッジを狙わず、モデル契約テスト・特徴量ゴールデン・Package smoke・一本のE2Eなど、科学的な誤判断や再現性崩壊につながる箇所へ絞る。
11. UIの基本言語は日本語。不確実性は専門用語のまま出さず、判断に使える表現へ翻訳する。UI上で予測値と実測値を混同させない。
12. 元データにあるシート名や列名などに関する名称などをコード内にハードコーディングすることなどは極力避ける
13. もし、実装の中で前提の変更や作業環境の改善が望ましい場面があればその旨を伝えること

## AI self-check

- 変更対象の正本、生成物、読取専用資源を区別したか。
- 予測、実測、不確かさ、支持範囲を混同していないか。
- 保存済みSnapshot、Run、Project identityを暗黙更新していないか。
- edit loopだけで完了扱いせず、変更riskに対応するLevel 1以上と必要なbrowser証拠を残したか。
- Actionsが利用可能かを実行時に確認し、利用できない場合は選択したローカルgateと不足する外部証拠をPRへ記録したか。

## 詳細ドキュメント

- [docs/app-charter.md](docs/app-charter.md) — 対象範囲、対象外、将来候補
- [docs/model-package-contract.md](docs/model-package-contract.md) — モデルPackageの契約と読込手順
- [docs/feature-engineering.md](docs/feature-engineering.md) — 特徴量パイプラインの定義
- [docs/design-system.md](docs/design-system.md) — UIデザインシステム

## CIの扱い

GitHub Actionsの利用可否は一時的な運用状態であり、この文書の不変条件ではない。

PRごとに現行checkを確認し、利用できない場合はローカルfull gateと変更リスクに応じたbrowserまたはpackaged smokeをPR本文へ記録する。
