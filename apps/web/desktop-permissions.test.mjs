import assert from "node:assert/strict";
import test from "node:test";

import {
  mergePermissionAction,
  PERMISSION_STATUS_COPY,
  readyPermissionCount,
} from "./src/lib/desktop-permissions.ts";

const state = {
  platform: "macos",
  permissions: [
    {
      id: "microphone",
      title: "Micrófono",
      description: "Voz",
      level: "essential",
      status: "unknown",
      action_label: "Permitir",
    },
    {
      id: "accessibility",
      title: "Accesibilidad",
      description: "Control",
      level: "essential",
      status: "granted",
      action_label: "Abrir",
    },
    {
      id: "files",
      title: "Archivos",
      description: "Archivos normales",
      level: "on_demand",
      status: "not_required",
      action_label: null,
    },
  ],
};

test("actualiza solo el permiso devuelto por el sistema operativo", () => {
  const next = mergePermissionAction(state, {
    permission_id: "microphone",
    status: "granted",
    message: "Listo",
  });
  assert.equal(next.permissions[0].status, "granted");
  assert.equal(next.permissions[1].status, "granted");
  assert.equal(state.permissions[0].status, "unknown");
});

test("cuenta como listos los concedidos y los que no requieren permiso", () => {
  assert.equal(readyPermissionCount(state), 2);
  assert.equal(PERMISSION_STATUS_COPY.needs_action.label, "Requiere atención");
});
