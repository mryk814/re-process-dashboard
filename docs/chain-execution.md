# Chain実行と証跡

多段Chainは既存の単段previewとは別の実行面を持つ。
`POST /api/projects/{project_id}/chain/candidates/{candidate_id}/executions` が、Projectに固定されたChain Revisionの順序でA→B→Cを実行する。

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
