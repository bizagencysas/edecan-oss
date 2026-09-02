import SwiftUI
import EdecanKit

/// Memoria de Edecán sobre la persona (`GET /v1/memory?namespace=user`):
/// lista real de lo que recuerda, con "olvidar" de a uno (`DELETE
/// /v1/memory/{id}`). Se conserva el acceso a preguntar/olvidar-todo, que
/// siguen siendo prompts prefillados en el chat.
struct MemoriaView: View {
    @Environment(SessionStore.self) private var session
    @Environment(TabRouter.self) private var router
    @State private var recuerdos: [MemoryItem] = []
    @State private var sugerencias: [MemorySuggestion] = []
    @State private var cargando = true
    @State private var error: String?
    @State private var errorSugerencias: String?
    @State private var proximamente = false
    @State private var olvidandoId: String?
    @State private var guardandoId: String?

    var body: some View {
        List {
            if let error {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .listRowSeparator(.hidden)
            }

            Section {
                if cargando && recuerdos.isEmpty {
                    HStack(spacing: 10) {
                        ProgressView()
                        Text("Leyendo tu memoria…").foregroundStyle(.secondary)
                    }
                } else if recuerdos.isEmpty {
                    Text("Todavía no hay recuerdos guardados. Conversa con Edecán y empezará a recordar lo importante.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(recuerdos) { recuerdo in
                        FilaRecuerdo(recuerdo: recuerdo, olvidando: olvidandoId == recuerdo.id) {
                            Task { await olvidar(recuerdo) }
                        }
                    }
                }
            } header: {
                Text("Lo que recuerda")
            }

            if !sugerencias.isEmpty {
                Section {
                    ForEach(sugerencias) { sugerencia in
                        FilaSugerenciaMemoria(
                            sugerencia: sugerencia,
                            guardando: guardandoId == sugerencia.id
                        ) {
                            Task { await guardar(sugerencia) }
                        } onIgnorar: {
                            ignorar(sugerencia)
                        }
                    }
                } header: {
                    Text("Sugerencias de memoria")
                } footer: {
                    Text("Propuestas del servidor para recordar. Guárdalas o ignóralas; nada se aplica solo.")
                }
            } else if proximamente {
                Section {
                    Label("Próximamente", systemImage: "hourglass")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(EdecanTheme.morado)
                }
            }

            if let errorSugerencias {
                Text(errorSugerencias)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .listRowSeparator(.hidden)
            }

            Section {
                filaAccion(
                    icono: "text.bubble.fill",
                    titulo: "Preguntar qué recuerdas",
                    subtitulo: "Abre el chat con la pregunta lista para enviar",
                    tintada: false
                ) {
                    router.pedir("¿Qué recuerdas de mí?")
                }
                filaAccion(
                    icono: "trash.fill",
                    titulo: "Pedir que olvide todo",
                    subtitulo: "Abre el chat con la orden lista para enviar",
                    tintada: true
                ) {
                    router.pedir("Olvida todo lo que recuerdas de mí")
                }
            }
        }
        .listStyle(.insetGrouped)
        .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
        .navigationTitle("Memoria")
        .navigationBarTitleDisplayMode(.inline)
        .task { await cargar() }
        .refreshable { await cargar() }
    }

    private func cargar() async {
        guard let client = session.client else {
            error = "No hay sesión activa."
            cargando = false
            return
        }
        cargando = recuerdos.isEmpty
        error = nil
        errorSugerencias = nil
        proximamente = false
        defer { cargando = false }
        do {
            recuerdos = try await client.listUserMemory()
        } catch {
            self.error = error.localizedDescription
        }
        await cargarSugerencias(client: client)
    }

    private func cargarSugerencias(client: APIClient) async {
        do {
            sugerencias = try await client.listMemorySuggestions()
        } catch let apiError as APIClient.APIError {
            if apiError.esProximamente {
                proximamente = true
            } else {
                errorSugerencias = apiError.localizedDescription
            }
        } catch {
            errorSugerencias = error.localizedDescription
        }
    }

    private func olvidar(_ recuerdo: MemoryItem) async {
        guard let client = session.client, olvidandoId == nil else { return }
        olvidandoId = recuerdo.id
        error = nil
        defer { olvidandoId = nil }
        do {
            try await client.deleteMemory(id: recuerdo.id)
            recuerdos.removeAll { $0.id == recuerdo.id }
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func guardar(_ sugerencia: MemorySuggestion) async {
        guard let client = session.client, guardandoId == nil else { return }
        guardandoId = sugerencia.id
        error = nil
        defer { guardandoId = nil }
        do {
            _ = try await client.addMemory(
                content: sugerencia.text,
                confidence: sugerencia.confidence ?? 0.8,
                source: sugerencia.source ?? "user"
            )
            sugerencias.removeAll { $0.id == sugerencia.id }
            recuerdos = try await client.listUserMemory()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func ignorar(_ sugerencia: MemorySuggestion) {
        sugerencias.removeAll { $0.id == sugerencia.id }
    }

    private func filaAccion(
        icono: String, titulo: String, subtitulo: String, tintada: Bool, accion: @escaping () -> Void
    ) -> some View {
        Button(action: accion) {
            HStack(spacing: 14) {
                ZStack {
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(tintada ? Color.red.opacity(0.12) : EdecanTheme.morado.opacity(0.13))
                        .frame(width: 42, height: 42)
                    Image(systemName: icono)
                        .foregroundStyle(tintada ? Color.red : EdecanTheme.morado)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(titulo)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(tintada ? Color.red : .primary)
                    Text(subtitulo)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 4)
                Image(systemName: "arrow.up.right")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.tertiary)
            }
        }
        .buttonStyle(.plain)
    }
}

private struct FilaRecuerdo: View {
    let recuerdo: MemoryItem
    let olvidando: Bool
    let onOlvidar: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 4) {
                Text(recuerdo.content ?? "")
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 6) {
                    if let kind = recuerdo.kind, !kind.isEmpty {
                        Text(kind)
                            .font(.caption2.weight(.semibold))
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(EdecanTheme.morado.opacity(0.12), in: Capsule())
                            .foregroundStyle(EdecanTheme.morado)
                    }
                    if let confianza = recuerdo.sourceTrust, !confianza.isEmpty {
                        Text(confianza)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            Spacer(minLength: 8)
            Button(role: .destructive, action: onOlvidar) {
                if olvidando {
                    ProgressView()
                } else {
                    Image(systemName: "trash")
                }
            }
            .buttonStyle(.borderless)
            .disabled(olvidando)
            .foregroundStyle(.red)
        }
        .padding(.vertical, 2)
    }
}

private struct FilaSugerenciaMemoria: View {
    let sugerencia: MemorySuggestion
    let guardando: Bool
    let onGuardar: () -> Void
    let onIgnorar: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(sugerencia.text)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 6) {
                if let fuente = sugerencia.source, !fuente.isEmpty {
                    Text(fuente)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                if let confianza = sugerencia.confidence {
                    Text("Confianza \(Int((confianza * 100).rounded()))%")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            HStack(spacing: 8) {
                Button("Guardar", action: onGuardar)
                    .buttonStyle(.borderedProminent)
                    .tint(EdecanTheme.morado)
                    .controlSize(.small)
                    .disabled(guardando)
                Button("Ignorar", action: onIgnorar)
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .disabled(guardando)
                if guardando {
                    ProgressView()
                }
            }
        }
        .padding(.vertical, 2)
    }
}