import EdecanKit
import SwiftUI

/// Hoja "Seleccionar modelo" del chat: los cuatro modelos de portada, la fila
/// "Esfuerzo" (solo donde el nivel cambia algo de verdad) y "Más modelos" con
/// el resto del catálogo.
///
/// La lista NO está escrita aquí: viene de `GET /v1/models/chat`
/// (``ChatViewModel/catalogoModelos``), cuya autoridad es
/// `config/modelos.yml` -> `modelos_chat`. Elegir dispara
/// `PUT /v1/conversations/{id}/model`, así que lo que se toca acá cambia el
/// modelo que corre el próximo turno — no es una preferencia decorativa.
struct SelectorModeloView: View {
    let viewModel: ChatViewModel

    @Environment(SessionStore.self) private var session
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    if let catalogo = viewModel.catalogoModelos {
                        ForEach(catalogo.principales) { modelo in
                            filaDeModelo(modelo)
                        }
                    } else if viewModel.cargandoCatalogoModelos {
                        HStack(spacing: 10) {
                            ProgressView()
                            Text("Cargando modelos…")
                                .foregroundStyle(.secondary)
                        }
                    } else {
                        Text("No se pudo cargar la lista de modelos. Desliza para reintentar.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }

                // Sin catálogo cargado esta sección no tendría filas: se omite
                // entera para no dejar un bloque vacío en la hoja.
                if let catalogo = viewModel.catalogoModelos {
                    Section {
                        // La fila solo existe donde el Esfuerzo cambia el turno
                        // de verdad (`soporta_esfuerzo`): en Copla o en
                        // automático el nivel no se aplica, y un control que no
                        // hace nada es exactamente lo que hace sentir incapaz
                        // al asistente.
                        if viewModel.seleccionDeModelo.muestraEsfuerzo {
                            NavigationLink {
                                PickerEsfuerzoView(viewModel: viewModel)
                            } label: {
                                LabeledContent(
                                    "Esfuerzo",
                                    value: (viewModel.esfuerzoElegido
                                        ?? catalogo.esfuerzoPorDefecto).nombreLegible
                                )
                            }
                        }

                        if !catalogo.secundarios.isEmpty {
                            NavigationLink {
                                MasModelosView(viewModel: viewModel, onElegir: elegir)
                            } label: {
                                Text("Más modelos")
                            }
                        }
                    }
                }

                Section {
                    Button {
                        elegir(nil)
                    } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Automático")
                                    .foregroundStyle(.primary)
                                Text("Edecán decide según la tarea")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            if viewModel.modeloElegido == nil {
                                Image(systemName: "checkmark")
                                    .font(.body.weight(.semibold))
                                    .foregroundStyle(EdecanTheme.morado)
                            }
                        }
                    }
                    .buttonStyle(.plain)
                }
            }
            .scrollContentBackground(.hidden)
            .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
            .navigationTitle("Seleccionar modelo")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { dismiss() } label: {
                        Image(systemName: "xmark")
                    }
                    .accessibilityLabel("Cerrar")
                }
            }
            .task { await viewModel.cargarCatalogoModelos(client: session.client) }
            .refreshable { await viewModel.cargarCatalogoModelos(client: session.client) }
        }
        .presentationDetents([.medium, .large])
    }

    @ViewBuilder
    private func filaDeModelo(_ modelo: ChatModelInfo) -> some View {
        Button {
            elegir(modelo.id)
        } label: {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(modelo.nombre)
                        .foregroundStyle(.primary)
                    Text(modelo.descripcion)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
                if viewModel.modeloElegido == modelo.id {
                    Image(systemName: "checkmark")
                        .font(.body.weight(.semibold))
                        .foregroundStyle(EdecanTheme.morado)
                }
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(
            "\(modelo.nombre). \(modelo.descripcion)"
                + (viewModel.modeloElegido == modelo.id ? ". Seleccionado" : "")
        )
    }

    /// Cerrar la hoja al elegir es deliberado: la selección ya se aplicó de
    /// forma optimista y el dueño vuelve al composer viendo la pastilla nueva.
    /// Si el PUT falla, ``ChatViewModel/fijarModelo(_:esfuerzo:client:)``
    /// revierte y el chat muestra el error.
    private func elegir(_ modeloId: String?) {
        let client = session.client
        dismiss()
        Task {
            await viewModel.fijarModelo(
                modeloId,
                esfuerzo: viewModel.esfuerzoElegido,
                client: client
            )
        }
    }
}

