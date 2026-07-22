import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

const manifestUrl = new URL("./FYDESIGN_UI_PORT_MANIFEST.json", import.meta.url);
const manifest = JSON.parse(readFileSync(manifestUrl, "utf8"));

function sha256(path) {
  return createHash("sha256").update(readFileSync(new URL(path, new URL("../../", manifestUrl)))).digest("hex");
}

test("el manifiesto identifica el snapshot local sucio sin atribuirlo por completo al commit base", () => {
  assert.equal(manifest.provenance.source, "owner-supplied FyDesign working-tree snapshot");
  assert.equal(manifest.provenance.sourceWorkingTreeWasDirty, true);
  assert.match(manifest.provenance.baseCommit, /^[a-f0-9]{40}$/);
  assert.match(manifest.provenance.claim, /do not claim/i);
});

test("cada componente adaptado conserva hashes de origen y destino verificable", () => {
  assert.equal(manifest.inventory.length, 10);
  for (const item of manifest.inventory) {
    assert.match(item.sourceSha256, /^[a-f0-9]{64}$/);
    const targets = item.targets ?? [{ path: item.target, sha256: item.targetSha256 }];
    for (const target of targets) {
      assert.equal(sha256(target.path), target.sha256, target.path);
    }
    assert.ok(item.adaptation.length > 30);
  }
});
