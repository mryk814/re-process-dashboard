# backend 開発ルール

ルートの [`AGENTS.md`](../AGENTS.md) に加えて、backend以下の変更では [`検証予算と停止条件`](../docs/operations/verification-budget.md) を適用します。

## 作業開始時

`change_class`、変更authority、focused evidence、実行しないgate、review、昇格条件、停止条件を短く宣言します。

- `micro`: typo、test expectation、局所metadata。最寄りcheck一つ＋diff
- `local`: 一つのapplication／contract owner。focused pytest＋必要な型check
- `structural`: package／registry／adapter／API boundary。focused tests＋関連guard
- `critical`: migration、restore、artifact safety、security、scientific identity。compatibility evidence＋独立review

`backend/**`というpathだけを理由に`backend/tests`全体へ広げません。変更したauthorityに最も近いtestを先に選び、特定できない場合は短いauthority調査を行います。

## debugging

`systematic-debugging`は`quick`から開始します。最初の仮説が外れた、複数layerへ到達した、再発／race／environmentが関係した場合だけ`standard`または`deep`へ昇格します。

## architecture

既存authority内の小さな責務移動は`local-boundary`として扱います。full architecture audit、全ADR読解、独立reviewを自動で要求しません。

transaction owner、composition root、migration readerを行数だけで分割しません。

## review

通常の`micro`／`local`変更はself-reviewで完了できます。

独立した敵対的reviewは次に限定します。

- migration／restore
- security／artifact loader
- persisted scientific identity
- model runtime semantics
- 複数authorityを横断する変更

## stop

変更したbehaviorまたはcontractが現在commitで一度証明され、diffがscope内で、新しい原因仮説が残っていなければ止めます。

上位gateがfocused testを包含して成功した後、そのtestを証拠目的に再実行しません。CIが同一commitのfull-suite ownerならlocal full pytestを重複実行しません。
