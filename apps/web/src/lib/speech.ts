/** Trocea texto en oraciones para reproducción progresiva de voz: la primera
 * síntesis pedida al backend es corta, así que llega rápido y el audio
 * empieza a sonar casi de inmediato en vez de esperar a que se sintetice el
 * mensaje completo. Fragmentos resultantes demasiado cortos (p. ej. una
 * abreviatura como "Sr.") se fusionan con el siguiente para no disparar una
 * síntesis por casi nada. Compartida entre el botón "Escuchar" por mensaje
 * (`app/page.tsx`) y el modo "Escuchar siempre" (`AlwaysListenMode.tsx`).
 */
export function splitIntoSentences(text: string): string[] {
  const raw = text
    .split(/(?<=[.!?])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (raw.length === 0) return [];

  const merged: string[] = [];
  for (const sentence of raw) {
    const last = merged[merged.length - 1];
    if (last !== undefined && last.length < 20) {
      merged[merged.length - 1] = `${last} ${sentence}`;
    } else {
      merged.push(sentence);
    }
  }
  return merged;
}
