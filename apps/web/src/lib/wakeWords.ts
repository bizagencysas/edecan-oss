/**
 * Preset de frases de activación ("wake word") para el modo "Escuchar
 * siempre". Compartido entre el modo del navegador (`AlwaysListenMode`,
 * `SpeechRecognition` continuo en el tab) y el entrenamiento de voz nativo
 * de la app de escritorio (`EscuchaSiempreTab`, Tauri/rustpotter) -- antes
 * vivía inline solo en `AlwaysListenMode.tsx`; se extrajo acá para que
 * ambos consuman exactamente la MISMA lista en vez de duplicarla.
 */

// 10 opciones + "Personalizado" (ver selector en cada componente que lo usa).
// Todas en español, mismo criterio que el resto de la UI (sin i18n en este
// producto).
export const WAKE_WORD_PRESETS = [
  "Oye Edecán",
  "Hey Edecán",
  "Ok Edecán",
  "Edecán",
  "Escúchame Edecán",
  "Despierta Edecán",
  "Vamos Edecán",
  "Hola Edecán",
  "Atención Edecán",
  "Estás ahí Edecán",
] as const;

export type WakeWordPreset = (typeof WAKE_WORD_PRESETS)[number];
