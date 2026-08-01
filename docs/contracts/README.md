# 実装契約

このdirectoryは、実装とtestが従う現在の意味境界、identity、invariant、制限の正本です。
ownerは各backend／frontend／modeling領域で、schema、保存意味、execution、feature、Package、Chainが変わる時に更新します。

- [Stage A 配合逆算](blend-optimization.md)
- [Chain評価](chain-evaluation.md)
- [Chain実行](chain-execution.md)
- [Curation and Proposal](curation-and-proposal-architecture.md)
- [検討アクティビティ](decision-activities.md)
- [特徴量パイプライン](feature-engineering.md)
- [Feature Recipe](feature-recipe.md)
- [推論実行](inference-execution.md)
- [Model Package契約](model-package-contract.md)
- [Objective Definition](objective-definition.md)
- [Project Design Space](project-design-space.md)
- [参照データloop](reference-data-loop.md)
- [Source data lifecycle](source-data-lifecycle.md)
- [疎な配合候補](sparse-blend-contract.md)
- [可変長系列](variable-length-series.md)
- [生成済みTask inventory](task-inventory.json)

`task-inventory.json`は同梱Taskだけを対象にした生成物です。個人用Task storeの内容は含みません。
直接編集せず、`npm run task:inventory`で更新し、`npm run task:inventory:check`でdriftを検査します。
