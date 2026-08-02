import test from "node:test";
import assert from "node:assert/strict";

async function navigationModule(search) {
  globalThis.window = { location: { search, pathname: "/", hash: "" } };
  return import(`../src/app/navigation.ts?model-library=${encodeURIComponent(search)}`);
}

test("Model Library asset tab survives a shareable URL round trip", async () => {
  const navigation = await navigationModule(
    "?view=model-library&asset=graphs",
  );
  const intent = navigation.readNavigationIntent();

  assert.equal(intent.view, "model-library");
  assert.equal(intent.modelLibraryTab, "graphs");
  assert.equal(
    navigation.navigationUrl(intent),
    "/?view=model-library&asset=graphs",
  );
});

test("unknown Model Library asset tab normalizes to Task assets", async () => {
  const navigation = await navigationModule(
    "?view=model-library&asset=unknown",
  );
  const intent = navigation.readNavigationIntent();

  assert.equal(intent.modelLibraryTab, "tasks");
  assert.equal(navigation.navigationUrl(intent), "/?view=model-library");
  assert.equal(
    navigation.navigationLocationNeedsNormalization(intent),
    true,
  );
});

test("Model Library selection is discarded when leaving the library", async () => {
  const navigation = await navigationModule(
    "?view=model-library&asset=packages",
  );
  const project = navigation.withView(
    navigation.readNavigationIntent(),
    "project",
  );

  assert.equal(project.modelLibraryTab, undefined);
});

