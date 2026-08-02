# Model Library UX Change Brief

## 利用者の問い

現在のWorkspaceで、どのPrediction Task、Model Package、Transform、Prediction Graphを再利用でき、利用不能なら何を復旧すべきかを確認したい。

## 到達時点で確定していること

Workspaceと、そのWorkspaceへ登録済みの資産・公開済みGraph Revisionは確定している。

## この画面で決めること

確認する資産種別と、Data Library、Project作成、Graph authoringのどこへ続くかを決める。

## この画面で決めさせないこと

data approval、Package activation、Graph publication、Project内の候補条件は変更させない。Model Libraryはread-onlyである。

## 構造案

### 案A: 資産種別tabs＋一覧内detail

同種資産を状態とidentityで比較し、必要な資産だけ技術詳細を開く。Graphではinput、stage、decision output、固定Revisionをまとめて読む。

### 案B: 四種を接続した資産マップ

関係は見えるが、多数資産で交差が増え、keyboard／small viewportのlinear alternativeも別途必要になる。

## 採用案と配置根拠

案Aを採用する。同時に比較する型を一つへ限定し、状態を資産名より先に確認できる。Graph detailの操作は固定Revisionの説明後に置き、編集可能だと誤解させない。Data LibraryとProject作成はTask／PackageのDataset参照に隣接させる。

## 既定表示と技術詳細

既定表示はavailability、lifecycle、用途、件数、主要identity。digest、port、固定stage、Validation Planはdetailに置く。

## 受入観察

- top navigationからModel Libraryへ到達し、URL reload／backでtabを復元できる。
- 四資産種別を区別して閲覧できる。
- unavailable／research-onlyの理由、影響、回復方法を操作前に読める。
- Graph detailでinput、stage、decision output、fixed Revisionを確認してStudioへ移動できる。
- Task／PackageからData Libraryへ移動でき、Dataset参照を持つPackageは既存Project作成面へ進める。

