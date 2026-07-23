/** Configuración neutral para reconocimiento de voz en español de Venezuela. */
export const SPEECH_RECOGNITION_LOCALE = "es-VE";

export function normalizeWakePhrase(value: string): string {
  return value
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function editDistance(left: string, right: string): number {
  if (!left) return right.length;
  if (!right) return left.length;
  const previous = Array.from({ length: right.length + 1 }, (_, index) => index);
  for (let leftIndex = 1; leftIndex <= left.length; leftIndex++) {
    const current = [leftIndex];
    for (let rightIndex = 1; rightIndex <= right.length; rightIndex++) {
      current[rightIndex] = Math.min(
        current[rightIndex - 1] + 1,
        previous[rightIndex] + 1,
        previous[rightIndex - 1] + (left[leftIndex - 1] === right[rightIndex - 1] ? 0 : 1),
      );
    }
    previous.splice(0, previous.length, ...current);
  }
  return previous[right.length];
}

/**
 * Tolera espacios y una pequeña variación del dictado (por ejemplo,
 * "oye de can"), sin convertir frases muy cortas como "Sol" en un detector
 * difuso que se active con cualquier palabra parecida.
 */
export function transcriptContainsWakePhrase(transcript: string, wakePhrase: string): boolean {
  const normalizedTranscript = normalizeWakePhrase(transcript);
  const normalizedWakePhrase = normalizeWakePhrase(wakePhrase);
  if (!normalizedTranscript || !normalizedWakePhrase) return false;
  if (normalizedTranscript.includes(normalizedWakePhrase)) return true;

  const compactTranscript = normalizedTranscript.replaceAll(" ", "");
  const compactWakePhrase = normalizedWakePhrase.replaceAll(" ", "");
  if (compactTranscript.includes(compactWakePhrase)) return true;
  if (compactWakePhrase.length < 5) return false;

  const maxDistance = Math.max(1, Math.floor(compactWakePhrase.length * 0.16));
  for (let start = 0; start < compactTranscript.length; start++) {
    for (
      let length = Math.max(1, compactWakePhrase.length - maxDistance);
      length <= compactWakePhrase.length + maxDistance && start + length <= compactTranscript.length;
      length++
    ) {
      if (editDistance(compactTranscript.slice(start, start + length), compactWakePhrase) <= maxDistance) {
        return true;
      }
    }
  }
  return false;
}

const SLEEP_COMMANDS = new Set([
  "duerme",
  "duerme edecan",
  "descansa",
  "descansa edecan",
  "puedes dormir",
  "puedes dormir edecan",
  "hasta luego",
  "hasta luego edecan",
  "deja de escuchar",
  "deja de escuchar edecan",
]);

/**
 * Reconoce únicamente órdenes completas y deliberadas para terminar la
 * conversación de voz continua. No usamos coincidencias parciales para que
 * frases como "recuérdame dormir" o "¿puedes dormir poco?" no apaguen a
 * Edecán por accidente.
 */
export function transcriptRequestsSleep(transcript: string): boolean {
  return SLEEP_COMMANDS.has(normalizeWakePhrase(transcript));
}
