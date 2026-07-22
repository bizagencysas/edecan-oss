import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  CONNECTOR_GUIDES,
  DIRECT_CREDENTIAL_LINKS,
  META_ADS_MCP_GUIDE,
  connectionStatusLabel,
} from "./src/lib/connector-guides.ts";

function source(path) {
  return readFileSync(new URL(path, import.meta.url), "utf8");
}

test("cada conector OAuth enlaza directamente a una consola HTTPS oficial", () => {
  assert.deepEqual(Object.keys(CONNECTOR_GUIDES).sort(), [
    "google",
    "meta",
    "microsoft",
    "slack",
    "x",
    "youtube",
  ]);
  for (const guide of Object.values(CONNECTOR_GUIDES)) {
    const url = new URL(guide.consoleUrl);
    assert.equal(url.protocol, "https:");
    assert.ok(guide.consoleLabel.length > 0);
    assert.ok(guide.help.length > 0);
  }
});

test("las credenciales directas usan portales oficiales y estados honestos", () => {
  assert.equal(new URL(DIRECT_CREDENTIAL_LINKS.telegram.url).hostname, "t.me");
  assert.equal(new URL(DIRECT_CREDENTIAL_LINKS.discord.url).hostname, "discord.com");
  assert.equal(new URL(DIRECT_CREDENTIAL_LINKS.whatsapp.url).hostname, "developers.facebook.com");
  assert.equal(new URL(DIRECT_CREDENTIAL_LINKS.twilio.url).hostname, "console.twilio.com");

  assert.equal(connectionStatusLabel([]).label, "Sin autorizar");
  assert.equal(connectionStatusLabel([{ status: "active" }]).label, "Autorizada");
  assert.equal(connectionStatusLabel([{ status: "expired" }]).label, "Requiere atención");
});

test("Meta Ads MCP distingue el endpoint oficial del servidor comunitario local", () => {
  assert.equal(new URL(META_ADS_MCP_GUIDE.officialEndpoint).hostname, "mcp.facebook.com");
  assert.equal(new URL(META_ADS_MCP_GUIDE.metaAppsUrl).hostname, "developers.facebook.com");
  assert.equal(new URL(META_ADS_MCP_GUIDE.communitySourceUrl).hostname, "github.com");
  assert.equal(META_ADS_MCP_GUIDE.tokenEnvName, "META_ADS_ACCESS_TOKEN");

  const card = source("./src/components/configuracion/CardServidoresMcp.tsx");
  const api = source("./src/lib/api-mcp.ts");
  assert.match(card, /no reutiliza tokens de Graph/);
  assert.match(card, /type="password"/);
  assert.match(api, /env\?: Record<string, string>/);
});
