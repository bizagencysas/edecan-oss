import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";

const source = fs.readFileSync(new URL("./src/lib/api-misiones.ts", import.meta.url), "utf8");

test("el contrato web conserva timing y tokens del trace de misiones", () => {
  assert.match(source, /duration_ms\?: number \| null/);
  assert.match(source, /token_usage\?: Record<string, number>/);
});

