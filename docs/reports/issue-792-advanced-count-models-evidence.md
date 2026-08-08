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

このPRの判定は **experimental** です。今回ローカルで確認できたのは contract、safe runtime、fold identity の unit evidence です。NumPyro/JAX がこの環境にないため、表の real-NUTS synthetic fixture は fresh `backend-science` CI が merge 前に所有します。production claim、active Package の置換、自動 model selection は支持しません。production の個別 Task evidence が揃った場合だけ #781 へ接続します。
