# e2e 変更・実行ルール

ルートの [`AGENTS.md`](../AGENTS.md) に加えて、[`検証予算と停止条件`](../docs/operations/verification-budget.md)を適用します。

## product spec

画面や操作経路の変更では、変更したjourneyを反証する対象spec／grepをfresh serverで一度実行します。

- 無関係なdefault Playwright全体を毎PRで実行しない
- 同じjourneyを複数runnerで重複確認しない
- fresh対象specが成功した後、server再利用版を証拠目的に再実行しない
- browser確認が不要なtest-only文言修正へE2Eを追加しない

## infrastructure

shared helper、fixture、port allocation、global setup／teardown、runner configを変える場合は`structural`として扱い、代表specの単独実行と並列／cleanup boundaryを確認します。全specを無条件に流す前に、影響するrunner fixtureを一つずつ反証します。

## reviewとstop

product specの局所変更はself-reviewで完了できます。shared mutable state、cleanup owner、parallel worker、process lifecycleを変える場合だけfocused-peer reviewへ昇格します。

対象journeyが現在commitで一度成功し、cleanup／identityに新しい仮説が残っていなければ止めます。
