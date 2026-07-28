# Stage A 配合逆算

Issue #169 は [多段ChainアーキテクチャADR](../decisions/multistage-chain-architecture.md)
の線形境界だけを実装する。

## 計算境界

- フープ、充填率、Design Space revisionは基準候補から固定する。
- solver変数はwhole-wire絶対質量分率 `z_i` とし、保存時だけ
  `core_ratio_i = 100 z_i / fill` へ戻す。
- 使用原料集合を固定する場合はHiGHS LPを使う。原料採否、選択数、
  群内選択数をsolverに判断させる場合だけHiGHS MILPを使う。
- 目的関数は粉体配合コストまたは基準配合からのL1距離のいずれかを
  ユーザーが選ぶ。
- 複数の成分許容範囲、合計、原料上下限、群合計、
  Design Spaceの `allowed_material_ids`、原料選択数、
  群内選択数、balance原料を同じ問題へ入れる。

ここで「使用可能な原料」は商用catalogの調達区分ではなく、固定した
Design Space revisionの `allowed_material_ids` を指す。調達区分（常用、
条件付、試作限定、廃止予定）は判断用メタデータとして表示するが、
solverの採否制約には変換しない。現在のデモ用active Design Space r2は、
形式とUIの検証を優先するため、商用catalogの252原料をすべて許可している。

物性値からの逆算、Stage B/Cを含む逆算、充填率と配合の同時最適化、
solverが複数案から自動的に「最良」を採用する動作は含めない。

## 可解性と保存

通常問題が不可解な場合は、上下限制約へ非負slackを追加した補助問題を解く。
返す値は「緩和すると可解性が回復する可能性がある制約」であり、
IISや矛盾の証明とは呼ばない。

可解な結果だけを通常の `Candidate` として保存する。
来歴には基準candidate revision、SciPy/HiGHS方式、目的関数、科学master、
商用catalog、Design Space、要求digestを固定する。このため、候補比較、
編集、詳細予測、snapshotは専用経路を持たず既存機能をそのまま使う。
