import assert from "node:assert/strict";
import test from "node:test";
import { resolveE2ePorts } from "./e2e-port-allocation.mjs";

test("dynamic parallel ports use independent OS reservations", async () => {
  const reserved = [43101, 43102];
  const ports = await resolveE2ePorts({}, { reserve: async () => reserved.shift() });
  assert.deepEqual(ports, { apiPort: 43101, webPort: 43102 });
});

test("explicit ports are respected but a duplicate pair is rejected", async () => {
  const explicit = await resolveE2ePorts({ PLAYWRIGHT_API_PORT: "43103", PLAYWRIGHT_WEB_PORT: "43104" });
  assert.deepEqual(explicit, { apiPort: 43103, webPort: 43104 });
  await assert.rejects(
    resolveE2ePorts({ PLAYWRIGHT_API_PORT: "43105", PLAYWRIGHT_WEB_PORT: "43105" }),
    /must resolve to different ports/,
  );
});
