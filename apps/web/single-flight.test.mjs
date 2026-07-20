import assert from "node:assert/strict";
import test from "node:test";

import { createSingleFlight } from "./src/lib/single-flight.ts";

test("deduplica callers concurrentes y comparte el mismo resultado", async () => {
  const run = createSingleFlight();
  let starts = 0;
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });

  const first = run(async () => {
    starts += 1;
    await gate;
    return "rotated";
  });
  const second = run(async () => {
    starts += 1;
    return "should-not-run";
  });

  assert.strictEqual(first, second);
  assert.equal(starts, 0);
  release();
  assert.deepEqual(await Promise.all([first, second]), ["rotated", "rotated"]);
  assert.equal(starts, 1);
});
test("libera el slot después de éxito o error", async () => {
  const run = createSingleFlight();
  let starts = 0;

  await assert.rejects(
    run(async () => {
      starts += 1;
      throw new Error("fallo esperado");
    }),
    /fallo esperado/,
  );
  assert.equal(await run(async () => ++starts), 2);
});
