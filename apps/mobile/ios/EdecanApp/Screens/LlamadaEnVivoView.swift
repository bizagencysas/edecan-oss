import EdecanKit
import SwiftUI

/// Llamada en curso vista en vivo (frente 6c, paridad Aria): turnos de la
/// conversación apareciendo a medida que se registran, más un campo para
/// susurrarle una instrucción al agente. Se entra desde `DetalleLlamadaView`
/// (``LlamadasView``) mientras la llamada no haya terminado.
///
/// DIFERENCIA con Aria: Aria corre Pipecat en tiempo real sobre
/// WebSocket, así que el susurro se inyecta al instante. Edecán conversa por
/// TURNOS vía webhooks de Twilio (`docs`/`plan-paridad-aria.local.md`
/// frente 6) — no hay pipeline de audio continuo que interceptar. Esta
/// pantalla sondea `APIClient.obtenerLlamada(id:)` cada
/// ``intervaloSondeo`` para ir mostrando los turnos ya persistidos
/// (`event_type == "transcript"`), y el susurro que se manda
/// (`APIClient.susurrarLlamada`) solo se incorpora en la SIGUIENTE
/// respuesta del agente, nunca de inmediato. Esa latencia es inherente al
/// modelo por turnos, no un bug de esta pantalla.
struct LlamadaEnVivoView: View {
    @Environment(SessionStore.self) private var session

    @State private var llamada: PhoneCallOut
    /// Solo controla el spinner de la PRIMERA carga — los sondeos
    /// siguientes son silenciosos para no hacer parpadear la lista.
    @State private var cargandoInicial = true
    @State private var errorCarga: String?
    @State private var tareaSondeo: Task<Void, Never>?

    @State private var textoSusurro = ""
    @State private var enviandoSusurro = false
    @State private var errorSusurro: String?
    /// Susurros ya confirmados por el servidor pero que el próximo sondeo
    /// todavía no trajo de vuelta en `llamada.events` — se muestran de una
    /// vez para que quede claro que se enviaron, y se descartan solos en
    /// cuanto `events` incluya ese mismo `id` (ver ``turnos``).
    @State private var susurrosOptimistas: [PhoneCallEventOut] = []
    @State private var confirmacionSusurroVisible = false
    @State private var tareaConfirmacion: Task<Void, Never>?

    @FocusState private var campoEnfocado: Bool

    private let anclaFinal = "llamada-en-vivo-final"
    private let intervaloSondeo: Duration = .seconds(2)
    private let estadosTerminales: Set<String> = ["completed", "failed", "busy", "no_answer", "cancelled"]

    init(llamada: PhoneCallOut) {
        _llamada = State(initialValue: llamada)
    }

    var body: some View {
        VStack(spacing: 0) {
            cabecera
            Divider()
            listaDeTurnos
            if let errorCarga {
                Text(errorCarga)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .padding(.horizontal)
                    .padding(.top, 4)
            }
            barraDeSusurro
        }
        .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
        .navigationTitle("Llamada en vivo")
        .navigationBarTitleDisplayMode(.inline)
        .task { await cargarYSondear() }
        .onDisappear {
            tareaSondeo?.cancel()
            tareaConfirmacion?.cancel()
        }
    }

    // MARK: - Cabecera

