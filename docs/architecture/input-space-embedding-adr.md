# 入力空間Surfaceの埋め込み方式

Status: accepted
Date: 2026-07-30
Issue: #508

## 判断したいこと

候補比較では、候補がModel Packageの学習条件の島から離れているかと、
候補同士が似すぎていないかを見たい。
この二つはTaskが定めた入力距離で評価し、二次元図の見た目だけで判定しない。

新しい候補は日常的に追加される。
そのたびに学習実績を含む配置全体を再学習すると、既存点の位置と判断の説明が変わる。
したがって、学習cohortへ一度fitし、新規候補を同じ変換へ配置できることを必須とする。

## 採用

`landmark-classical-mds-oos@1.0.0`を採用する。

1. Taskの`input_space` Surfaceが、距離の基準target、実績を束ねる単位、
   embedding version、seed、landmark数を宣言する。
2. Model Packageに固定された学習cohortを、Runtimeの
   `support_policy_id`と同じ特徴変換・距離で表す。
   支持・注意閾値はRuntimeがgroup-aware LOOから起動時に確定した値を使い、
   Surfaceでは再計算しない。
3. seed固定のfarthest-firstで最大96点のlandmarkを選ぶ。
4. landmark間のTask距離へclassical MDSをfitする。
5. 表示する学習点と新規候補を同じGower out-of-sample式で二次元へ配置する。
6. 学習の島までの距離は学習cohortへの最短Task距離、
   候補間の新規性はほかの候補への最短Task距離として別々に返す。

flank-wear Taskでは、同じ独立run内の反復観測を別々の学習条件として数えない。
Runtimeのrun平均特徴量を1点とし、`parent_condition`を実績単位にすることで、
支持範囲の距離・閾値・表示点を同じ180 runの粒度へ揃える。

APIは`distance_method`、`distance_version`、`cohort_digest`、
特徴量順序、group、正規化済み参照vector、Package manifest、
Feature Pipeline identityを固定する`vector_space_digest`、
`embedding_method`、`embedding_version`、`seed`、landmark数を返す。
画面はこのidentityを技術詳細で確認できるようにする。

固定cohort、距離contract、landmark、表示用学習点はRuntime単位で一度だけ解決する。
大規模cohortでも全点間距離は作らず、farthest-first landmark選択は
`O(N × landmark_limit)`、表示用追加点の選択は`O(N)`とする。

## 比較した選択肢

### PCA

線形変換でout-of-sampleは明快だが、Task距離が組成のCLRやgroup weightingを含む場合、
その距離を保存する配置にならない。
特徴ベクトルへPCAをかけた図とTask距離の判定が別物になるため採らない。

### UMAP

局所構造の可視化には有力だが、現行依存へ追加ライブラリが必要で、
乱数、近傍graph、学習実装のversionまで固定する必要がある。
transformは可能でも、配置上の距離をTask距離として読みやすくする方式ではない。
現在のデータ規模と目的では複雑さが勝るため採らない。

### Task距離の数値だけ

支持範囲と近傍順序の正本としては最も直接的で、画面にも数値を残す。
一方、複数の島や候補全体の位置関係を一目で読む用途を満たさないため、
数値表だけにはしない。

### 全点classical MDS

距離行列と固有値分解が学習条件数に対して二乗メモリ・三乗計算となる。
ローカルアプリの初回表示には重い。
landmarkを固定してout-of-sample配置する方式を採る。

## 非目標

- 軸1・軸2へ材料学的な意味を付けない。
- 二次元上の距離を支持範囲、外挿、候補新規性の判定に使わない。
- cluster数、島の数、密度、異常度を自動推定しない。
- 目的変数、予測値、目標達成度を入力空間へ混ぜない。
- candidate追加のたびに学習cohortやlandmarkをfitし直さない。
- 2軸で保持できない距離構造を、完全に再現できたように表示しない。

## 検証

- syntheticな二次元fixtureで、同じseedの座標とout-of-sample近傍順序を固定する。
- 実Taskで、APIを二回呼んだ応答が一致すること、
  `nearest_training_context_id`がTask距離の最小条件と一致することを確認する。
- 14,000行以上のwear Taskで、複数候補の初回配置とcache後の再表示に
  上限時間を設け、全点間距離の再導入を検出する。
- wearとbattery Taskで、Surfaceの閾値がRuntimeのprecomputed値と一致することを確認する。
- UIでは島までの距離と候補間の新規性を別欄にし、
  軸の向き自体に意味がないことを明記する。
