# Decision Replay

Decision Replayは、保存済みevidenceを一つの判断時点へ束ね、後着情報と分離して振り返るretrospective activityである。workflow承認、個人評価、過去判断の自動訂正ではない。

## identity

`decision-case/v1`はProject、Task contract、Objective Definition、decision timestamp、Candidate Revision集合、Prediction Snapshot集合、selected Candidateまたは`no_decision`、任意のActor付きrationale、後着Actual identity、outcome policyを固定する。Caseは追加専用で、Candidate、Snapshot、Actual、Decision Activityのidentityを変更しない。

`decision-replay-run/v1`はCase identity、allow-list済みpolicy、実行時のcurrent Package、参照したActual identityからsemantic identityを作る。Runも追加専用である。

## evidence cutoff

Historical layerへ入れられるCandidate RevisionとSnapshotは、Caseのdecision timestamp以前に保存済みでなければならない。Snapshot payload内のCandidate RevisionはCaseの固定集合と一致し、全targetのpredictionとPackage digestを保持する。

Actualはdecision timestampより後に保存されたものだけをRetrospective layerへ入れる。current Packageによる再評価は`hindsight`と型・表示の両方で明示し、当時のPredictionを上書きしない。

## P0 replay

- realized outcomeは固定Snapshotのpredictionと後着Actualをtargetごとに照合する。Actualが一部または未到着でもRunを保存し、未観測targetを返す。
- `primary-objective-point-estimate/v1`だけをallow-listし、当時のCandidate集合とSnapshot値だけへ適用する。現在のCandidateやPackage結果をalternative selectionへ混ぜない。
- similar CaseはTask ID、Task contract digest、Objective digest、target集合が一致するCaseだけを返す。結果は元Project、Snapshot、Actualへ戻るidentityを含む。

自動的なregret、rationale採点、Actor ranking、model trainingへのrationale利用は行わない。

## authority

- 型: `backend/src/decision_workbench/contracts/decision_replay_contracts.py`
- cutoff／replay: `backend/src/decision_workbench/application/decision_replay.py`
- 追加専用保存: `backend/src/decision_workbench/persistence/decision_replay_repository.py`
- API／生成型: FastAPI OpenAPIと`apps/web/src/generated/`
- UI: `apps/web/src/features/workbench/DecisionReplayPanel.tsx`
