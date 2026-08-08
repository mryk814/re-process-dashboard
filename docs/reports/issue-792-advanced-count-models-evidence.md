# Issue #792 Advanced Count Models evidence

`negative-binomial-regression.v1` と `zero-inflated-poisson-regression.v1` は、既存の safe な `numpyro.dense_posterior.v1` / `bounded-npz` adapter だけへ出力する experimental candidate です。新しい runtime、任意 Python artifact、Poisson fallback、暗黙の exposure=1、target の丸めは導入しません。

## 固定された比較 protocol

- target は non-negative integer と count unit を Task が明示する。連続値は build 前に拒否する。
- exposure がある場合は label、canonical input path、unit の三点を Task が明示し、各行の有限正値を offset として使う。exposure がない場合は「unexposed count」と記録し、rate として擬制しない。
- Feature Recipe は outer fold の training rows だけで fit する。既存の grouped / temporal `ValidationPlan` をそのまま使い、final Package だけが全 training cohort で refit する。
- NUTS の seed、draw、warmup、chain、diagnostics threshold は recipe / `inference-identity/v1` に固定する。NumPyro/JAX 不足、sampling failure、diagnostic failure は unavailable / build failure であり、Poisson などへの fallback はない。
- Poisson / NB / ZIP は同じ cohort、同じ fold digest、同じ exposure contract 上だけで比較する。単一 score による自動選択・自動 activation は行わない。

## 観測・quality evidence

count candidate は build 前に observed zero rate、mean、variance を記録する。quality は count MAE / RMSE、Poisson と NB deviance、log score、posterior predictive interval coverage、zero calibration、tail count calibration、exposure-stratified diagnostics を同じ quality cohort に残す。

NB の point estimate は expected count、`overdispersion` は別の distribution metadata である。ZIP の point estimate は expected count、`zero_probability` は構造的 zero gate の別 metadata であり、count process mean と混同しない。zero rate が高い事実だけでは ZIP を選ばない。

## Fixture scope と adoption decision

| fixture | protocolで確認すること |
| --- | --- |
| true Poisson | NB / ZIP が自動採用されない baseline |
| overdispersed NB | mean process と overdispersion の区別 |
| structural-zero + Poisson | expected count と ZIP zero probability の区別 |
| zeroが多い非ZIP | zero率だけで採用しないこと |
| varying exposure | explicit offset と exposure-stratified diagnostics |
| grouped / temporal count | outer-fold feature fit と split identity |

このPRの判定は **experimental** です。ローカルでは固定 seed 792 の6 fixtureを deterministic injected posterior fit で `compile -> fold/temporal train -> bounded NPZ -> safe runtime -> quality` まで実行しました。true Poisson、overdispersed NB、structural ZIP、zero-heavy non-ZIP、varying exposure、temporal countを含み、grouped k-foldとtemporal holdout、数値 exposure strata、全OOF rowのtail calibrationを確認しています。これはtrainer全経路の広さを確認するfixtureであり、実NUTSの収束証拠とは区別します。Model Playgroundの完了attemptは同じcohort・fold・exposure digestだけを比較evidenceとしてRunへ保存し、API／再起動後も返します。adoption memoや自動選択は作りません。

ローカルでは `uv run --extra dev --extra runtime-numpyro` で固定 sampling identity（2 chain、256 warmup、256 draw、seed 792）の実NUTSをNBとZIPの両方について実行しました。2件は18.48秒で通過し、sampling diagnostics、production artifact serializer、safe runtime predictionまで確認しました。`backend-science` shardも `runtime-numpyro` extraを明示的にinstallし、同じ2件をskipせずmerge前に実行します。OOF trainer全経路は上記injected matrix、実サンプラー式はこのdependency-gated smokeがそれぞれ所有します。

zero calibrationは、NBではposterior drawごとに `(r / (r + mu)) ** r`、ZIPではposterior drawごとに `g + (1 - g) * exp(-mu)` を計算してからOOF行とquality cohortへ集約します。ZIPの構造的gate平均は別の `structural_zero_gate_rate` として残し、total zero probabilityをexpected countと平均gateから逆算しません。log scoreはNB/ZIPともposterior drawごとの確率質量をlog-mean-expで混合し、tail rateは各foldのposterior predictive sampleを各OOF行へ保存して全quality cohortで集約します。

production claim、active Packageの置換、自動model selectionは支持しません。productionの個別Task evidenceが揃った場合だけ #781 へ接続します。