/// Picker Bajo/Medio/Alto. No cierra la hoja: elegir el nivel deja al dueño
/// donde estaba para que pueda seguir ajustando.
private struct PickerEsfuerzoView: View {
    let viewModel: ChatViewModel

    @Environment(SessionStore.self) private var session
    @Environment(\.dismiss) private var dismiss

    private var niveles: [EsfuerzoChat] {
        viewModel.catalogoModelos?.esfuerzos ?? EsfuerzoChat.allCases
    }

    private var activo: EsfuerzoChat {
        viewModel.esfuerzoElegido
            ?? viewModel.catalogoModelos?.esfuerzoPorDefecto
            ?? .medio
    }

    var body: some View {
        List {
            Section {
                ForEach(niveles) { nivel in
                    Button {
                        let client = session.client
                        dismiss()
                        Task {
                            await viewModel.fijarModelo(
                                viewModel.modeloElegido,
                                esfuerzo: nivel,
                                client: client
                            )
                        }
                    } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(nivel.nombreLegible)
                                    .foregroundStyle(.primary)
                                Text(nivel.descripcion)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            if nivel == activo {
                                Image(systemName: "checkmark")
                                    .font(.body.weight(.semibold))
                                    .foregroundStyle(EdecanTheme.morado)
                            }
                        }
                    }
                    .buttonStyle(.plain)
                }
            } footer: {
                Text("El nivel cambia cuánto puede pensar Edecán en cada vuelta de trabajo. Solo se aplica en los modelos que lo soportan.")
            }
        }
        .scrollContentBackground(.hidden)
        .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
        .navigationTitle("Esfuerzo")
        .navigationBarTitleDisplayMode(.inline)
    }
}

/// Resto del catálogo. Los modelos ciegos ya vienen etiquetados en su
/// `descripcion` desde el backend; el ícono repite el dato para quien escanea
/// la lista sin leerla.
private struct MasModelosView: View {
    let viewModel: ChatViewModel
    let onElegir: (String?) -> Void

    private var secundarios: [ChatModelInfo] {
        viewModel.catalogoModelos?.secundarios ?? []
    }

    var body: some View {
        List {
            Section {
                ForEach(secundarios) { modelo in
                    Button {
                        onElegir(modelo.id)
                    } label: {
                        HStack(alignment: .top, spacing: 12) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(modelo.nombre)
                                    .foregroundStyle(.primary)
                                Text(modelo.descripcion)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            Spacer(minLength: 8)
                            if !modelo.veImagenes {
                                Image(systemName: "eye.slash")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .accessibilityLabel("No ve imágenes")
                            }
                            if viewModel.modeloElegido == modelo.id {
                                Image(systemName: "checkmark")
                                    .font(.body.weight(.semibold))
                                    .foregroundStyle(EdecanTheme.morado)
                            }
                        }
                    }
                    .buttonStyle(.plain)
                }
            } footer: {
                // Condicional a propósito: el texto lo decide el catálogo, no
                // una suposición sobre qué modelos hay hoy detrás de esta
                // pantalla.
                if secundarios.contains(where: { !$0.veImagenes }) {
                    Text("Los marcados con el ojo tachado no ven imágenes. Si mandas una captura, ese mensaje lo atiende un modelo con visión.")
                }
            }
        }
        .scrollContentBackground(.hidden)
        .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
        .navigationTitle("Más modelos")
        .navigationBarTitleDisplayMode(.inline)
    }
}
