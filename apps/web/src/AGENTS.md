# apps/web/src 変更ルール

上位の [`apps/web/AGENTS.md`](../AGENTS.md) に加えて、[`検証予算と停止条件`](../../../docs/operations/verification-budget.md)を適用します。

## 変更class

- `micro`: typo、label、既存token、明らかなaria欠落。最寄りcheck＋diff、self-review
- `local`: 一つのcomponent／hook／state transition。focused Web unit、必要な場合だけ対象browser、self-review
- `structural`: navigation、form、handoff、共有state owner。focused unit＋対象journey＋focused-peer review
- `critical`: scientific identity、API meaning、security／migrationへ実際に到達した場合のみ独立review

画面構造を変えない`micro`／`local`変更で、完全なUX Change Brief、全viewport、全failure state、Scenario Journey、default Playwrightを要求しません。

interactionを変えた場合は、そのinteractionを反証する最小のfresh browser evidenceを一つ選びます。Web unit全体がfocused testを包含して成功した後、focused testを再実行しません。

変更した受入観察が現在commitで一度証明され、新しいUX／state仮説が残っていなければ止めます。
