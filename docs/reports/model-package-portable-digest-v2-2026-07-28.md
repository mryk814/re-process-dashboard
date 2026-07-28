# Model Package portable digest v2 release evidence（2026-07-28）

## 対象

| Task | active | previous |
|---|---|---|
| `mpea-room-tensile-v1` | `mpea-room-tensile-ridge-v2` | `mpea-room-tensile-ridge-v1` |
| `mpea-hardness-process-v1` | `mpea-hardness-ridge-v2` | `mpea-hardness-ridge-v1` |

v1 manifestとartifactは変更せず、v2を別directory・別Package ID・version `2.0.0`として生成した。
`active-packages.json`の`previous`はv1を指し、Windows配布物にはactiveとpreviousの両方を同梱する。

## Provenance契約

- raw source: `data/source/external/mpea_ground_truth_18021833.csv`
- raw source SHA-256: `a3b4230ff150e32ed51050a4813fc3fbad01b2bc2fc61b5d4fb8a5be3cc5e476`
- digest algorithm: `canonical-json-finite-float15/v2`
- room tensile feature dataset: `sha256:83923f79f38edc80d309c458568a9e51d0c9fb63d15fbfb571c902812d3c3063`
- hardness feature dataset: `sha256:135d2360c8762f0d669dbdb5abca0c56c314eaf958fa3306370eb830abc63b64`

v2はfinite floatを15有効桁のtagged decimalへ変換し、`-0.0`を`0`としてsemantic digestを作る。
raw source digest、Task contract、Profile digest、行、特徴量名・順序の検証は維持する。
runtime予測値や一般のsemantic digestは丸めない。

## 生成・検証

```powershell
uv run --extra dev python backend/scripts/build_external_tabular_packages.py mpea-room-tensile-v1 mpea-hardness-process-v1
npm run task:inventory
npm run task:inventory:check
```

- builderは両v2 Packageを生成後、production verifierとsmokeを通過した。
- Windows/Python 3.13とLinux/Python 3.12でroom tensileのv2 digestが同じ`83923f...3063`になることを実測した。
- Linux containerでactive Package全Taskのregistry contract testを実行し、MPEA両v2を含めて合格した。
- Package、catalog、source byteを含むfocused backend suiteは164件合格した。
- Windows full pytestは959件合格、4件skip。
- GitHub Actions run `30334967797`のLinux normal change gateは成功した。
- `task:inventory:check`はactive／previous履歴を合格と判定した。

最終のWindows installer／portable ZIP同梱と別配置smokeは、Issues #429・#430のLevel 3 acceptance reportへ記録する。
