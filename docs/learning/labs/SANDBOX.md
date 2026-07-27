# Learning Lab sandbox規約

Labはproductionの状態を使わず、破棄できる小さな入力と出力だけで実行します。
読者がcommandを中断しても、通常起動するアプリのWorkspace、active Package、元データが変化しないことを境界にします。

## 書込み先

Labがrepository内へ書ける場所は`artifacts/learning-labs/<lab-id>/`だけです。
各Labはmanifestの`writes`へ具体的なdirectoryを宣言し、共通rootそのものを削除対象にしません。

次のpathは読取りの有無にかかわらず、Labから書き換えません。

- `data/source`
- `data/`配下のproduction Workspace DB
- `models/active-packages.json`
- `models/active-transforms.json`
- `models/packages`
- `apps/web/src/generated`

実行可能Labはsetup前、run後、reset前後にprotected pathのfingerprintを比較します。
fingerprint一致は「書込みがなかった」ことの補助証拠であり、filesystem監査や権限分離の代わりではありません。
Lab script自体も出力pathをallow-listへ解決してから書込みます。

## 入力

toy fixtureは`docs/learning/labs/fixtures/`へ置き、実データを複製しません。
ID、unit、期待値、toleranceを人が読める形で持たせます。
randomnessが必要なLabはseedをmanifestまたはfixtureへ固定します。

Labはnetworkへ接続せず、credentialと環境変数上のsecretを入力にしません。
外部serviceの応答が必要な課題は、内容を限定した合成fixtureへ置き換えます。

## commandの契約

- `setup`：allow-list内に専用outputを作り、protected fingerprintを記録する
- `run`：toy fixtureから結果を作り、宣言済みoutputだけへ保存する
- `verify`：exact structure、数値tolerance、semantic expectationを検査する
- `reset`：対象Labのoutputだけを削除し、失敗を黙って無視しない

book buildはLabを自動実行しません。
読者または教材検証者がmanifestのcommandを明示実行します。

## 新しいLabを追加する条件

1. `manifest.json`へLabを登録する。
2. `writes`を`artifacts/learning-labs/<lab-id>`以下へ限定する。
3. `must_not_write`へmanifest共通のprotected pathをすべて含める。
4. 実行可能Labではsetup、run、verify、resetをすべて用意する。
5. runtimeを10分以内に制限し、成功条件と失敗時の確認先を本文へ置く。
6. `node docs/learning/test-labs.mjs`と`node docs/learning/check-labs.mjs`を実行する。

checkerはmanifestとdocumentの対応、fixtureとcommandの存在、書込みallow-list、protected pathとの重なりを検査します。
command文字列にprotected pathが現れる場合も拒否します。
