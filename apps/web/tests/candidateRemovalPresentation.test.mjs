import assert from "node:assert/strict";
import test from "node:test";

import { candidateRemovalConfirmationText } from "../src/features/candidates/candidateRemovalPresentation.ts";

test("candidate removal says that the archived candidate can be restored", () => {
  assert.equal(
    candidateRemovalConfirmationText("高強度案"),
    "高強度案を一覧から外します。後でプロジェクト概要から復元できます。",
  );
});
