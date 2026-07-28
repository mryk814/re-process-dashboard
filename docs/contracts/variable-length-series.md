# 可変長系列の契約

## 目的

温度履歴、電池のサイクル劣化、スペクトルなど、点数が一定でない測定列を、元観測を失わずにモデル入力へ変換する。
系列は次の三層を混ぜない。

1. **Raw Series** — 取得した点、source順、単位、channel、取得元を不変に保持する。
2. **Canonical Series** — 意味と単位だけを、明示したrecipeで正規化したrevisionである。
3. **Feature Representation** — 補間、resampling、区間統計量、sequence tensorなど、モデルが使う表現である。

Canonical Seriesを「モデルが欲しい固定長配列」に加工しない。
補間や平滑化で観測を増減させる処理はFeature Pipelineだけで行う。

## Raw Series

`RawSeriesAsset` は次を固定する。

- coordinate名・単位
- value名・単位
- series kind
- 各点のcoordinate、value、channel、source position、任意のsource row
- source kind、locator、取得時刻、source digest、profile revision
- 内容全体のSHA-256 digest

同じdigestの再登録は同じassetを返す。
更新・削除APIは持たず、修正時は新しいRaw Seriesを登録する。

## Canonical Series

許可する正規化操作はallow-listされた次の意味保存変換だけである。

- coordinate/valueの単位変換
- 経過座標の原点合わせ
- source positionを残す安定sort
- 値が同一の重複座標の統合

Recipeは順序を含むdigestで固定する。
Canonical revisionはRaw digest、Recipe digest、変換履歴、品質finding、canonical digestを保持する。

品質状態は次の通り。

| 状態 | 意味 |
|---|---|
| `accepted` | 変換不要で採用できる |
| `normalized` | 明示recipeによって正規化した |
| `warning` | 採用できるが確認事項がある |
| `quarantined` | 意味を一意に決められず隔離した |
| `blocked` | 必要点数などの契約を満たさず変換を停止した |

座標順、重複、有限値、最小点数を検査する。
同一channel・同一座標に異なる値がある場合は、平均などで隠さず `conflicting_duplicate_coordinate` として隔離する。
source順が逆転している場合は、明示的なstable sortがなければ停止する。

## Feature Representation

`SeriesFeatureContract` がModel PackageのFeature Pipelineに表現とparameterを固定する。
現在のallow-listは次の三つである。

- `linear_resample_v1` — Canonical範囲内の線形resampling
- `segment_statistics_v1` — channelごとの最小、最大、平均、傾き、面積
- `sequence_tensor_v1` — 点数を保つ可変長tensor

Feature previewはCanonical revision digest、Feature contract digest、shape、feature名、値を返す。
Model Packageは `feature-pipeline.json` の `series_representations` へ契約を含める。
旧Packageではこのフィールドを省略でき、空の表現集合として読み込む。

## APIと画面

- `GET /api/series-assets`
- `POST /api/series-assets`
- `GET /api/series-assets/{series_id}`
- `POST /api/series-assets/{series_id}/canonical-revisions`
- `POST /api/canonical-series/{revision_id}/feature-preview`

データライブラリはRawとCanonicalを別チャートで表示する。
異なるchannelは別のsmall multipleに分け、異なる単位を同じ軸へ重ねない。
変換履歴、finding、source position、Feature Representationのshapeを同じinspectorで確認できる。

同梱例は温度履歴と電池サイクル劣化で、点数と座標の意味が異なる。
この二例は契約の反証用であり、統計精度を示すデータではない。

## 現時点の境界

一般系列assetの契約・保存・inspectionはproductionで利用できる。
一方、通常TaskのCandidateへ系列をbindingし、推論時に自動変換する経路はTaskごとの縦スライスとして追加する。
既存の焼鈍 `heat_pattern` scalar入力を暗黙に一般系列へ置き換えない。
