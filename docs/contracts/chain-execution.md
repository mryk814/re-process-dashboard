# Chain実行と証跡

多段Chainは既存の単段previewとは別の実行面を持つ。
`POST /api/projects/{project_id}/chain/candidates/{candidate_id}/executions` が、Projectに固定されたChain Revisionの順序でA→B→Cを実行する。

Chain候補も単段候補APIへ混ぜない。
`/api/projects/{project_id}/chain/candidates`で疎な配合と外部contextをrevision付きで作成・一覧し、
`PUT .../{candidate_id}`と`GET .../{candidate_id}/revisions/{revision}`で再編集と履歴参照を行う。
作成・更新時に、Projectが固定した科学変換master、商用catalog、Design Spaceのrevisionをサーバー正本と照合する。
配合制約に違反するdraftは理由付きで保存できるが、Chain実行はできない。

## Chain Coreとcandidate adapter

Chain実行は2層に分かれる。

**Chain Core**（`application/chain_execution.py`, `chain_uncertainty.py`）が持つのは、
Stage順序、binding解決、単位変換、部分再計算、鮮度判定、古い応答の破棄、memo、
generation競合制御、provenance、snapshotである。
**Coreは候補の形状を仮定しない。**

**Candidate adapter**（`application/chain_candidate_adapters.py`）が持つのは、
候補が何で出来ているかに依存する部分である。

- 候補が外部入力として公開する名前空間
- 候補の妥当性検証と初期候補のdomain payload
- 決定論的Stageの実行と、その出力形状
- snapshotの解釈に必要な追加revision参照

adapterはallow-listされており、Chain Revisionが宣言したStage構成から選ばれる。
Task IDやProject名からは選ばない。

| adapter | 選択条件 | 候補 |
| --- | --- | --- |
| `sparse_blend/v1` | 決定論的Stageが1段 | 疎な配合明細＋施工・試験contextのスカラー |
| `scalar/v1` | 決定論的Stageなし | 工程条件のスカラーとカテゴリのみ |

画面を出す前に `GET /api/projects/{project_id}/chain/candidate-capability` で
必要な入力面を判断する。疎配合を使わないChainに対して疎配合契約APIは409で明示的に断る。
現行のChain Workbench画面は `sparse_blend/v1` 専用であり、他のadapterでは理由を表示して停止する。

## snapshot identityとdomain参照

保存済みsnapshotのidentityは、Chain Coreの参照（Chain Revision、候補ID、候補revision、adapter ID）と、
adapterが宣言した `domain_references` を分けて持つ。
疎配合Chainは `design_space` と `commercial_catalog` をdomain参照として記録する。
疎配合を使わないChainのsnapshotはdomain参照を持たない。

保存済みの `chain-snapshot-identity/v1` は不変なので、そのまま読める形で残している。
新しいsnapshotは `chain-snapshot-identity/v2` で保存する。

## 再計算単位

各Stageのcanonical inputを正規化JSONとして組み立て、そのcontent hashを計算する。
永続memoのidentityは次の組であり、入力内容だけで異なるPackageの結果を共有しない。

- Stage ID
- canonical input content hash
- Stage contract digest
- Package manifest digest

上流結果と外部contextをbindingどおりに組み立てるため、試験温度だけを変えた場合はA/Bのhashが変わらずCだけが再実行される。
原料明細を変えた場合はAのhashが変わり、その結果を入力にするB/Cも再実行される。

## 競合と鮮度

実行要求はProject/candidateをscopeとしてrequest IDを持つ。
既定では編集停止後250 ms待ってから実行し、その間に新しいrequestが来た場合、古いrequestは`superseded`として破棄する。
実行中に新しいrequestが来た場合も、完了した古い結果を現在状態へ書き戻さない。

各Stageは次のいずれかを返す。

- `latest`: requested inputと結果のdigestが一致する
- `running`: requested inputを計算中
- `stale`: 以前の成功結果を保持しているが、現在の下流入力には未追従
- `failed`: 今回の実行は失敗した。以前の成功結果があれば保持する

Stageが失敗しても、成功済みの上流結果と以前の下流結果は削除しない。
`requested_input_digest`と`result_input_digest`の違いで、保持結果が古いことを機械的に判定できる。
candidate revisionの保存直後にも再分類し、試験温度だけならA/Bは`latest`のままCだけを`stale`にする。

## immutable snapshot

全Stageが`latest`のときだけChain snapshotを作成できる。
snapshotは次を一つの不変recordとして保存する。

- Chain Revision ID/digest
- Design Space revision
- candidate ID/revision
- commercial catalog revision
- 全Stageのcanonical input/content hash
- 全Stageの結果
- 全Stageのcontract digestとPackage manifest digest
- 実行request ID

snapshotと最新実行状態、Stage memoはSQLiteへ保存されるため、API再起動後も同じRevisionと結果を読み直せる。
元Excelや既存の単段prediction snapshotへChain結果を書き込まない。

## 分布伝播は点推定とは別の明示実行

通常の編集では従来どおり点推定だけを自動更新する。
上流Stageの不確かさを下流へ流す処理は
`POST .../distribution-runs` を利用者が明示して実行し、別の不変証跡として保存する。
点推定の `ChainExecution` や判断時点の `ChainSnapshot` を分布結果で上書きしない。

既存Model Packageは不変とし、サンプリング対応の追記でmanifest digestを変えない。
アプリ本体のallow-listが固定PackageのIDとmanifest digestを完全一致で承認し、
runtimeが `StageSampleRuntime` を実装した場合だけ有効になる。
現在のB/C Packageに対して承認する方式は
`independent-residual-normal-bounded-from-q05-q95/v1` である。
これは各出力の経験的な5–95%区間から標準偏差を近似し、出力ごとに独立な正規残差を発生させ、
exact allow-listで固定した物理境界をStage固有・伝播後の双方へ適用する方式である。
事後分布でも出力間相関を持つjoint distributionでもない。

各Stageの表示とAPIは次を分ける。

- `stage_uncertainty`: 上流入力を点推定へ固定した、そのStage自身の残差不確かさ
- `propagated_uncertainty`: sample可能な上流Stageを通し、当該Stageの残差も加えた不確かさ

固定seed、sample数、Chain Revision、candidate revision、点推定request ID、
StageごとのPackage digestとseedをprovenanceへ残す。
sample非対応Stageは理由を明示し、そこより下流の
`propagated_uncertainty` を空にするが、既存の点推定結果は変更しない。
