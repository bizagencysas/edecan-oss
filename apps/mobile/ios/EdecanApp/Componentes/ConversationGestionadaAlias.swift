import ElevenLabs

/// Alias puente: `Conversation` de EdecanKit (modelo de datos del chat) y
/// `Conversation` del SDK oficial de ElevenLabs (sesión WebRTC) comparten
/// nombre. Este archivo importa SOLO ElevenLabs, así que el alias queda
/// inequívoco en los módulos que necesitan ambos.
typealias ConversationGestionada = Conversation