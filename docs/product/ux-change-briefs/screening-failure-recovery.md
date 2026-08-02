# Screening failure recovery

- Issue: #703
- Change class: structural
- Authority: Screening実行失敗の表示と再実行

## 判断場面

利用者は入力条件を比較するために範囲探索を実行する。失敗したときは、原因を直して同じ条件を再実行したい。直前の成功結果がある場合は、それを判断の退避先として残したい。

## 現状の破綻

APIのvalidation情報を単一の文へ潰し、失敗時に表示中の成功RunとURL上のRun identityを消している。そのため、何を直すか、入力が残っているか、前回の結果へ戻れるかが分からない。

## 変更後の構造

1. 実行ボタンと同じ条件編集面に、失敗の種類、原因、該当項目、保持状態、再実行をまとめて表示する。
2. validation失敗ではAPIのmessageとfield errorを利用者向けラベルへ翻訳する。
3. network／予期しない失敗ではtransportやローカルpathを表示せず、応答喪失後の重複Runを避けるため保存済みRun確認を先に案内する。
4. 失敗した要求はRunとして保存せず、直前に成功したRun、結果面、選択、URL identityを変更しない。

## 禁止事項

- 失敗時に成功済みRunを空表示へ置き換えない。
- raw stack、filesystem path、transport messageを表示しない。
- 入力を初期化しない。
- 自動retryや旧経路fallbackを追加しない。

## 受入観察

- API 422で原因とfield errorが表示される。
- 入力値は保持され、同じ条件で再実行できる。
- 直前の成功結果がある場合、その結果とRun URLは失敗後も残る。
- validation retry成功後はfailure surfaceが消え、新しいRunへ切り替わる。
- network／runtime失敗では未保存と断定せず、保存済みRun一覧を再取得してから利用者が再実行を判断できる。
- failure surfaceは`role=alert`で到達でき、狭幅でも横あふれしない。
