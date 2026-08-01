# Project Design Space

Project Design Spaceは、単一Task Projectで「今回、作れる・試せる範囲」を固定する不変契約です。
TaskDefinitionの科学的な許容範囲を広げることはできず、範囲探索とロバストネス解析が同じ契約を参照します。

## Identity

- `design_space_id`と`revision`で人が追跡できる版を表す
- 全定義のsemantic digestをProjectへ固定する
- 新しいProjectは、明示指定がなければTask許容範囲からrevision 1を生成する
- 「このプロジェクトの続き」と候補コピーでは、元Projectの定義をそのまま固定する
- 続きとして作成したProjectは`inherited_predecessor`として継承元を区別する
- Design Spaceを変更する場合は既存Projectを更新せず、新しいProjectを作る
- 既存Projectへ履歴を推測して補完しない。未固定のProjectは`unbound_legacy`と表示する

## Validation

Design SpaceはTaskDefinitionに対して保存前に検証します。

- numeric range／候補値はTaskのallowed range内で、integer／step（Taskのlattice originを含む）／log scaleを変えない
- categorical choicesはTaskの選択肢の部分集合
- composition total／balanceは宣言済み組成と単位だけを参照
- conditional activationは宣言済みcontrollerと入力だけを参照

範囲探索が作るrun-local Design Spaceは、Project Design Spaceをさらに狭めることしかできません。
stepのnarrowingもTaskのallowed rangeをoriginとする同じlatticeを保ちます。
ロバストネス解析の公差範囲も同じnumeric domain内に限定します。clipは行いません。

## Provenance

Screening RunとDecision Activity Runは、実行時のProject Design Space digestとbinding provenanceを保存します。
保存済みRunは、ProjectやTaskの後続変更で自動再評価しません。
