import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = dirname(fileURLToPath(import.meta.url));

test("WorkingStatusRow expone aria-label con nombre y trabajando", () => {
  const source = readFileSync(join(root, "src/components/chat/WorkingStatusRow.tsx"), "utf8");
  assert.match(source, /aria-label=\{accessibilityLabel\}/);
  assert.match(source, /`\$\{visibleName\} está trabajando`/);
  assert.match(source, /font-medium.*trabajando/s);
});

test("page.tsx usa fila silenciosa en vivo y no monta ToolTimeline", () => {
  const source = readFileSync(join(root, "src/app/(app)/app/page.tsx"), "utf8");
  assert.match(source, /<WorkingStatusRow/);
  assert.doesNotMatch(source, /\{toolEvents\.length > 0 && <ToolTimeline/);
});
