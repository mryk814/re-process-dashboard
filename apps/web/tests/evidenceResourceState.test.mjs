import test from "node:test";
import assert from "node:assert/strict";
import {
  beginLineageResourceLoad,
  initialLineageResourceState,
  rejectLineageResourceLoad,
  resolveLineageResourceLoad,
} from "../src/features/lineage/lineageResourceState.ts";
import {
  beginQualityResourceLoad,
  initialQualityResourceState,
  rejectQualityResourceLoad,
  resolveQualityResourceLoad,
} from "../src/features/quality/qualityResourceState.ts";

const implementations = [
  {
    name: "Lineage",
    initial: initialLineageResourceState,
    begin: beginLineageResourceLoad,
    resolve: resolveLineageResourceLoad,
    reject: rejectLineageResourceLoad,
  },
  {
    name: "Data Quality",
    initial: initialQualityResourceState,
    begin: beginQualityResourceLoad,
    resolve: resolveQualityResourceLoad,
    reject: rejectQualityResourceLoad,
  },
];

for (const resource of implementations) {
  test(`${resource.name}: a successful empty response is distinct from initial failure`, () => {
    const empty = resource.resolve("project-a", true, "2026-08-02T00:00:00.000Z");
    const unavailable = resource.reject(
      resource.initial("project-a"),
      "project-a",
      "このresourceは利用できません。",
      true,
    );
    const error = resource.reject(
      resource.initial("project-a"),
      "project-a",
      "このresourceを取得できませんでした。",
      false,
    );

    assert.equal(empty.phase, "empty");
    assert.equal(unavailable.phase, "unavailable");
    assert.equal(error.phase, "error");
    assert.equal(unavailable.loadedAt, null);
    assert.equal(error.loadedAt, null);
  });

  test(`${resource.name}: failed same-scope refresh retains timestamp as stale evidence`, () => {
    const ready = resource.resolve("project-a", false, "2026-08-02T01:02:03.000Z");
    const loading = resource.begin(ready, "project-a");
    const stale = resource.reject(
      loading,
      "project-a",
      "更新できませんでした。",
      false,
    );

    assert.equal(loading.phase, "loading");
    assert.deepEqual(stale, {
      scope: "project-a",
      phase: "stale",
      loadedAt: "2026-08-02T01:02:03.000Z",
      error: "更新できませんでした。",
    });
  });

  test(`${resource.name}: a changed scope never presents previous evidence as stale`, () => {
    const ready = resource.resolve("project-a", false, "2026-08-02T01:02:03.000Z");
    const loading = resource.begin(ready, "project-b");
    const failed = resource.reject(
      loading,
      "project-b",
      "取得できませんでした。",
      false,
    );

    assert.deepEqual(loading, resource.initial("project-b"));
    assert.equal(failed.phase, "error");
    assert.equal(failed.loadedAt, null);
  });
}
