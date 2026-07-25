# 疎な配合候補とDesign Spaceの契約

多段Chainの候補は、原料を固定列へ展開せず `blend.items` の可変長明細を正本とする。
この契約は [多段Chainアーキテクチャ](decisions/multistage-chain-architecture.md) のPhase 1境界を実装したものである。

## 候補

```json
{
  "blend": {
    "schema_version": "sparse-blend/v1",
    "items": [
      {"material_id": "RM-0001", "ratio": 75.0},
      {"material_id": "RM-0002", "ratio": 25.0}
    ],
    "hoop_id": "HP-01",
    "fill_ratio": 24.0,
    "balance_material_id": "RM-0001",
    "scientific_master": {"resource_id": "science", "revision": 1, "digest": "sha256:..."},
    "commercial_catalog": {"resource_id": "commercial", "revision": 3, "digest": "sha256:..."},
    "design_space": {"resource_id": "space", "revision": 2, "digest": "sha256:..."}
  }
}
```

`ratio` と `fill_ratio` はいずれも百分率である。
候補のフープ、充填率、残部原料はDesign Spaceで固定する。
原料明細の順序はモデル入力の意味に含めない。

## revisionの分離

- 科学master：原料種類、群、粒度、フープを固定する。Stage Aの科学変換が参照する。
- 商用catalog：調達区分と単価を固定する。価格変更だけでは科学入力hashを変えない。
- Design Space：使用可能集合、原料上下限、群合計、群ごとの選択数、全体の選択数、合計、残部を固定する。

各参照はresource ID、revision、内容digestの三つ組で解決する。
未知revision、未知原料、未知フープ、重複原料、非有限値は構造エラーとして保存前に拒否する。

## draftと推論

合計、固定フープ・充填率、使用可能集合、上下限、群合計、選択数に違反する候補は拒否しない。
サーバーが `blend_validation.status=invalid` と理由一覧を保存し、編集可能なdraftとして返す。
preview、詳細予測、範囲探索、検討アクティビティは成立するまで実行しない。

`editor_state.locked_material_ids` は保存するが、canonical scientific inputと推論cache hashには含めない。
残部原料、Design Space revision、商用catalog revisionもcandidate revisionの再現情報として保持し、Stage Aの数値入力hashとは分離する。

既存の固定フォーム候補には `blend=null`、`blend_validation=not_applicable` が補われる。
既存snapshotのraw candidateも同じ既定値で読み取るため、保存済み結果を再計算しない。
