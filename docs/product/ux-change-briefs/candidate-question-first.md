# Candidate Question-first UX Change Brief

## 利用者の問い

どの候補を次の判断対象にし、その判断を予測区間、支持範囲、目標との差、近い実績で
説明できるか。

## 到達時点で確定していること

Project、Prediction Task、Package、候補集合、選択候補、URLで再開する分析面は確定済みである。
この変更ではCandidate identity、保存済み実測、URL resume状態を作り直さない。

## この画面で決めること

- 選択候補が目標と支持範囲を踏まえて判断対象になるか
- どの予測区間、出典、近い実績を根拠にするか
- 根拠を見た後に入力編集が必要か

## 構造

既存の判断サマリーと精密比較表を先頭に置き、候補の出典、分析面、近い実績を続ける。
全入力編集パネルは既定で閉じ、必要なときだけ左レールから開く。利用者が保存した
開閉設定は上書きせず、Exploreは条件を動かす画面なので従来どおり編集パネルを開く。
blend編集も比較表より後へ移し、判断前の入力作業に見せない。

## 守る証拠と安全境界

- 精密比較表、予測区間ラベル、支持範囲、目標達成表示を維持する
- Candidate選択、保存状態、競合表示、実測保存のownerを変更しない
- `view`、`project`、`surface`など#715のURL resume契約を変更しない
- ScreeningとDecision Activityの内部構造を変更しない

## 検証予算

focused unitでmode別の既定開閉を確認し、Web typecheckを実行する。fresh Playwrightは
Candidate比較の1経路に絞り、初期表示で判断サマリーと比較表が見え、全入力編集が閉じ、
開いた後の保存済み開閉状態とkeyboard focusが維持されることを確認する。
無関係なfull E2E、checkpoint、release acceptanceは実行しない。
