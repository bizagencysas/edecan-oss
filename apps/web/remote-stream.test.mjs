import assert from "node:assert/strict";
import test from "node:test";

import { remoteStreamRetryDelay } from "./src/lib/api-remoto.ts";

test("el backoff del visor remoto crece y queda acotado", () => {
  assert.equal(remoteStreamRetryDelay(0), 250);
  assert.equal(remoteStreamRetryDelay(1), 500);
  assert.equal(remoteStreamRetryDelay(5), 5000);
  assert.equal(remoteStreamRetryDelay(99), 5000);
  assert.equal(remoteStreamRetryDelay(-4), 250);
  assert.equal(remoteStreamRetryDelay(Number.NaN), 250);
});

