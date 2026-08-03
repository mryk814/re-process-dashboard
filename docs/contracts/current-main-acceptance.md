# Current-main acceptance receipt

`e2e/current-main-acceptance-*.spec.ts` は、Capability Atlasが表す最新mainの代表journeyを確認する専用specです。
既定のPlaywright suiteからは除外し、`npm run test:e2e:current-main-acceptance` がJourney A/Bを別々のfresh API・Web・DB・個人storeで一度ずつ実行します。
retryは0固定です。

実行前に `docs/contracts/capability-atlas.json` のSHA-256を
`CURRENT_MAIN_CAPABILITY_ATLAS_DIGEST=sha256:<digest>` として明示します。
runnerはファイルから計算したdigestとの一致を確認し、各receiptと集約reportへ同じdigest・tested commitを記録します。

`current-main-acceptance/v2` receiptでは、journeyごとの必須checkを全件列挙します。
各checkは `passed` または `not_run`、owner、check固有の証拠resourceとassertion discriminatorを持ちます。
checkごとに許可するresource kind、必須identity field、assertionを固定し、一つのresourceを複数checkの成功根拠へ使い回せません。
必要なcheckが一つでも `not_run` ならreceipt全体は `incomplete` です。
resourceはProject、Candidate、Run、Snapshotだけでなく、Source、Profile Revision、Dataset Revision、Model Package、Graph draft/revisionまで各identityを保持します。
実際に確認していない挙動を一括の成功文言で代用しません。

runnerは開始時、Journey A後、Journey B後に `git status --porcelain` が空であることを確認します。
spec失敗、receipt欠落、receipt不正、dirty treeのいずれでも、集約report自体は書き出し、
該当journeyの全checkをowner付き `not_run` としたfailure receiptと診断を残します。

Journey Aは公開CALCE CSVをData Library UIから新規Taskとしてonboardingし、readinessへの反映、
標準Estimator Packageのbuild・verify・promote・登録、Model Library表示を確認した後に判断journeyへ進みます。
同梱済みPackageの参照だけでonboarding成功を代用しません。

画面を実際に操作して判断する確認は自動化の完了条件ではなく、
`manual_visual_judgment.status = not_run`／`owner = user` として明示的に分離します。
