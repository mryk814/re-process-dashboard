import type { components } from "../generated/api-types";

type ApiPrediction = components["schemas"]["Prediction"];
type LegacySamplingIdentity = components["schemas"]["LegacySamplingIdentityUnavailable"];

export type SamplingPredictionEntry = {
  target: string;
  label?: string;
  prediction?: ApiPrediction | null;
};

function optionalValue(value: string | null | undefined): string {
  return value || "なし";
}

export function SamplingIdentityDetails({
  entries,
  snapshotLegacyStatus,
  runtimeTypesByTarget,
  packageRuntimeTypes,
  unknownScopeLabel = "Snapshot全体",
}: {
  entries: SamplingPredictionEntry[];
  snapshotLegacyStatus?: LegacySamplingIdentity | null;
  runtimeTypesByTarget?: Record<string, string>;
  packageRuntimeTypes?: string[];
  unknownScopeLabel?: string;
}) {
  const recorded = entries.flatMap((entry) => (
    entry.prediction?.sampling_identity
      ? [{ ...entry, identity: entry.prediction.sampling_identity }]
      : []
  ));
  const hasTargetRuntimeAuthority = Boolean(
    runtimeTypesByTarget && Object.keys(runtimeTypesByTarget).length > 0,
  );
  const runtimeTypes = new Set(packageRuntimeTypes ?? []);
  const pureNumpyroPackage = (
    runtimeTypes.size === 1
    && runtimeTypes.has("numpyro.dense_posterior.v1")
  );
  const mixedPackageWithoutTargetAuthority = (
    !hasTargetRuntimeAuthority
    && runtimeTypes.size > 1
    && runtimeTypes.has("numpyro.dense_posterior.v1")
  );
  const legacyTargets = [...new Map(
    entries
      .filter((entry) => {
        if (entry.prediction?.sampling_identity) return false;
        if (hasTargetRuntimeAuthority) {
          return runtimeTypesByTarget?.[entry.target] === "numpyro.dense_posterior.v1";
        }
        return pureNumpyroPackage;
      })
      .map((entry) => [`${entry.target}:${entry.label ?? ""}`, entry]),
  ).values()];
  const unknownLegacyScope = Boolean(
    snapshotLegacyStatus || mixedPackageWithoutTargetAuthority,
  );
  if (
    recorded.length === 0
    && legacyTargets.length === 0
    && !unknownLegacyScope
  ) return null;

  return <details className="sampling-identity-details">
    <summary>Sample-based predictionの再現条件</summary>
    <p>予測値を再現するためにRuntimeが実際に使用し、保存した条件です。</p>
    {recorded.map(({ target, label, identity }, index) => (
      <section
        className="sampling-identity-target"
        key={`${target}-${label ?? ""}-${identity.schema_version}-${identity.schema_version === "sampling-identity/v1" ? identity.parameter_digest : index}`}
      >
        <h4>{label ?? target}</h4>
        {identity.schema_version === "sampling-identity/unavailable-legacy"
          ? <p className="sampling-identity-legacy">Legacy evidence：sampling条件は記録されていません。</p>
          : <dl>
            <div><dt>Status</dt><dd>sample-based・実効条件を記録</dd></div>
            <div><dt>Schema</dt><dd><code>{identity.schema_version}</code></dd></div>
            <div><dt>Runtime</dt><dd><code>{identity.runtime_type}</code></dd></div>
            <div><dt>Operation</dt><dd><code>{identity.operation}</code></dd></div>
            <div><dt>Method</dt><dd><code>{identity.method_id} / {identity.method_version}</code></dd></div>
            <div><dt>Seed</dt><dd><code>{identity.seed}</code></dd></div>
            <div><dt>Sample count</dt><dd><code>requested {identity.requested_sample_count} / effective {identity.effective_sample_count}</code></dd></div>
            <div><dt>Posterior draws</dt><dd><code>{identity.posterior_draw_count}</code></dd></div>
            <div><dt>Draw selection</dt><dd><code>{identity.draw_selection_policy}</code></dd></div>
            <div><dt>Predictive resampling</dt><dd><code>{identity.predictive_resampling_policy}</code></dd></div>
            <div><dt>Aggregation</dt><dd><code>{identity.aggregation_policy}</code></dd></div>
            <div><dt>Approximation</dt><dd><code>{optionalValue(identity.approximation)}</code></dd></div>
            <div><dt>Fallback</dt><dd><code>{optionalValue(identity.fallback)}</code></dd></div>
            <div><dt>Policy</dt><dd><code>{identity.request_policy_id}</code></dd></div>
            <div><dt>Request policy digest</dt><dd><code>{identity.request_policy_digest}</code></dd></div>
            <div><dt>Identity digest</dt><dd><code>{identity.parameter_digest}</code></dd></div>
          </dl>}
      </section>
    ))}
    {legacyTargets.map(({ target, label }) => <section
      className="sampling-identity-target"
      key={`${target}-${label ?? ""}-runtime-legacy`}
    >
      <h4>{label ?? target}</h4>
      <p className="sampling-identity-legacy">Legacy evidence：sample-based Runtimeですが、sampling条件は記録されていません。</p>
    </section>)}
    {unknownLegacyScope && <section className="sampling-identity-target">
      <h4>{unknownScopeLabel}</h4>
      <p className="sampling-identity-legacy">Legacy evidence：target別Runtimeが不明なため、sampling条件の記録有無を判定できません。</p>
    </section>}
  </details>;
}
