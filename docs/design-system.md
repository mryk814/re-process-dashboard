# Material Decision Workbench design system

Accepted concept: `docs/design/candidate-workbench.png` (1600 x 1024).

## Visual direction

- Background: pale blue-gray `#F5F7FA`; surfaces: true white `#FFFFFF`.
- Header: `#0F1B2D`; primary/selection: `#1F5FC4`; border: `#D8E0EA`.
- Main text: `#18202C`; muted text: `#5D6775`; amber is reserved for caution.
- Typography: Noto Sans JP-compatible system stack; tabular numerals; 14-16px data UI.
- Geometry: 4-6px radii, fine borders, almost no shadow.

## Container model

Quiet header, left inspector rail, central comparison table and charts, right evidence rail. Tables and open rails carry comparison; cards are limited to summaries and warnings.

## Component inventory

- Header navigation and one primary action.
- Candidate selector, unit-aware numeric fields, copy/delete/add controls.
- Editable candidate comparison table with selected column state.
- Heat-pattern chart plus point table; temperature points are draggable and numerically editable.
- Property prediction table with interval whiskers and support status.
- Similar-experiment list and reproducibility metadata.

## Table rules

- Column headers are centered. Row headers and identifier/text columns stay left-aligned.
- Numeric cells and numeric inputs are right-aligned and use tabular numerals.
- A variable uses the same decimal places across every row so candidates can be compared vertically.
- `TaskDefinition.display_decimals` is the upstream default. A project's sparse `display_decimals` map overrides it without rounding stored or calculated values.
- Editable tables may format an idle numeric input with trailing zeroes; editing and persistence continue to use the original numeric value.

## Copy lock for first viewport

Material Decision Workbench / プロジェクト / 候補比較 / データ探索 / 範囲探索 / 焼鈍条件の候補検討 / プレビュー / 詳細予測を実行 / 選択候補 / 入力条件 / 候補操作 / 候補を追加 / 候補比較表 / ヒートパターン / 応答曲線 / 予測特性 / 類似する過去実験 / 予測の根拠。
