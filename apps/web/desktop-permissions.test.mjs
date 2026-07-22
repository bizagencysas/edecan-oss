import assert from "node:assert/strict";
import test from "node:test";

import {
  DESKTOP_PLATFORM_COPY,
  mergePermissionAction,
  PERMISSION_STATUS_COPY,
  readyPermissionCount,
  remoteEngineBecameReady,
} from "./src/lib/desktop-permissions.ts";

test("desktop platform copy never presents Linux or Windows as macOS", () => {
  assert.equal(DESKTOP_PLATFORM_COPY.macos.name, "macOS");
  assert.equal(DESKTOP_PLATFORM_COPY.windows.name, "Windows");
  assert.equal(DESKTOP_PLATFORM_COPY.linux.name, "Linux");
  assert.match(DESKTOP_PLATFORM_COPY.windows.revealLabel, /Explorador/);
  assert.match(DESKTOP_PLATFORM_COPY.linux.revealLabel, /carpeta/);
  assert.doesNotMatch(DESKTOP_PLATFORM_COPY.linux.applicationHint, /macOS/);
});

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

test("reinicia el motor remoto solo al quedar listos ambos permisos de macOS", () => {
  const missingScreenRecording = {
    ...state,
    permissions: [
      ...state.permissions,
      {
        id: "screen_recording",
        title: "Grabación de pantalla",
        description: "Vista remota",
        level: "essential",
        status: "needs_action",
        action_label: "Permitir",
      },
    ],
  };
  const ready = {
    ...missingScreenRecording,
    permissions: missingScreenRecording.permissions.map((permission) =>
      permission.id === "screen_recording"
        ? { ...permission, status: "granted" }
        : permission,
    ),
  };

  assert.equal(remoteEngineBecameReady(missingScreenRecording, ready), true);
  assert.equal(remoteEngineBecameReady(ready, ready), false);
  assert.equal(
    remoteEngineBecameReady(null, { ...ready, platform: "windows" }),
    false,
  );
});
