import assert from "node:assert/strict";
import test from "node:test";

import { isPublicAuthRoute } from "./src/lib/auth-route-policy.ts";

test("solo las entradas de sesión omiten el Bearer", () => {
  for (const path of [
    "/v1/auth/local",
    "/v1/auth/register",
    "/v1/auth/login",
    "/v1/auth/refresh",
    "/v1/auth/logout",
  ]) {
    assert.equal(isPublicAuthRoute(path), true, path);
  }
});
test("las operaciones TOTP y rutas de aplicación siguen protegidas", () => {
  for (const path of [
    "/v1/auth/totp/enable",
    "/v1/auth/totp/verify",
    "/v1/auth/totp/disable",
    "/v1/me",
    "/v1/files",
  ]) {
    assert.equal(isPublicAuthRoute(path), false, path);
  }
});
