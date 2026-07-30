/**
 * Enlaces y lenguaje de onboarding para conectores.
 *
 * Son metadatos públicos, nunca secretos ni valores de una instalación. Se
 * mantienen fuera de los formularios para que Ajustes y Conectores usen una
 * única fuente de verdad y para poder probar que cada proveedor manda a su
 * consola oficial, no a tutoriales aleatorios.
 */

export interface ConnectorGuide {
  consoleUrl: string;
  consoleLabel: string;
  appNoun: string;
  accountNoun: string;
  help: string;
  clientSecretRequired?: boolean;
}

export const CONNECTOR_GUIDES: Readonly<Record<string, ConnectorGuide>> = {
  linkedin: {
    consoleUrl: "https://www.linkedin.com/developers/apps",
    consoleLabel: "Abrir mis apps de LinkedIn",
    appNoun: "app de LinkedIn",
    accountNoun: "perfil de LinkedIn",
    help:
      "Activa “Sign in with LinkedIn using OpenID Connect” y “Share on LinkedIn”, y añade la URL de redirección de Edecán.",
    clientSecretRequired: true,
  },
  google: {
    consoleUrl: "https://console.cloud.google.com/apis/credentials",
    consoleLabel: "Abrir credenciales de Google Cloud",
    appNoun: "cliente OAuth",
    accountNoun: "cuenta de Google",
    help: "Activa Gmail API y Google Calendar API en el mismo proyecto.",
    clientSecretRequired: true,
  },
  microsoft: {
    consoleUrl: "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
    consoleLabel: "Abrir registros de aplicaciones de Microsoft",
    appNoun: "aplicación",
    accountNoun: "cuenta de Microsoft",
    help: "Registra una aplicación web en Microsoft Entra y añade la URL de redirección de Edecan.",
    clientSecretRequired: true,
  },
  meta: {
    consoleUrl: "https://developers.facebook.com/apps/",
    consoleLabel: "Abrir mis apps de Meta",
    appNoun: "app de Meta",
    accountNoun: "Facebook Pages e Instagram",
    help: "Esta conexión es para páginas y publicaciones. Meta Ads se configura en su tarjeta propia.",
    clientSecretRequired: true,
  },
  x: {
    consoleUrl: "https://developer.x.com/en/portal/dashboard",
    consoleLabel: "Abrir X Developer Portal",
    appNoun: "app de X",
    accountNoun: "cuenta de X",
    help: "Configura OAuth 2.0 y registra exactamente la URL de redirección mostrada aquí.",
  },
  youtube: {
    consoleUrl: "https://console.cloud.google.com/apis/credentials",
    consoleLabel: "Abrir credenciales de Google Cloud",
    appNoun: "cliente OAuth",
    accountNoun: "canal de YouTube",
    help: "Activa YouTube Data API v3 en el proyecto antes de autorizar tu canal.",
    clientSecretRequired: true,
  },
  slack: {
    consoleUrl: "https://api.slack.com/apps",
    consoleLabel: "Abrir mis apps de Slack",
    appNoun: "app de Slack",
    accountNoun: "espacio de Slack",
    help: "Crea una app desde cero y añade la URL de redirección en OAuth & Permissions.",
    clientSecretRequired: true,
  },
};

export const DIRECT_CREDENTIAL_LINKS = {
  telegram: {
    url: "https://t.me/BotFather",
    label: "Crear o administrar mi bot con BotFather",
  },
  discord: {
    url: "https://discord.com/developers/applications",
    label: "Abrir Discord Developer Portal",
  },
  whatsapp: {
    url: "https://developers.facebook.com/apps/",
    label: "Abrir mis apps de Meta",
  },
  twilio: {
    url: "https://console.twilio.com/",
    label: "Abrir la consola de Twilio",
  },
} as const;

/** Encaje documentado de Meta Ads dentro del soporte MCP actual. */
export const META_ADS_MCP_GUIDE = {
  officialEndpoint: "https://mcp.facebook.com/ads",
  adsManagerUrl: "https://adsmanager.facebook.com/",
  metaAppsUrl: "https://developers.facebook.com/apps/",
  communitySourceUrl: "https://github.com/hashcott/meta-ads-mcp-server",
  localCommand: "npx -y meta-ads-mcp-server",
  tokenEnvName: "META_ADS_ACCESS_TOKEN",
} as const;

export function getConnectorGuide(key: string): ConnectorGuide | null {
  return CONNECTOR_GUIDES[key] ?? null;
}

export function connectionStatusLabel(
  accounts: Array<{ status: string }>,
): { label: string; variant: "success" | "warning" | "neutral" } {
  if (accounts.length === 0) return { label: "Sin autorizar", variant: "neutral" };
  if (accounts.some((account) => account.status !== "active")) {
    return { label: "Requiere atención", variant: "warning" };
  }
  return { label: "Autorizada", variant: "success" };
}
