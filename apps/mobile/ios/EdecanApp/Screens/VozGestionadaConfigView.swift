import EdecanKit
import SwiftUI

/// Configuración de la voz GESTIONADA (`docs/speech-engine.md`).
///
/// - La activación es explícita y el aviso de proveedor PAGADO va primero:
///   los minutos se facturan a la API key de ElevenLabs del tenant.
/// - Los catálogos y defaults vienen del servidor (`GET /v1/voice/preferences`);
///   el `AppStorage` local no es autoridad de nada acá.
/// - Sin credencial conectada, el toggle no se puede activar y se indica dónde
///   conectarla (Ajustes → Conectores), en vez de fingir que hay voz gestionada.
struct VozGestionadaConfigView: View {
    let client: APIClient?
    @Environment(\.dismiss) private var dismiss

    @State private var cargando = true
    @State private var guardando = false
    @State private var errorMensaje: String?
    @State private var datos: PreferenciasVozGestionada?
    @State private var enabled = false
    @State private var voiceModelId: String?
    @State private var delegationModelId: String?
    @State private var delegationEffort: String?
    @State private var voiceId: String?
    @State private var ttsModelId: String?
    @State private var maxDurationSeconds = 900

    var body: some View {
        NavigationStack {
            Form {
                if cargando {
                    HStack { ProgressView(); Text("Cargando…") }
                } else if let errorMensaje {
                    Text(errorMensaje).font(.footnote).foregroundStyle(.red)
                } else if let datos {
                    seccionAvisoPagado(datos)
                    seccionModelos(datos)
                    seccionVoz(datos)
                    seccionDuracion
                }
            }
            .navigationTitle("Voz gestionada")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancelar") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await guardar() }
                    } label: {
                        if guardando { ProgressView() } else { Text("Guardar").fontWeight(.semibold) }
                    }
                    .disabled(cargando || datos == nil)
                }
            }
            .task { await cargar() }
        }
    }

    private func seccionAvisoPagado(_ datos: PreferenciasVozGestionada) -> some View {
        Section("Proveedor pagado") {
            Text(datos.paidProviderNotice)
                .font(.footnote)
                .foregroundStyle(.secondary)
            Toggle("Activar voz gestionada", isOn: $enabled)
                .disabled(!datos.credentialConnected)
            if !datos.credentialConnected {
                Text("Conecta tu API key de ElevenLabs en Ajustes → Conectores (Voz gestionada — Speech Engine) para poder activarla.")
                    .font(.footnote)
                    .foregroundStyle(.orange)
            }
        }
    }

    private func seccionModelos(_ datos: PreferenciasVozGestionada) -> some View {
        Section("Modelos") {
            Picker("Interlocutor de voz", selection: $voiceModelId) {
                ForEach(datos.catalogs.voiceModels) { modelo in
                    Text(modelo.nombre).tag(Optional(modelo.id))
                }
            }
            Picker("Delegación (trabajo real)", selection: $delegationModelId) {
                Text("Heredar del chat").tag(String?.none)
                ForEach(datos.catalogs.voiceModels) { modelo in
                    Text(modelo.nombre).tag(Optional(modelo.id))
                }
            }
            Picker("Esfuerzo de delegación", selection: $delegationEffort) {
                Text("Automático").tag(String?.none)
                Text("Bajo").tag(Optional("bajo"))
                Text("Medio").tag(Optional("medio"))
                Text("Alto").tag(Optional("alto"))
            }
        }
    }

    private func seccionVoz(_ datos: PreferenciasVozGestionada) -> some View {
        Section("Voz") {
            if datos.catalogs.ttsVoices.isEmpty {
                Text("Sin catálogo de voces (requiere la API key del tenant).")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                Picker("Voz", selection: $voiceId) {
                    ForEach(datos.catalogs.ttsVoices) { voz in
                        Text(voz.name).tag(Optional(voz.id))
                    }
                }
            }
            if datos.catalogs.ttsModels.isEmpty {
                Picker("Modelo TTS", selection: $ttsModelId) {
                    Text(datos.defaults.ttsModelId).tag(Optional(datos.defaults.ttsModelId))
                }
            } else {
                Picker("Modelo TTS", selection: $ttsModelId) {
                    ForEach(datos.catalogs.ttsModels) { modelo in
                        Text(modelo.name).tag(Optional(modelo.id))
                    }
                }
            }
        }
    }

    private var seccionDuracion: some View {
        Section("Límites") {
            Stepper(
                "Duración máxima: \(maxDurationSeconds / 60) min",
                value: $maxDurationSeconds,
                in: 60...3600,
                step: 60
            )
        }
    }

    private func cargar() async {
        guard let client else {
            errorMensaje = "No hay sesión activa."
            cargando = false
            return
        }
        do {
            let datos = try await client.preferenciasVozGestionada()
            self.datos = datos
            enabled = datos.preference?.enabled ?? false
            voiceModelId = datos.preference?.voiceModelId
            delegationModelId = datos.preference?.delegationModelId
            delegationEffort = datos.preference?.delegationEffort
            voiceId = datos.preference?.voiceId
            ttsModelId = datos.preference?.ttsModelId
            maxDurationSeconds = datos.preference?.maxDurationSeconds ?? datos.defaults.maxDurationSeconds
        } catch {
            errorMensaje = "No se pudieron cargar las preferencias: \(error.localizedDescription)"
        }
        cargando = false
    }

    private func guardar() async {
        guard let client else { return }
        guardando = true
        defer { guardando = false }
        do {
            _ = try await client.actualizarPreferenciasVozGestionada(
                PutPreferenciasVozGestionada(
                    enabled: enabled,
                    voiceModelId: voiceModelId,
                    delegationModelId: delegationModelId,
                    delegationEffort: delegationEffort,
                    voiceId: voiceId,
                    ttsModelId: ttsModelId,
                    maxDurationSeconds: maxDurationSeconds
                )
            )
            dismiss()
        } catch {
            if let apiError = error as? APIClient.APIError,
               case .servidor(_, let mensaje) = apiError {
                errorMensaje = mensaje
            } else {
                errorMensaje = "No se pudieron guardar las preferencias: \(error.localizedDescription)"
            }
        }
    }
}