    private var cabecera: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Label(numeroContacto, systemImage: llamada.direction == "incoming" ? "phone.arrow.down.left.fill" : "phone.arrow.up.right.fill")
                    .font(.subheadline.weight(.semibold))
                Spacer()
                Text(nombreEstado(llamada.status))
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(colorEstado(llamada.status))
                    .padding(.horizontal, 9)
                    .padding(.vertical, 5)
                    .background(colorEstado(llamada.status).opacity(0.12), in: Capsule())
            }
            Text(llamada.goal)
                .font(.footnote)
                .foregroundStyle(.secondary)
                .lineLimit(2)
        }
        .padding(.horizontal)
        .padding(.vertical, 10)
    }

    private var numeroContacto: String {
        llamada.direction == "incoming" ? llamada.fromE164 : llamada.toE164
    }

    private func nombreEstado(_ status: String) -> String {
        switch status {
        case "draft": "Por confirmar"
        case "confirmed": "Confirmada"
        case "queued": "En cola"
        case "ringing": "Sonando"
        case "in_progress": "En curso"
        case "completed": "Completada"
        case "failed": "Fallida"
        case "busy": "Ocupado"
        case "no_answer": "Sin respuesta"
        case "cancelled": "Cancelada"
        default: status.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private func colorEstado(_ status: String) -> Color {
        switch status {
        case "completed": .green
        case "failed", "busy", "no_answer": .red
        case "ringing", "in_progress": EdecanTheme.morado
        default: EdecanTheme.azul
        }
    }

    // MARK: - Turnos

    /// Fusiona los eventos del servidor con los susurros optimistas
    /// pendientes de confirmar por sondeo, y se queda solo con lo que se
    /// muestra como turno (`transcript`/`susurro`) — el resto de tipos de
    /// evento (`cancelled`, `susurro_consumido`…) no le importan a esta
    /// pantalla.
    private var turnos: [PhoneCallEventOut] {
        let delServidor = llamada.events ?? []
        let idsDelServidor = Set(delServidor.map(\.id))
        let optimistasVigentes = susurrosOptimistas.filter { !idsDelServidor.contains($0.id) }
        return (delServidor + optimistasVigentes)
            .filter { $0.eventType == "transcript" || $0.eventType == "susurro" }
            .sorted { ($0.occurredAt ?? .distantPast) < ($1.occurredAt ?? .distantPast) }
    }

    private var listaDeTurnos: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if cargandoInicial && turnos.isEmpty {
                        ProgressView("Cargando la llamada…")
                            .frame(maxWidth: .infinity)
                            .padding(.top, 50)
                    } else if turnos.isEmpty {
                        EmptyStateView(
                            icono: "waveform",
                            titulo: tituloSinTurnos,
                            descripcion: descripcionSinTurnos
                        )
                        .padding(.top, 50)
                    }
                    ForEach(turnos) { evento in
                        turno(evento)
                    }
                    Color.clear.frame(height: 1).id(anclaFinal)
                }
                .padding()
            }
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: turnos.last?.id) { _, _ in
                withAnimation(.easeOut(duration: 0.2)) {
                    proxy.scrollTo(anclaFinal, anchor: .bottom)
                }
            }
        }
    }

    private var tituloSinTurnos: String {
        estadosTerminales.contains(llamada.status) ? "Sin conversación" : "Esperando la conversación"
    }

    private var descripcionSinTurnos: String {
        if estadosTerminales.contains(llamada.status) {
            return "La llamada terminó sin conversación: no contestaron o se cortó."
        }
        if llamada.status == "in_progress" {
            return "La llamada está en curso. Los turnos van a ir apareciendo aquí según se hable."
        }
        return "Todavía está sonando. En cuanto conteste, la conversación va a ir apareciendo aquí."
    }

    @ViewBuilder
    private func turno(_ evento: PhoneCallEventOut) -> some View {
        if evento.eventType == "susurro" {
            susurroBurbuja(evento)
        } else {
            transcriptBurbuja(evento)
        }
    }

    private func transcriptBurbuja(_ evento: PhoneCallEventOut) -> some View {
        let esAgente = evento.role == "assistant"
        return HStack {
            if esAgente { Spacer(minLength: 40) }
            VStack(alignment: esAgente ? .trailing : .leading, spacing: 4) {
                Text(esAgente ? "Edecán" : numeroContacto)
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.secondary)
                Text(evento.text ?? "")
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .foregroundStyle(esAgente ? .white : .primary)
                    .background {
                        if esAgente {
                            RoundedRectangle(cornerRadius: 18, style: .continuous).fill(EdecanTheme.degradado)
                        } else {
                            RoundedRectangle(cornerRadius: 18, style: .continuous).fill(.ultraThinMaterial)
                        }
                    }
            }
            if !esAgente { Spacer(minLength: 40) }
        }
    }

    private func susurroBurbuja(_ evento: PhoneCallEventOut) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "ear.and.waveform")
            Text("Le susurraste: “\(evento.text ?? "")”")
                .font(.footnote.weight(.medium))
        }
        .frame(maxWidth: .infinity)
        .multilineTextAlignment(.center)
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .foregroundStyle(EdecanTheme.morado)
        .background(EdecanTheme.morado.opacity(0.12), in: Capsule())
    }

    // MARK: - Susurro

    private var puedeSusurrar: Bool {
        llamada.status == "in_progress"
    }

    private var textoListoParaEnviar: Bool {
        !textoSusurro.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private var botonSusurroHabilitado: Bool {
        puedeSusurrar && textoListoParaEnviar && !enviandoSusurro
    }

    private var barraDeSusurro: some View {
        VStack(alignment: .leading, spacing: 6) {
            if confirmacionSusurroVisible {
                Label("Susurro enviado. Edecán lo va a incorporar en su próxima respuesta.", systemImage: "checkmark.circle.fill")
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.green)
                    .transition(.opacity)
            }
            if let errorSusurro {
                Text(errorSusurro)
                    .font(.caption)
                    .foregroundStyle(.red)
            }
            if !puedeSusurrar {
                Text(mensajeComposerDeshabilitado)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            HStack(spacing: 10) {
                TextField("Susúrrale algo a Edecán…", text: $textoSusurro, axis: .vertical)
                    .lineLimit(1...4)
                    .focused($campoEnfocado)
                    .submitLabel(.send)
                    .onSubmit {
                        guard botonSusurroHabilitado else { return }
                        enviarSusurro()
                    }
                    .textFieldStyle(.plain)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .tarjetaVidrio(esquina: 18)
                    .disabled(!puedeSusurrar || enviandoSusurro)

                Button(action: enviarSusurro) {
                    Group {
                        if enviandoSusurro {
                            ProgressView().tint(.white)
                        } else {
                            Image(systemName: "ear.and.waveform.fill")
                                .font(.system(size: 17, weight: .semibold))
                                .foregroundStyle(.white)
                        }
                    }
                    .frame(width: 42, height: 42)
                    .background(
                        botonSusurroHabilitado ? AnyShapeStyle(EdecanTheme.degradado) : AnyShapeStyle(.tertiary),
                        in: Circle()
                    )
                }
                .disabled(!botonSusurroHabilitado)
                .accessibilityLabel("Enviar susurro")
                .accessibilityHint("Se lo dice a Edecán a tiempo para su próxima respuesta en la llamada")
            }
        }
        .padding()
    }

    private var mensajeComposerDeshabilitado: String {
        if estadosTerminales.contains(llamada.status) {
            return "La llamada terminó — ya no se puede susurrar."
        }
        return "Vas a poder susurrar en cuanto la llamada esté en curso."
    }

    private func enviarSusurro() {
        guard let client = session.client else { return }
        let texto = textoSusurro.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !texto.isEmpty, botonSusurroHabilitado else { return }
        enviandoSusurro = true
        errorSusurro = nil
        Task {
            do {
                let confirmado = try await client.susurrarLlamada(id: llamada.id, texto: texto)
                susurrosOptimistas.append(
                    PhoneCallEventOut(
                        id: confirmado.id,
                        eventType: "susurro",
                        occurredAt: confirmado.queuedAt ?? Date(),
                        role: nil,
                        text: confirmado.text
                    )
                )
                textoSusurro = ""
                campoEnfocado = false
                mostrarConfirmacionSusurro()
            } catch APIClient.APIError.servidor(_, let mensaje) {
                errorSusurro = mensaje
            } catch {
                errorSusurro = error.localizedDescription
            }
            enviandoSusurro = false
        }
    }

    private func mostrarConfirmacionSusurro() {
        tareaConfirmacion?.cancel()
        withAnimation { confirmacionSusurroVisible = true }
        tareaConfirmacion = Task {
            try? await Task.sleep(for: .seconds(4))
            guard !Task.isCancelled else { return }
            withAnimation { confirmacionSusurroVisible = false }
        }
    }

    // MARK: - Sondeo

    private func cargarYSondear() async {
        await actualizar(primeraCarga: true)
        tareaSondeo?.cancel()
        tareaSondeo = Task {
            while !Task.isCancelled {
                try? await Task.sleep(for: intervaloSondeo)
                guard !Task.isCancelled else { return }
                await actualizar(primeraCarga: false)
                if estadosTerminales.contains(llamada.status) { break }
            }
        }
    }

    private func actualizar(primeraCarga: Bool) async {
        guard let client = session.client else { return }
        do {
            let actualizada = try await client.obtenerLlamada(id: llamada.id)
            llamada = actualizada
            errorCarga = nil
        } catch {
            // Un sondeo fallido no debe tapar la conversación ya cargada —
            // solo se avisa, la lista existente se queda tal cual.
            errorCarga = error.localizedDescription
        }
        if primeraCarga { cargandoInicial = false }
    }
}
