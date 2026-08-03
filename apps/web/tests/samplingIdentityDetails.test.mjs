import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import path from "node:path";
import { createRequire } from "node:module";

const sourceRoot = path.resolve(import.meta.dirname, "../src");
const bundle = await build({
  stdin: {
    contents: `
      import React from "react";
      import { renderToStaticMarkup } from "react-dom/server";
      import { SamplingIdentityDetails } from "./shared/SamplingIdentityDetails.tsx";
      export const renderDetails = (props) => renderToStaticMarkup(
        React.createElement(SamplingIdentityDetails, props),
      );
    `,
    resolveDir: sourceRoot,
    loader: "tsx",
  },
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
});
const module = { exports: {} };
new Function("module", "exports", "require", bundle.outputFiles[0].text)(
  module,
  module.exports,
  createRequire(import.meta.url),
);
const { renderDetails } = module.exports;

const prediction = (samplingIdentity) => ({
  value: 1,
  lower: 0,
  upper: 2,
  unit: "MPa",
  target_kind: "continuous",
  point_statistic: "mean",
  predictive_family: "normal",
  quantiles: {},
  sampling_identity: samplingIdentity,
});

const effectiveIdentity = {
  schema_version: "sampling-identity/v1",
  runtime_type: "numpyro.dense_posterior.v1",
  method_id: "numpyro-posterior-predictive",
  method_version: "1.0.0",
  operation: "detailed_prediction",
  request_policy_id: "detailed-prediction/v1",
  request_policy_digest: `sha256:${"1".repeat(64)}`,
  seed: 17,
  requested_sample_count: 512,
  effective_sample_count: 512,
  posterior_draw_count: 1000,
  draw_selection_policy: "seeded_without_replacement",
  predictive_resampling_policy: "numpy-default-rng-likelihood/v1",
  aggregation_policy: "central-90-linear-quantiles/v1",
  approximation: null,
  fallback: null,
  parameter_digest: `sha256:${"2".repeat(64)}`,
};

test("sampling detail stays absent for deterministic predictions", () => {
  assert.equal(renderDetails({
    entries: [{ target: "strength", prediction: prediction(null) }],
  }), "");
});

test("sampling detail keeps effective runtime conditions behind disclosure", () => {
  const html = renderDetails({
    entries: [{
      target: "strength",
      label: "引張強さ",
      prediction: prediction(effectiveIdentity),
    }],
  });

  assert.match(html, /<details class="sampling-identity-details">/);
  assert.match(html, /引張強さ/);
  assert.match(html, /sampling-identity\/v1/);
  assert.match(html, /numpyro\.dense_posterior\.v1/);
  assert.match(html, /numpyro-posterior-predictive \/ 1\.0\.0/);
  assert.match(html, />17</);
  assert.match(html, /requested 512 \/ effective 512/);
  assert.match(html, /seeded_without_replacement/);
  assert.match(html, /numpy-default-rng-likelihood\/v1/);
  assert.match(html, /central-90-linear-quantiles\/v1/);
  assert.match(html, new RegExp(`sha256:${"1".repeat(64)}`));
  assert.match(html, /Approximation/);
  assert.match(html, /Fallback/);
});

test("sampling detail distinguishes target and snapshot-level legacy evidence", () => {
  const targetLegacy = renderDetails({
    entries: [{
      target: "strength",
      prediction: prediction({
        schema_version: "sampling-identity/unavailable-legacy",
        reason: "not_recorded",
      }),
    }],
  });
  assert.match(targetLegacy, /Legacy evidence：sampling条件は記録されていません/);

  const runtimeLegacy = renderDetails({
    entries: [
      { target: "strength", prediction: prediction(null) },
      { target: "stable", prediction: prediction(null) },
    ],
    runtimeTypesByTarget: {
      strength: "numpyro.dense_posterior.v1",
      stable: "builtin.deterministic-linear.v1",
    },
  });
  assert.match(runtimeLegacy, /sample-based Runtimeですが、sampling条件は記録されていません/);
  assert.doesNotMatch(runtimeLegacy, /<h4>stable<\/h4>/);

  const pureRuntimeLegacy = renderDetails({
    entries: [
      { target: "strength", prediction: prediction(null) },
      { target: "elongation", prediction: prediction(null) },
    ],
    packageRuntimeTypes: ["numpyro.dense_posterior.v1"],
  });
  assert.match(pureRuntimeLegacy, /strength/);
  assert.match(pureRuntimeLegacy, /elongation/);

  const mixedRuntimeUnknown = renderDetails({
    entries: [{ target: "strength", prediction: prediction(null) }],
    packageRuntimeTypes: [
      "numpyro.dense_posterior.v1",
      "builtin.deterministic-linear.v1",
    ],
    unknownScopeLabel: "Activity全体",
  });
  assert.match(mixedRuntimeUnknown, /Activity全体/);
  assert.match(mixedRuntimeUnknown, /target別Runtimeが不明/);
  assert.doesNotMatch(mixedRuntimeUnknown, /<h4>strength<\/h4>/);

  const snapshotLegacy = renderDetails({
    entries: [{ target: "strength", prediction: prediction(null) }],
    snapshotLegacyStatus: {
      schema_version: "sampling-identity/unavailable-legacy",
      reason: "not_recorded",
    },
  });
  assert.match(snapshotLegacy, /Snapshot全体/);
  assert.match(snapshotLegacy, /target別Runtimeが不明/);
});
