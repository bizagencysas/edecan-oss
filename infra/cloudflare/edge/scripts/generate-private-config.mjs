#!/usr/bin/env node

import { chmod, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const PROJECT_DIR = resolve(SCRIPT_DIR, "..");
const BASE_CONFIG_PATH = resolve(PROJECT_DIR, "wrangler.jsonc");

const DOMAIN_VARIABLES = Object.freeze({
  staging: "EDECAN_EDGE_STAGING_DOMAIN",
  production: "EDECAN_EDGE_PRODUCTION_DOMAIN"
});

function normalizeDomain(rawDomain) {
  const domain = rawDomain.trim().toLowerCase();
  if (!domain) {
    throw new Error("el dominio está vacío");
  }
  if (
    domain.includes("://")
    || domain.includes("/")
    || domain.includes("?")
    || domain.includes("#")
    || domain.includes("@")
    || domain.includes(":")
  ) {
    throw new Error("usa solo el hostname, sin protocolo, puerto, ruta, query ni fragmento");
  }
  if (
    domain === "localhost"
    || !domain.includes(".")
    || domain.startsWith(".")
    || domain.endsWith(".")
    || domain.includes("..")
  ) {
    throw new Error("usa un hostname DNS completo");
  }
  if (!/^[a-z0-9.-]+$/.test(domain)) {
    throw new Error("el hostname contiene caracteres no admitidos");
  }
  return domain;
}

function buildPrivateConfig(baseConfig, environment, rawDomain) {
  if (!(environment in DOMAIN_VARIABLES)) {
    throw new Error(`entorno no admitido: ${environment}`);
  }
  const selectedEnvironment = baseConfig.env?.[environment];
  if (!selectedEnvironment) {
    throw new Error(`wrangler.jsonc no define env.${environment}`);
  }

  const domain = normalizeDomain(rawDomain);
  return {
    ...baseConfig,
    env: {
      ...baseConfig.env,
      [environment]: {
        ...selectedEnvironment,
        routes: [{ pattern: domain, custom_domain: true }]
      }
    }
  };
}

function privateConfigPath(environment) {
  return resolve(PROJECT_DIR, `wrangler.private.${environment}.jsonc`);
}

async function generatePrivateConfig(environment, processEnvironment = process.env) {
  const variableName = DOMAIN_VARIABLES[environment];
  if (!variableName) {
    throw new Error("uso: node scripts/generate-private-config.mjs <staging|production>");
  }
  const rawDomain = processEnvironment[variableName];
  if (!rawDomain) {
    throw new Error(`falta ${variableName}`);
  }

  const baseConfig = JSON.parse(await readFile(BASE_CONFIG_PATH, "utf8"));
  const privateConfig = buildPrivateConfig(baseConfig, environment, rawDomain);
  const destination = privateConfigPath(environment);
  const temporary = `${destination}.tmp-${process.pid}`;
  await writeFile(temporary, `${JSON.stringify(privateConfig, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600
  });
  await rename(temporary, destination);
  await chmod(destination, 0o600);
  return destination;
}

async function main() {
  try {
    const environment = process.argv[2] ?? "";
    const destination = await generatePrivateConfig(environment);
    process.stdout.write(`Configuración privada creada: ${destination}\n`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    process.stderr.write(`No se pudo crear la configuración privada: ${message}\n`);
    process.exitCode = 64;
  }
}

const invokedPath = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : "";
if (import.meta.url === invokedPath) {
  await main();
}

export {
  DOMAIN_VARIABLES,
  buildPrivateConfig,
  generatePrivateConfig,
  normalizeDomain,
  privateConfigPath
};
