import test from "node:test";
import assert from "node:assert/strict";

async function navigationModule(search) {
  globalThis.window = { location: { search, pathname: "/", hash: "" } };
  return import(`../src/app/navigation.ts?source-lifecycle=${encodeURIComponent(search)}`);
}

test("source lifecycle stage and revision survive a shareable URL round trip", async () => {
  const { navigationUrl, readNavigationIntent } = await navigationModule(
    "?view=data-library&tab=update&connector=connector-1&stage=approval&revision=approval-1",
  );
  const intent = readNavigationIntent();
  assert.equal(intent.view, "data-library");
  assert.equal(intent.dataLibraryTab, "update");
  assert.equal(intent.sourceConnectorId, "connector-1");
  assert.equal(intent.sourceStage, "approval");
  assert.equal(intent.sourceRevisionId, "approval-1");
  assert.equal(
    navigationUrl(intent),
    "/?view=data-library&tab=update&connector=connector-1&stage=approval&revision=approval-1",
  );
});

test("source lifecycle location is discarded outside the Data Library", async () => {
  const { readNavigationIntent, withView } = await navigationModule(
    "?view=data-library&tab=update&connector=connector-1&stage=curation&revision=run-1",
  );
  const project = withView(readNavigationIntent(), "project");
  assert.equal(project.dataLibraryTab, undefined);
  assert.equal(project.sourceConnectorId, undefined);
  assert.equal(project.sourceStage, undefined);
  assert.equal(project.sourceRevisionId, undefined);
});

test("invalid Data Library query is normalized without retaining an unusable resource selection", async () => {
  const { navigationLocationNeedsNormalization, navigationUrl, readNavigationIntent } = await navigationModule(
    "?view=data-library&tab=browse&connector=connector-1&stage=unknown&revision=revision-1",
  );
  const intent = readNavigationIntent();
  assert.equal(intent.view, "data-library");
  assert.equal(intent.dataLibraryTab, "update");
  assert.equal(intent.sourceConnectorId, "connector-1");
  assert.equal(intent.sourceStage, undefined);
  assert.equal(intent.sourceRevisionId, undefined);
  assert.equal(navigationUrl(intent), "/?view=data-library&tab=update&connector=connector-1");
  assert.equal(navigationLocationNeedsNormalization(intent), true);
});

test("Data Library onboarding and lifecycle locations round-trip independently", async () => {
  const onboarding = await navigationModule("?view=data-library&onboarding=new-task");
  const onboardingIntent = onboarding.readNavigationIntent();
  assert.equal(onboardingIntent.dataLibraryTab, "browse");
  assert.equal(onboardingIntent.dataOnboardingMode, "new-task");
  assert.equal(onboarding.navigationUrl(onboardingIntent), "/?view=data-library&onboarding=new-task");

  const resource = await navigationModule("?view=data-library&tab=update&connector=connector-1&stage=training&revision=snapshot-1");
  assert.deepEqual(
    resource.readNavigationIntent(),
    resource.readNavigationIntent(resource.navigationUrl(resource.readNavigationIntent()).slice(1)),
  );
});
