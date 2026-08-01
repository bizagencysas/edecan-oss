import assert from "node:assert/strict";
import test from "node:test";

import {
  createLatestRequestGuard,
  FILE_STATUS_POLL_MS,
  filesNeedRefresh,
  mergeFileSnapshots,
  upsertFile,
} from "./src/lib/file-status.ts";

function file(id, status = "ready") {
  return {
    id,
    filename: `${id}.txt`,
    mime: "text/plain",
    size_bytes: 1,
    status,
    s3_key: id,
    created_at: "2026-07-20T00:00:00Z",
  };
}

test("hace polling únicamente para archivos todavía procesables", () => {
  assert.equal(filesNeedRefresh([]), false);
  assert.equal(filesNeedRefresh([{ status: "ready" }, { status: "error" }]), false);
  assert.equal(filesNeedRefresh([{ status: "ready" }, { status: "uploaded" }]), true);
  assert.equal(filesNeedRefresh([{ status: "processing" }]), true);
});

test("usa una cadencia acotada y no un loop inmediato", () => {
  assert.ok(FILE_STATUS_POLL_MS >= 1_000);
  assert.ok(FILE_STATUS_POLL_MS <= 10_000);
});

test("reconcilia estado remoto sin perder un upload aún no listado", () => {
  const uploaded = file("new", "uploaded");
  const current = [uploaded, file("existing", "processing")];
  const incoming = [file("existing", "ready")];

  assert.deepEqual(mergeFileSnapshots(current, incoming), [incoming[0], uploaded]);
  assert.deepEqual(upsertFile(current, file("existing", "error")), [
    file("existing", "error"),
    uploaded,
  ]);
});

test("invalida determinísticamente respuestas de lista fuera de orden", () => {
  const guard = createLatestRequestGuard();
  const oldRequest = guard.begin();
  const newestRequest = guard.begin();
  assert.equal(oldRequest.isCurrent(), false);
  assert.equal(newestRequest.isCurrent(), true);

  guard.invalidate();
  assert.equal(newestRequest.isCurrent(), false);
});
