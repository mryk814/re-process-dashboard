# Candidate Shapeの拡張方針

計画§2.2に対する方針。**この文書の時点では実装しない。**
新しい候補入力形状が必要になったとき、どう追加すれば既存形状を壊さないかを先に決めておく。

根拠は [ケースC実測](extensibility-spikes.md#3-ケースc可変長温度系列task)（7項目すべてが現行契約で表現できない）と
[inventory §1.6](extensibility-inventory.md#16-candidate-shape)。

## 現在地

候補入力は単一形状であり、typed unionではない。

| 層 | 型 |
| --- | --- |
| API入力 | `CandidateInputs`（composition / process / categorical / heat_pattern / heat_time_basis） |
| 正規形 | `CanonicalCandidate`（同上、heat_time_basisなし） |
| 契約側の許容path | `^(composition\|process\|categorical)\.…$\|^heat_pattern$` |
| UI側のgroup key | 同じ4値を `Set` で固定 |

共有schemaにドメイン固有フィールドが混ざっている。

- `CandidateInputs.heat_time_basis` — 焼鈍ライン固有
- `CandidateInput.blend` / `editor_state` — 溶接固有

`heat_pattern` は「最大30点の時刻―温度列」という**単一の例外形状**として存在する。

## 方針

### 1. 任意JSONにしない

`inputs: dict[str, Any]` のような逃げ道を作らない。
形状ごとにpydanticモデルを定義し、`schema_version` を判別子とする
discriminated unionへ**明示的にallow-list**する。
Decision Activityのparameter/resultで採った方法（[decision-activities.md](../decision-activities.md)）と同じにする。

```text
CandidateInputs（union）
  ScalarCandidateInputs        schema_version = candidate-inputs.scalar/v1
  SparseBlendCandidateInputs   schema_version = candidate-inputs.sparse_blend/v1
  SeriesCandidateInputs        schema_version = candidate-inputs.series/v1   ← 実needが出てから
```

pydanticの `Field(discriminator=)` はunionが2メンバー以上でないと使えない。
したがって**1形状目の切り出しと2形状目の追加は同じ変更で行う**。
Decision Activityで同じ制約に当たっている。

### 2. 既存形状は `ScalarCandidateInputs` として保存互換にする

現行の composition / process / categorical / heat_pattern は
`ScalarCandidateInputs` へそのまま入る。
保存済み候補revisionは不変なので、判別子を持たない既存payloadを読める経路を残す
（Chain snapshot identityで採った v1 / v2 併存と同じ方法）。

`heat_time_basis` は焼鈍固有だが `heat_pattern` と対で意味を持つ。
`heat_pattern` を持つ形状の中へ閉じる。共有の最上位には置かない。

`blend` / `editor_state` は `SparseBlendCandidateInputs` へ移す。
Chain Coreは既にcandidate adapter経由でしか候補形状を見ないので
（[chain-execution.md](../chain-execution.md)）、移動先はadapterが決める。

### 3. shapeごとに4つの意味を定義する

形状を追加するとき、次を**すべて**定義しないと登録できないものとする。
どれかが未定義のまま入ると、候補の履歴と再現性が壊れる。

| 意味 | 定義するもの |
| --- | --- |
| persistence | 保存表現と、判別子を持たない旧payloadの読み方 |
| diff | 2つの候補の差をどう出すか（`candidate-difference-v1` が使う） |
| copy | 別Projectへコピーしたとき何を引き継ぎ、何を捨てるか |
| snapshot | 不変snapshotの解釈に必要な追加参照（Chainの `domain_references` と同じ考え方） |

### 4. UIはshape capabilityから入力surfaceを選ぶ

`task_id` や `activity_id` で画面を分岐させない。
Chainで導入した `GET /chain/candidate-capability` と同じ形で、
Projectが必要とする候補形状を宣言し、UIはそれを見てeditorを選ぶ。

view registryは Decision Activity で採った形（shape id → component の1 entry）に揃える。

### 5. Task固有IDによる分岐を増やさない

形状の選択は、Taskが宣言した候補形状から決まる。
Chainのcandidate adapterがChain Revisionの宣言から選ばれるのと同じにする。

## ケースCで確定した要件

可変長系列を入れる場合、`SeriesCandidateInputs` だけでは足りない。
[ケースC実測](extensibility-spikes.md#3-ケースc可変長温度系列task)の7項目から、次が確定している。

| 必要な型 | 何を持つ必要があるか | 根拠 |
| --- | --- | --- |
| `SeriesCandidateInputs` | 候補入力としての系列参照 | C-1, C-2（path正規表現とgroup key Literalが `heat_pattern` 以外を許さない） |
| `CanonicalSeries` | 点列 ＋ **元単位 ＋ 変換ID ＋ 除外点** | C-3（系列に単位を宣言できない）、C-6（`CanonicalCandidate` が `extra="forbid"` で正規化provenanceを置けない） |
| `SeriesQualityFinding` | 系列内位置を持つ不適格判定 | C-5（timestamp重複は**保存自体が拒否**されるため、値を残したまま不適格にできない） |
| `SeriesFeatureSpec` | 系列→特徴量の宣言 | `FeatureDefinition` がスカラー前提 |

**C-5が最も重要**である。
現行契約は不正な系列を保存できないため、このリポジトリが他の場所で採っている
「値は残しつつ品質findingとして不適格にする」（Tabular / Observation familyの
`eligible` + `exclusion_reasons`）を系列に対しては取れない。
系列を入れるなら、この方針の一貫性を先に決める必要がある。

上限点数（現行30点）も、系列を扱うなら形状側の宣言へ移す。

## やらないこと

- すべての候補形状を一つの万能schemaへ統合する
- 形状追加のために任意JSONやfree-form dictを共有schemaへ入れる
- Chain Coreや共通serviceへ形状別の分岐を足す
- 実needが出る前に `SeriesCandidateInputs` を実装する（計画P3）

## 着手の条件

1形状しかない状態でunion化だけを行っても、pydanticの制約で成立しない。
したがって着手は「2形状目が実際に必要になったとき」である。
現時点で最も近いのは、Chainのスカラー候補（`scalar/v1` adapter）を
製品機能として出すときで、そのとき `ScalarCandidateInputs` と
`SparseBlendCandidateInputs` を同時に切り出すのが自然な最初の一歩になる。
