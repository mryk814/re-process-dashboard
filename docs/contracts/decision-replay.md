# Decision Replay

Decision Replayは、保存済みevidenceを一つの判断時点へ束ね、後着情報と分離して振り返るretrospective activityである。workflow承認、個人評価、過去判断の自動訂正ではない。

## identity

`decision-case/v1`はProject、Task contract、Objective Definition、decision timestamp、Candidate Revision集合、Prediction Snapshot集合、selected Candidateまたは`no_decision`、任意のActor付きrationale、outcome policyを固定する。Case本体は追加後に更新しない。後着Actualは`decision-case-actual-attachment/v1`としてCaseとActualを一件ずつ結ぶ別の追加専用recordである。

`decision-replay-run/v1`はCase identity、allow-list済みpolicy、明示選択した後発hindsight Project／Package／Dataset provenance、参照したActual attachment identityとCandidate revisionからsemantic identityを作る。Runも追加専用である。

## evidence cutoff

Historical layerへ入れられるCandidate RevisionとSnapshotは、Caseのdecision timestamp以前に保存済みでなければならない。Snapshot payload内のCandidate RevisionはCaseの固定集合と一致し、全targetのpredictionとPackage digestを保持する。

Actual attachmentはdecision timestampより後に保存されたActualだけを受け付ける。Caseの固定Candidate set、同一Project、同じCandidate revision、参照Snapshot、outcome targetがすべて一致しなければ拒否する。同じCaseへ同じActualを二度追加できない。

hindsight再評価はCaseの元Projectをcurrentとして使わない。requestは別の後発single-task Projectを明示し、そのTask ID／Task contract digest／Objective digest／target semanticsがCaseと一致する場合だけ受け付ける。Runには選択したProject ID、Package ref／manifest digest、Dataset View revision、Dataset source SHA-256を別のprovenanceとして保存・表示する。

## P0 replay

- realized outcomeは固定Snapshotのpredictionと添付済み後着Actualをtargetごとに照合する。Actual attachmentが一部または未到着でもRunを保存し、未観測targetを返す。
- `primary-objective-point-estimate/v1`だけをallow-listし、当時のCandidate集合とSnapshot値だけへ適用する。現在のCandidateやPackage結果をalternative selectionへ混ぜない。
- similar CaseはTask ID、Task contract digest、Objective digest、target集合が一致するCaseだけを返す。結果は元Project、Snapshot、Actualへ戻るidentityを含む。

自動的なregret、rationale採点、Actor ranking、model trainingへのrationale利用は行わない。

## authority

- 型: `backend/src/decision_workbench/contracts/decision_replay_contracts.py`
- cutoff／replay: `backend/src/decision_workbench/application/decision_replay.py`
- 追加専用保存: `backend/src/decision_workbench/persistence/decision_replay_repository.py`
- API／生成型: FastAPI OpenAPIと`apps/web/src/generated/`
- UI: `apps/web/src/features/workbench/DecisionReplayPanel.tsx`
