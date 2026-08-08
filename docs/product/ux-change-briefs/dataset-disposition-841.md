# Dataset disposition #841

## ユーザーの問い

このDatasetは登録できるのか、登録後にどの操作で使えるのか、何を直せば利用範囲が広がるのか。

## 到着時の文脈

Profile WorkbenchでExcelとProfileを確認した直後。登録可否だけでなく、Entity／Observationを残したまま一部の工程条件だけが操作対象外になることを理解したい。

## 判断と判断しないこと

- 判断する: 登録可能性、Taskごとの保持・学習・候補参照・Similarity・Prediction inputの扱い、前Revisionとの差分。
- 判断しない: EntityやObservationの削除、Profileの自動修正、必要系列を推測した補完、実測値と予測値の再解釈。

## 作業記憶・再入場・復旧

- ReceiptとData Library detailの両方で、同じ`dataset-disposition/v1`のTask集計とdigestを表示する。
- Data LibraryをreloadしてもDataset Revisionとdisposition statusを復元する。
- `unknown_legacy` は過去データを推測せず、比較不能理由を表示する。
- Partial registration後もLineageとObservation browseは使えることを先に示す。

## 情報構造

1. Status: 利用可能／登録済み・一部操作対象外／legacy unknown
2. TaskごとのEntity、Observation、usable observation、unresolved parent
3. Operation matrix: 保持、必要系列なしで対象外、入力系列が必要
4. 改善候補と前Revision差分
5. Technical detail: disposition digestとSource／Profile revision

## 受入観察

- Previewで「登録可能」と「一部操作対象外」が同時に理解できる。
- ReceiptからData Library detailへ移っても同じstatus・Task counts・operation handlingが変わらない。
- 「excluded rows」ではなく、保持範囲と操作入力の対象外として読める。
- Source locator、personal path、raw row、Entity keyはブラウザ応答に含まれない。
