import assert from "node:assert/strict";
import test from "node:test";

import { initialScreeningMode } from "../src/features/screening/screeningInitialMode.ts";

test("starts with an executable landscape when the Project has no goal", () => {
  assert.equal(initialScreeningMode({}), "landscape");
  assert.equal(initialScreeningMode({ TS: null }), "landscape");
});

test("starts with opportunity search when the Project has a valid goal", () => {
  assert.equal(initialScreeningMode({ TS: 500 }), "opportunity");
  assert.equal(
    initialScreeningMode({ TS: { direction: "between", lower: 480, upper: 520 } }),
    "opportunity",
  );
});
