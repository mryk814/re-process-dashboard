# UI Lab

UI Labは、共通Workbenchの画面構成・操作・情報密度を、本番契約の完成を待たずに検証するための実験環境です。

## 起動

```powershell
npm install
npm run dev:lab -w apps/web
```

Viteが開いたら `/lab.html` を使用します。APIやPython backendは不要です。

## このbranchの位置づけ

- Branch: `prototype/ui-workbench-lab`
- Tracking Issue: #24
- production実装ではない
- branch全体をmainへmergeしない
- 本線の#17 / #5 / #6 / #7 / #8 / #14 / #15をブロックしない
- 本線エージェントは、このbranchの型・mock・CSSを正本として参照しない

UI Labの成果はコード量ではなく、次の判断です。

- 採用する画面構成
- 捨てる画面構成
- 利用者が迷う操作
- 最初から表示すべき情報
- 遅延表示でよい情報
- APIに本当に必要な情報
- loading / stale / error / unavailableの適切な見せ方

## 守る境界

### 使ってよいもの

- React / TypeScript / CSS
- `LabTaskDefinition`などLab専用のfixture型
- mock data source
- deterministicな擬似予測・擬似応答曲線
- UI検証用の状態切替

### 接続してはいけないもの

- production endpoint
- SQLite
- Dataset Input Profile
- Model Package
- productionの`App.tsx`
- generated API client
- production cache identity
- 保存・migration処理

### 型の扱い

Lab型には`Lab` prefixを付けます。

```text
LabTaskDefinition
LabCandidate
LabPredictiveSummary
LabRuntimeCapability
```

これらは画面を触るためのfixture型であり、正式契約ではありません。production側へ移植するときは、TaskDefinition / generated client / repositoryの正式型へ接続し直します。

## 大胆に変更してよいもの

- レイアウト
- カード・表・パネルの配置
- 表示密度
- 候補比較方法
- 応答曲線の比較方法
- loading / stale表示
- ラベル
- 色・余白・タイポグラフィ
- keyboard / mouse操作
- component分割

UI Lab内での後方互換性は不要です。良くない案は消してください。

## 合流手順

1. UI Labで操作を試す
2. Issue #24またはスクリーンショットで、採用・不採用と理由を残す
3. productionに必要な情報が不足していれば、該当IssueへAPI要件を返す
4. #7 / #8 / #15側で正式契約を使ってcomponentを小さく再実装または移植する
5. Lab固有mock・型・擬似計算・CSSをproductionへ持ち込まない
6. 移植後もLabは次の探索用に自由に壊す

## 移植可否の判断

次を満たす小さなcomponentだけ移植候補です。

- production型をimportしていない
- API URLを知らない
- task idによる分岐がない
- propsだけで描画できる
- Lab fixtureへの依存を容易に外せる
- 見た目または操作として先輩が採用した

## 最初の検証テーマ

- 左入力・中央比較・右根拠の三面構成
- 焼鈍と熱延を同じshellで扱えるか
- 不確実性がないruntimeの表示
- staleな旧結果を残しながら更新中を示す方法
- 応答曲線を選択候補中心にし、必要な比較候補だけ重ねる方法
- 外挿・注意・supportedを過剰に煽らず判断へ使える表現
- 詳細情報を常時表示するか、遅延・展開表示にするか

## エージェント向け

このbranchを見ているエージェントは、先輩が自由にUIを変更できる状態を優先してください。

- productionへ直接統合しない
- 正式契約をLab側で先回りして決めない
- Labの見た目を守るためにbackend契約を歪めない
- 採用判断前のcomponentをmainへ移植しない
- 大きな変更前でもLab内の互換性確認は不要

本線作業との重複が疑われる場合、Issue #24に担当範囲をコメントしてください。
