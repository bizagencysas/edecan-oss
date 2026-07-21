import SwiftUI
import EdecanKit

/// Superficie principal "Edecan" — lista de mensajes, input y envío hacia
/// `POST /v1/conversations/{id}/messages` (SSE) apendeando `text_delta` a
/// medida que llega, más un indicador mientras el agente usa una
/// herramienta (`tool_start`/`tool_end`). Lógica real en ``ChatViewModel``;
/// esta vista solo dibuja su estado.
struct ChatView: View {
    @Environment(SessionStore.self) private var session
    @State private var viewModel = ChatViewModel()
    @State private var textoActual = ""
    @State private var mostrandoVoz = false
    @FocusState private var campoEnfocado: Bool

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                listaDeMensajes
                if let error = viewModel.errorMensaje {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .padding(.horizontal)
                        .padding(.top, 6)
                }
                barraDeEntrada
            }
            .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
            .navigationTitle("Edecan")
            .navigationBarTitleDisplayMode(.inline)
            .sheet(isPresented: $mostrandoVoz) {
                VozView()
            }
        }
    }

    private var listaDeMensajes: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if viewModel.mensajes.isEmpty {
                        EmptyStateView(
                            icono: "bubble.left.and.bubble.right",
                            titulo: "Empieza una conversación",
                            descripcion: "Escríbele a Edecán abajo — puede agendar, buscar en tu correo, resumir finanzas y más, con tu confirmación cuando haga falta."
                        )
                        .padding(.top, 60)
                    }
                    ForEach(viewModel.mensajes) { mensaje in
                        BurbujaMensaje(mensaje: mensaje).id(mensaje.id)
                    }
                    if let herramienta = viewModel.herramientaActiva {
                        IndicadorHerramienta(nombre: herramienta)
                    }
                    if let confirmacion = viewModel.confirmacionPendiente {
                        TarjetaConfirmacion(confirmacion: confirmacion, deshabilitada: viewModel.enviando) { aprobado in
                            resolverConfirmacion(aprobado: aprobado)
                        }
                    }
                }
                .padding()
            }
            .onChange(of: viewModel.mensajes.count) { _, _ in
                desplazarAlFinal(proxy)
            }
            .onChange(of: viewModel.mensajes.last?.texto) { _, _ in
                desplazarAlFinal(proxy)
            }
        }
    }

    private func desplazarAlFinal(_ proxy: ScrollViewProxy) {
        guard let ultimo = viewModel.mensajes.last else { return }
        withAnimation(.easeOut(duration: 0.2)) {
            proxy.scrollTo(ultimo.id, anchor: .bottom)
        }
    }

    private var barraDeEntrada: some View {
        HStack(alignment: .bottom, spacing: 12) {
            Button {
                mostrandoVoz = true
            } label: {
                Image(systemName: "mic.fill")
                    .font(.system(size: 18, weight: .semibold))
                    .frame(width: 42, height: 42)
                    .foregroundStyle(EdecanTheme.morado)
                    .tarjetaVidrio(esquina: 18)
            }
            .accessibilityLabel("Hablar con Edecan")

            TextField("Escríbele a Edecán…", text: $textoActual, axis: .vertical)
                .lineLimit(1...4)
                .focused($campoEnfocado)
                .textFieldStyle(.plain)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .tarjetaVidrio(esquina: 18)

            Button(action: enviarMensajeActual) {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 32))
                    .foregroundStyle(botonHabilitado ? AnyShapeStyle(EdecanTheme.degradado) : AnyShapeStyle(.tertiary))
            }
            .disabled(!botonHabilitado)
        }
        .padding()
    }

    private var botonHabilitado: Bool {
        !textoActual.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !viewModel.enviando
            && viewModel.confirmacionPendiente == nil
    }

    private func enviarMensajeActual() {
        guard let client = session.client else {
            viewModel.errorMensaje = "No hay sesión activa."
            return
        }
        let texto = textoActual
        textoActual = ""
        Task { await viewModel.enviar(texto: texto, client: client) }
    }

    private func resolverConfirmacion(aprobado: Bool) {
        guard let client = session.client else {
            viewModel.errorMensaje = "No hay sesión activa."
            return
        }
        Task { await viewModel.resolverConfirmacion(aprobado: aprobado, client: client) }
    }
}
