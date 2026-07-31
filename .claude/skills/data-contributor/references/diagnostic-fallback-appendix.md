# Diagnostic／fallback appendix

既定経路ではない。UIで`ui_missing`／`ui_blocked`／`setup_only`を記録し、継続が明示承認された後だけ使う。
read-only診断は原因切り分けに限る。

```powershell
$source = "C:\path\to\data.xlsx"
$task = "<task-id>"
$profile = "C:\path\to\personal-profile.json"

npm run dev:doctor -- --source $source
uv run python backend/scripts/operations/profile_workbench.py inspect $source
npm run model:diagnose -- --task $task --source $source --profile $profile
```

画面が生成したTask scaffold、model build、verify、promote commandは表示内容を正本として使う。
source、Profile、Task identityを工程間で変えない。
実行前に目的、期待、visible evidence、承認者をonboarding logへ残し、実行後は`fallback_used`とする。

失敗時は最初に失敗したcommand、終了code、入力identity、エラー全文を残す。
元データの上書き、DB direct write、mutation API、`model:activate`はfallbackに含めない。
