# apps/web/tests 変更ルール

[`検証予算と停止条件`](../../../docs/operations/verification-budget.md)を適用します。

- 変更したtestを直接実行するのは反復loopとして一度でよい
- 最終的にWeb unit全体を実行し、そのtestを包含して成功した場合、同一commitで直接testを再実行しない
- test-only変更でWeb build、Playwright、full backend suiteを自動追加しない
- test追加は過去の回帰、decision safety、state identity、accessibility阻害等の具体的riskを守るものへ限定する
- implementation detailを追認するだけの重複testを増やさない

通常はself-reviewで完了できます。共有test helper、runner、global setupへ影響する場合だけfocused-peer reviewへ昇格します。
