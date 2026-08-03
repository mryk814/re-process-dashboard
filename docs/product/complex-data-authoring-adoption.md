# Complex Data Authoring adoption memo

Issue: #757
Decision: repeated measurements / Observation familyだけを最初の再利用可能authoringへ採用する。

## 比較

| 評価軸 | repeated / Observation | longitudinal curve | relational workbook |
|---|---|---|---|
| 現行Task | 4 Task、共通Observation adapterとgrouped validationあり | 複数Profile familyに分散 | 複数Taskがあるがjoinとentity意味がTaskごとに異なる |
| 新sourceで変わる箇所 | 列対応、観測ID、group ID、target cohort | axis、系列切断、補間可否、Candidate化 | table、key、join方向、学習行の生成 |
| identity | observationとgroupを別に固定 | row、group、time/axisを固定 | entity、relation、observationを固定 |
| target cohort | targetごとの非欠測観測 | axis範囲とtarget欠測の両方 | join成立とtarget欠測の両方 |
| Validation Plan | groupを跨がないk-foldを共通化可能 | group splitに加えてaxis外挿評価が必要 | entity leakageを防ぐTask固有splitが残る |
| Candidate shape | groupの条件値をそのままCanonical Candidateへ接続可能 | 曲線全体か条件値かがTaskで異なる | 複数entityのどれをCandidateにするかがTaskで異なる |
| Estimator | 既存allow-list ridgeを再利用可能 | curve固有特徴量の合意が先 | canonical rowを作るまでEstimatorへ渡せない |
| UIで確認する意味 | 1行の観測粒度、観測ID、group、入力、実測 | 加えてaxis roleと系列境界 | 加えて各tableのkeyとjoin方向 |
| Task ID分岐 | 0。Task contractとadapter registryから列を列挙 | 現状はfamily差を吸収できない | 現状はTask固有loader差を吸収できない |

Observation familyは、既存のProfile canonicalization、canonical training dataset、grouped model builder、Package verifierまで共通 seam が揃い、欠けていたのがsource側のtyped authoringだけだった。二つ目の現実的fixtureとして、同一溶接条件から二本ずつ採取した引張試験片の単一表CSVを使い、既存の複数sheet溶接教材とは異なるsource layoutを反証にした。

longitudinal curveとrelational workbookは未対応の欠陥ではない。前者はaxis/series semantics、後者はjoinとCandidate authorityの合意が不足しているため、万能ETLを作らずspecialized routeを維持する。Capability Atlasには、repeated measurementsだけを再利用可能な外部authoringとして、残る二familyを`specialized_only`の制限として記録する。

## 採用した契約

- source: UTF-8 CSVまたは可視sheetが一つだけのExcel
- grain: 1行=1個別観測
- identity: 一意なobservation IDと反復条件を表すgroup IDを別列で指定
- inputs: 選択Taskの全Canonical inputへ一対一に対応
- targets: 選択Taskの全outputへ一対一に対応し、非欠測targetごとのcohortを保持
- metadata: technical metadataは学習入力へ自動昇格しない
- fixed context: 現行の対象Taskでは全Canonical inputが候補入力であるため、このsurfaceから固定条件へ読み替えない。固定条件を持つTask contractを採用するときにtyped fieldとして追加する
- validation: allow-list済み`grouped-k-fold`
- feature recipe: allow-list済み`observation-identity-v1`
- estimator: allow-list済みridgeと正のalpha
- source identity: authoring時のSHA-256をProfileへ固定
- retry identity: source SHA、Task、binding契約からProfile IDを決定し、同一契約の再試行でProfileを増殖させない

任意join、任意expression、任意Python、暗黙の単位換算は受け付けない。複数可視sheetはrelational shapeとしてfail-closedにする。

## UX change brief

対象ユーザーは、同一条件の反復試験を持つData Contributorである。従来は既存の複数sheet Profileを選ぶしかなく、単一表の反復データを独立行Tabularへ誤登録する危険があった。

比較した認知モデルは次の二つ。

1. 既存Profileを先に選び、その構造へsource名を合わせる。
2. sourceを先に置き、「1行」「観測ID」「反復group」の意味を確認してからTaskのCanonical input/outputへ対応付ける。

単一表の外部データでは2を採用する。今回の画面入口はCSVに限定し、API/parserが受け付ける単一sheet ExcelはUI未提供の制限として残す。画面は `source → grain/identity → input/target mapping → 明示確認 → Profile検証 → Dataset Revision登録` の順にし、推測した列を無確認で保存しない。Packageのactive切替とProject作成は既存どおり別の明示操作である。

受入観察:

- group列に同じ値を持つ複数行が、別々の観測のまま残る。
- target欠測は他targetの利用可能行を落とさない。
- 複数表Excelは「扱える」と誤判定されない。
- 保存済み既存Observation ProfileのdigestとPackage identityは変わらない。
