import assert from "node:assert/strict";
import test from "node:test";

import {
  DESKTOP_UPDATE_CHECK_INTERVAL_MS,
  normalizeDesktopUpdateChannel,
  shouldCheckForDesktopUpdate,
  updateProgressPercent,
} from "./src/lib/desktop-updates.ts";

test("el canal desconocido siempre cae al estable", () => {
  assert.equal(normalizeDesktopUpdateChannel("preview"), "preview");
  assert.equal(normalizeDesktopUpdateChannel("stable"), "stable");
  assert.equal(normalizeDesktopUpdateChannel("nightly"), "stable");
  assert.equal(normalizeDesktopUpdateChannel(null), "stable");
});

test("la comprobación automática respeta un intervalo y tolera storage inválido", () => {
  const now = 1_000_000_000;
  assert.equal(shouldCheckForDesktopUpdate(null, now), true);
  assert.equal(shouldCheckForDesktopUpdate("not-a-number", now), true);
  assert.equal(shouldCheckForDesktopUpdate(String(now - 1000), now), false);
  assert.equal(
    shouldCheckForDesktopUpdate(
      String(now - DESKTOP_UPDATE_CHECK_INTERVAL_MS),
      now,
    ),
    true,
  );
});

test("el progreso nunca sale de cero a cien", () => {
  assert.equal(updateProgressPercent(50, 100), 50);
  assert.equal(updateProgressPercent(200, 100), 100);
  assert.equal(updateProgressPercent(-5, 100), 0);
  assert.equal(updateProgressPercent(5, null), null);
  assert.equal(updateProgressPercent(5, 0), null);
});
