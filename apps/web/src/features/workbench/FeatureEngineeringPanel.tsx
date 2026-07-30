import type { ApiPreview } from "../../shared/api/workbench-api";

type EngineeredFeature = {
  name: string;
  label: string;
  value: number;
  unit: string;
  family: string;
  formula: string;
  meaning: string;
  caveat: string;
};

function featureRows(preview: ApiPreview | null): EngineeredFeature[] {
  const raw = preview?.canonical_input.feature_engineering;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    if (typeof row.name !== "string" || typeof row.label !== "string" || typeof row.value !== "number") return [];
    return [{
      name: row.name,
      label: row.label,
      value: row.value,
      unit: typeof row.unit === "string" ? row.unit : "",
      family: typeof row.family === "string" ? row.family : "その他",
      formula: typeof row.formula === "string" ? row.formula : "",
      meaning: typeof row.meaning === "string" ? row.meaning : "",
      caveat: typeof row.caveat === "string" ? row.caveat : "",
    }];
  });
}

const number = new Intl.NumberFormat("ja-JP", { maximumFractionDigits: 3 });

export function FeatureEngineeringPanel({ preview }: { preview: ApiPreview | null }) {
  const rows = featureRows(preview);
  if (!rows.length) return null;
  const families = [...new Set(rows.map((row) => row.family))];
  return (
    <details className="feature-engineering-panel">
      <summary>
        <span><b>内部で作った特徴量</b><small>入力から {rows.length}項目を計算</small></span>
        <i>{families.join(" · ")}</i>
      </summary>
      <div className="feature-engineering-intro">
        元の入力からモデルへ渡すために作った補助量です。デモでは、精度や因果の証明ではなく計算パターンを確認します。
      </div>
      <div className="feature-engineering-families">
        {families.map((family) => (
          <section key={family}>
            <h3>{family}</h3>
            <div className="feature-engineering-grid">
              {rows.filter((row) => row.family === family).map((row) => (
                <article key={row.name}>
                  <header><span>{row.label}</span><strong>{number.format(row.value)} <small>{row.unit}</small></strong></header>
                  {row.formula && <code>{row.formula}</code>}
                  {row.caveat && <p>{row.caveat}</p>}
                </article>
              ))}
            </div>
          </section>
        ))}
      </div>
    </details>
  );
}
