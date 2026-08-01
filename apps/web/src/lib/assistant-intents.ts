/**
 * Entradas humanas para capacidades que no deben exponer una pantalla técnica
 * como primer paso. El valor de la URL es una clave opaca y permitida; nunca
 * aceptamos texto libre desde query params para evitar que un enlace externo
 * inyecte instrucciones arbitrarias en el compositor.
 */
export const ASSISTANT_INTENTS = {
  prepare_order:
    "Ayúdame a preparar una orden o un pago de forma segura. Pregúntame solo lo necesario y no ejecutes ni confirmes nada hasta recibir mi confirmación explícita.",
  improve_campaigns:
    "Ayúdame con mis campañas publicitarias. Revisa primero si mi cuenta de anuncios está conectada, explícame cualquier dato de ejemplo y no actives gasto sin mi confirmación explícita.",
} as const;

export type AssistantIntentKey = keyof typeof ASSISTANT_INTENTS;

export const ASSISTANT_INTENT_EVENT = "edecan:assistant-intent";

export function assistantIntentHref(intent: AssistantIntentKey): string {
  return `/app?intent=${encodeURIComponent(intent)}`;
}

export function assistantPromptFromSearch(search: string): string | null {
  const intent = new URLSearchParams(search).get("intent");
  return assistantPromptForIntent(intent);
}

export function assistantPromptForIntent(intent: unknown): string | null {
  if (typeof intent !== "string" || !Object.hasOwn(ASSISTANT_INTENTS, intent)) return null;
  return ASSISTANT_INTENTS[intent as AssistantIntentKey];
}
