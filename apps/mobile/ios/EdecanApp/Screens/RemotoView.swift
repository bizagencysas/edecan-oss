import SwiftUI
import UIKit
import UniformTypeIdentifiers
import EdecanKit

/// "Remoto" — visor de control remoto tipo TeamViewer del Mac/PC companion
/// emparejado (`ARCHITECTURE.md` §13.c/§14, `docs/control-remoto.md`,
/// `apps/api/edecan_api/routers/remote.py`). Alcanzable desde el acceso
/// directo "Remoto" en ``InicioView`` — no es una pestaña propia (mismo
/// criterio que ``MisionesView``/``AutomatizacionesView``/``RecordatoriosView``,
/// `RootTabView` no cambia).
///
/// Guardrail de producto INNEGOCIABLE (`DIRECCION_ACTUAL.md`, "Control
/// remoto del Mac/PC desde el móvil"): emparejamiento explícito + aprobación
/// humana + indicador visible + botón Terminar SIEMPRE alcanzable. Esta
/// vista nunca muestra el visor sin, a la vez, mostrar el banner "Sesión
/// remota activa" Y el botón Terminar/Salir — ver ``bannerDeSesion(_:)``:
/// se renderiza siempre que `viewModel.sesion` no sea `nil`, sin importar el
/// `status` (pendiente, activa, denegada o terminada).
///
/// Nota sobre el flag `companion.remote_input`: a diferencia del panel web
/// (`ConsentGate.tsx`, que oculta el checkbox de "Controlar" por completo
/// sin el flag `companion.remote_input` en `/v1/me`), esta pantalla SIEMPRE
/// ofrece la opción "Controlar". Decisión deliberada (WP-V6-08): hoy
/// ``SessionStore``/`EdecanKit.Me` SÍ exponen `flags` (`Me.flags`), pero
/// NINGUNA otra pantalla de esta app los lee todavía — gatear justo esta
/// pantalla con un patrón que no existe en ningún otro lado de la app sería
/// introducir el primer caso especial, con el riesgo real de un flag que
/// no cargó todavía (`SessionStore.me == nil` en el primer *frame*) o que
/// simplemente no llegó en el diccionario ocultando una opción que sí
/// debería verse. En vez de eso: si el plan no trae el flag, el servidor
/// responde `403` con un mensaje claro en español
/// (`"El control remoto (teclado/mouse) no está disponible en tu plan."`,
/// `_require_remote_control` en `routers/remote.py`) que ``RemotoViewModel``
/// ya captura tal cual en `errorMensaje` — esta pantalla lo muestra sin
/// reinventar el copy. Si una ola futura agrega un lugar central para leer
/// flags de plan, esta pantalla es la primera candidata a adoptarlo.
struct RemotoView: View {
    @Environment(SessionStore.self) private var session
    @State private var viewModel = RemotoViewModel()

    @State private var quiereControl = false
    @State private var consentido = false
    @State private var escala: CGFloat = 1
    @State private var escalaBase: CGFloat = 1
    @State private var desplazamiento: CGSize = .zero
    @State private var desplazamientoBase: CGSize = .zero
    @State private var mostrarTeclado = false
    @State private var mostrarCompartir = false
    @State private var mostrarSelectorArchivo = false
    @State private var archivoParaCompartir: ArchivoCompartible?
    @State private var ultimoPuntoRemoto: CGPoint?

    private var sesionInmersiva: RemoteSession? {
        guard let sesion = viewModel.sesion, !sesion.esTerminal else { return nil }
        return sesion
    }

    var body: some View {
        Group {
            if let sesion = sesionInmersiva {
                visorInmersivo(sesion: sesion)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        if let error = viewModel.errorMensaje {
                            Text(error)
                                .font(.footnote)
                                .foregroundStyle(.red)
                                .padding(10)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(Color.red.opacity(0.1), in: RoundedRectangle(cornerRadius: 10))
                        }

                        if let sesion = viewModel.sesion {
                            visorTerminado(sesion: sesion)
                        } else {
                            consentimiento
                        }
                    }
                    .padding()
                }
                .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
            }
        }
        .navigationTitle(sesionInmersiva == nil ? "Remoto" : "")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(sesionInmersiva == nil ? .visible : .hidden, for: .navigationBar)
        .toolbar(sesionInmersiva == nil ? .visible : .hidden, for: .tabBar)
        .statusBarHidden(sesionInmersiva != nil)
        .onDisappear { viewModel.limpiar() }
    }

    // MARK: - Consentimiento (sin sesión activa)

    private var consentimiento: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(quiereControl ? "Iniciar sesión de control remoto" : "Iniciar sesión de vista remota")
                .font(.headline)
            Text(
                quiereControl
                    ? "Vas a ver y manejar tu computadora desde este iPhone."
                    : "Vas a ver la pantalla de tu computadora sin mover el mouse ni escribir."
            )
            .font(.subheadline)
            .foregroundStyle(.secondary)

            avisoDeVinculacion

            Toggle("También quiero usar el mouse y el teclado", isOn: $quiereControl)
                .tint(EdecanTheme.morado)
                .onChange(of: quiereControl) { consentido = false }

            Toggle(isOn: $consentido) {
                Text(
                    quiereControl
                        ? "Confirmo que quiero ver y controlar mi computadora desde este iPhone."
                        : "Confirmo que quiero ver la pantalla de mi computadora desde este iPhone."
                )
                .font(.footnote)
            }
            .tint(EdecanTheme.morado)

            Button {
                Task {
                    await viewModel.iniciar(kind: quiereControl ? "control" : "view", client: session.client)
                }
            } label: {
                if viewModel.iniciando {
                    ProgressView().frame(maxWidth: .infinity)
                } else {
                    Text(quiereControl ? "Iniciar sesión de control remoto" : "Iniciar sesión de vista remota")
                        .frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.borderedProminent)
            .tint(EdecanTheme.morado)
            .disabled(!consentido || viewModel.iniciando)
        }
        .padding(16)
        .tarjetaVidrio(esquina: 18)
    }

    private var avisoDeVinculacion: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label("Tu iPhone ya está vinculado", systemImage: "checkmark.shield.fill")
                .font(.caption.weight(.semibold))
            Text("Escaneaste el QR de esta computadora. Confirma esta sesión una vez y podrás terminarla cuando quieras.")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(EdecanTheme.azul.opacity(0.1), in: RoundedRectangle(cornerRadius: 10))
    }

    // MARK: - Visor inmersivo

    @ViewBuilder
    private func visorInmersivo(sesion: RemoteSession) -> some View {
        ZStack {
            Color.black.ignoresSafeArea()
            imagenInmersiva(sesion: sesion)
                .ignoresSafeArea()

            VStack(spacing: 12) {
                barraSuperiorInmersiva(sesion)
                Spacer(minLength: 12)

                if let error = viewModel.errorMensaje {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(.white)
                        .lineLimit(3)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(Color.red.opacity(0.88), in: Capsule())
                        .padding(.horizontal)
                }

                if let info = viewModel.infoMensaje {
                    Text(info)
                        .font(.caption)
                        .foregroundStyle(.white)
                        .lineLimit(3)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(Color.green.opacity(0.82), in: Capsule())
                        .padding(.horizontal)
                }

                if sesion.esControl {
                    if mostrarTeclado {
                        tecladoFlotante
                    }
                    if mostrarCompartir {
                        compartirFlotante
                    }
                    dockDeControl
                } else {
                    Text("Solo vista")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 9)
                        .background(.ultraThinMaterial, in: Capsule())
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
        }
        .background(Color.black)
        .fileImporter(
            isPresented: $mostrarSelectorArchivo,
            allowedContentTypes: [.item],
            allowsMultipleSelection: false
        ) { resultado in
            manejarSeleccionDeArchivo(resultado)
        }
        .sheet(item: $archivoParaCompartir) { archivo in
            HojaCompartir(urls: [archivo.url])
        }
    }

    /// Panel flotante "Compartir": portapapeles (traer/enviar) y transferencia
    /// de archivos (enviar desde el teléfono, traer del buzón de la Mac).
    private var compartirFlotante: some View {
        CompartirRemotoView(
            deshabilitado: viewModel.transfiriendo,
            cargandoArchivos: viewModel.cargandoArchivos,
            archivos: viewModel.archivosCompartidos,
            onTraerPortapapeles: {
                Task {
                    if let texto = await viewModel.traerPortapapeles(client: session.client) {
                        UIPasteboard.general.string = texto
                        viewModel.infoMensaje = texto.isEmpty
                            ? "El portapapeles de tu Mac estaba vacío."
                            : "Copiado a este teléfono."
                    }
                }
            },
            onEnviarPortapapeles: {
                let texto = UIPasteboard.general.string ?? ""
                guard !texto.isEmpty else {
                    viewModel.errorMensaje = "No hay texto en el portapapeles de este teléfono."
                    return
                }
                Task { await viewModel.enviarPortapapeles(texto, client: session.client) }
            },
            onEnviarArchivo: { mostrarSelectorArchivo = true },
            onRefrescar: { Task { await viewModel.listarArchivos(client: session.client) } },
            onTraerArchivo: { archivo in
                Task {
                    if let (datos, nombre) = await viewModel.traerArchivo(
                        nombre: archivo.name, client: session.client
                    ) {
                        prepararParaCompartir(datos: datos, nombre: nombre)
                    }
                }
            }
        )
    }

    private func manejarSeleccionDeArchivo(_ resultado: Result<[URL], Error>) {
        switch resultado {
        case .failure(let error):
            viewModel.errorMensaje = error.localizedDescription
        case .success(let urls):
            guard let url = urls.first else { return }
            // El picker entrega una URL con alcance de seguridad: hay que
            // pedir acceso explícito antes de leerla y soltarlo al terminar.
            let accedio = url.startAccessingSecurityScopedResource()
            defer { if accedio { url.stopAccessingSecurityScopedResource() } }
            do {
                let datos = try Data(contentsOf: url)
                Task {
                    await viewModel.enviarArchivo(
                        nombre: url.lastPathComponent, datos: datos, client: session.client
                    )
                }
            } catch {
                viewModel.errorMensaje = "No se pudo leer el archivo: \(error.localizedDescription)"
            }
        }
    }

    /// Escribe los bytes traídos de la Mac a un archivo temporal y lo presenta
    /// en la hoja de compartir de iOS (Guardar en Archivos, AirDrop, etc.).
    private func prepararParaCompartir(datos: Data, nombre: String) {
        let destino = FileManager.default.temporaryDirectory.appendingPathComponent(nombre)
        do {
            try datos.write(to: destino, options: .atomic)
            archivoParaCompartir = ArchivoCompartible(url: destino)
        } catch {
            viewModel.errorMensaje = "No se pudo preparar el archivo: \(error.localizedDescription)"
        }
    }

    private func barraSuperiorInmersiva(_ sesion: RemoteSession) -> some View {
        HStack(spacing: 10) {
            HStack(spacing: 8) {
                Circle()
                    .fill(sesion.status == "active" ? Color.green : Color.orange)
                    .frame(width: 9, height: 9)
                Text(sesion.status == "active" ? "En vivo" : "Conectando")
                    .font(.caption.weight(.semibold))
                if viewModel.enviandoInput {
                    ProgressView().controlSize(.mini).tint(.white)
                }
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(.ultraThinMaterial, in: Capsule())

            Spacer()

            Button(role: .destructive) {
                Task { await viewModel.terminar(client: session.client) }
            } label: {
                Label("Terminar", systemImage: "xmark")
                    .font(.caption.weight(.semibold))
            }
            .buttonStyle(.borderedProminent)
            .tint(.red)
            .disabled(viewModel.terminando)
        }
    }

    private var dockDeControl: some View {
        HStack(spacing: 8) {
            botonDock("Teclado", icono: mostrarTeclado ? "keyboard.chevron.compact.down" : "keyboard") {
                withAnimation(.snappy) {
                    mostrarTeclado.toggle()
                    if mostrarTeclado { mostrarCompartir = false }
                }
            }
            botonDock("Compartir", icono: mostrarCompartir ? "square.and.arrow.up.fill" : "square.and.arrow.up") {
                withAnimation(.snappy) {
                    mostrarCompartir.toggle()
                    if mostrarCompartir { mostrarTeclado = false }
                }
                if mostrarCompartir {
                    Task { await viewModel.listarArchivos(client: session.client) }
                }
            }
            botonDock("Derecho", icono: "cursorarrow.click.2") { enviarAccionDock(.rightClick) }
            botonDock("Subir", icono: "arrow.up") { enviarAccionDock(.scroll, deltaY: 520) }
            botonDock("Bajar", icono: "arrow.down") { enviarAccionDock(.scroll, deltaY: -520) }
            botonDock("Actualizar", icono: "arrow.clockwise") {
                Task { await viewModel.pedirFrame(client: session.client) }
            }
        }
        .padding(8)
        .background(.ultraThinMaterial, in: Capsule())
    }

    private func botonDock(
        _ titulo: String, icono: String, accion: @escaping () -> Void
    ) -> some View {
        Button(action: accion) {
            VStack(spacing: 3) {
                Image(systemName: icono).font(.body.weight(.semibold))
                Text(titulo).font(.system(size: 9, weight: .semibold))
            }
            .frame(maxWidth: .infinity)
            .foregroundStyle(.white)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(viewModel.enviandoInput && titulo != "Teclado")
    }

    private var tecladoFlotante: some View {
        TecladoRemotoView(
            deshabilitado: viewModel.enviandoInput,
            compacto: true,
            onEnviarTexto: { texto in
                Task { await viewModel.enviarKey(.texto(texto), client: session.client) }
            },
            onEnviarTecla: { tecla in
                Task { await viewModel.enviarKey(.tecla(tecla), client: session.client) }
            },
            onEnviarAtajo: { tecla, modifiers in
                Task { await viewModel.enviarKey(.tecla(tecla, modifiers: modifiers), client: session.client) }
            }
        )
        .transition(.move(edge: .bottom).combined(with: .opacity))
    }

    private func enviarAccionDock(_ accion: RemotePointerAccion, deltaY: Int = 0) {
        guard let frame = viewModel.frame else { return }
        let punto = ultimoPuntoRemoto ?? CGPoint(x: frame.width / 2, y: frame.height / 2)
        Task {
            await viewModel.enviarPointer(
                RemotePointerInput(
                    x: Int(punto.x), y: Int(punto.y), accion: accion, deltaY: deltaY
                ),
                client: session.client
            )
        }
    }

    private func visorTerminado(sesion: RemoteSession) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            bannerDeSesion(sesion)
            reservaSinFrame(sesion: sesion)
                .frame(maxWidth: .infinity, minHeight: 260)
                .background(Color.black, in: RoundedRectangle(cornerRadius: 14))
        }
        .padding(14)
        .tarjetaVidrio(esquina: 16)
    }

    /// Indicador visible PERMANENTE + botón Terminar SIEMPRE alcanzable
    /// mientras haya una sesión en pantalla — guardrail no negociable, ver
    /// el docstring de este archivo. Nunca se oculta condicionalmente, ni
    /// siquiera si la sesión ya está `denied`/`ended` (el botón simplemente
    /// pasa a decir "Salir" en vez de "Terminar").
    private func bannerDeSesion(_ sesion: RemoteSession) -> some View {
        HStack(spacing: 10) {
            Image(systemName: sesion.esControl ? "hand.point.up.left.fill" : "eye.fill")
                .foregroundStyle(colorBanner(sesion))
            VStack(alignment: .leading, spacing: 2) {
                Text(tituloBanner(sesion))
                    .font(.subheadline.weight(.semibold))
                if sesion.esControl && !sesion.esTerminal {
                    Text("Se puede mover el mouse y escribir en tu equipo.")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
            Button(sesion.esTerminal ? "Salir" : "Terminar", role: .destructive) {
                Task { await viewModel.terminar(client: session.client) }
            }
            .buttonStyle(.bordered)
            .disabled(viewModel.terminando)
        }
        .padding(12)
        .background(colorBanner(sesion).opacity(0.15), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private func colorBanner(_ sesion: RemoteSession) -> Color {
        if sesion.esTerminal { return .secondary }
        return sesion.esControl ? .red : .orange
    }

    private func tituloBanner(_ sesion: RemoteSession) -> String {
        switch sesion.status {
        case "denied":
            return "Sesión denegada en el companion"
        case "ended":
            return "Sesión terminada"
        case "pending":
            return sesion.esControl
                ? "Conectando control remoto con tu Mac…"
                : "Conectando vista remota con tu Mac…"
        default:
            return sesion.esControl
                ? "Sesión de control activa: se está controlando tu equipo"
                : "Sesión de vista activa: solo lectura"
        }
    }

    @ViewBuilder
    private func imagenInmersiva(sesion: RemoteSession) -> some View {
        GeometryReader { geo in
            Group {
                if let frame = viewModel.frame, let imagen = decodificarImagen(frame) {
                    Image(uiImage: imagen)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .scaleEffect(escala)
                        .offset(desplazamiento)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .gesture(
                            MagnificationGesture()
                                .onChanged { valor in
                                    escala = min(max(escalaBase * valor, 1), 4)
                                    // Re-acotar en vivo: al alejar, el desplazamiento
                                    // permitido se encoge y el frame vuelve solo al centro.
                                    desplazamiento = Self.desplazamientoAcotado(
                                        desplazamiento, escala: escala, tamano: geo.size
                                    )
                                }
                                .onEnded { _ in
                                    escalaBase = escala
                                    desplazamientoBase = desplazamiento
                                    if escala <= 1 {
                                        withAnimation {
                                            desplazamiento = .zero
                                            desplazamientoBase = .zero
                                        }
                                    }
                                }
                        )
                        .simultaneousGesture(gestoDePuntero(sesion: sesion, frame: frame, tamano: geo.size))
                        .simultaneousGesture(gestoDeArrastre(sesion: sesion, frame: frame, tamano: geo.size))
                        .onTapGesture(count: 2) {
                            guard !sesion.esControl else { return } // el doble tap ya es "double_click" en control
                            withAnimation {
                                escala = 1
                                escalaBase = 1
                                desplazamiento = .zero
                                desplazamientoBase = .zero
                            }
                        }
                } else {
                    reservaSinFrame(sesion: sesion)
                        .frame(width: geo.size.width, height: geo.size.height)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.black)
    }

    /// Clic (`SpatialTapGesture(count: 1)`) → `input_pointer {accion: "click"}`,
    /// doble clic (`count: 2`) → `"double_click"` — mapeados a coordenadas
    /// REALES del frame vía ``RemoteCoordinateMapper`` (no las de la pantalla
    /// del teléfono, que casi nunca coinciden con la resolución del equipo
    /// remoto). Sin efecto en sesiones `kind="view"` o ya terminales.
    /// El punto pasa antes por ``puntoSinZoom``: los gestos viven FUERA del
    /// `scaleEffect`/`offset`, así que reportan coordenadas del contenedor y
    /// hay que des-proyectarlas al espacio sin zoom que espera el mapper.
    private func gestoDePuntero(sesion: RemoteSession, frame: RemoteFrame, tamano: CGSize) -> some Gesture {
        SpatialTapGesture(count: 2)
            .exclusively(before: SpatialTapGesture(count: 1))
            .onEnded { resultado in
                guard sesion.esControl, !sesion.esTerminal else { return }
                switch resultado {
                case .first(let valor):
                    enviarPointer(
                        local: puntoSinZoom(valor.location, tamano: tamano),
                        tamanoElemento: tamano, frame: frame, accion: .doubleClick
                    )
                case .second(let valor):
                    enviarPointer(
                        local: puntoSinZoom(valor.location, tamano: tamano),
                        tamanoElemento: tamano, frame: frame, accion: .click
                    )
                }
            }
    }

    /// Sin zoom (1×), arrastrar sobre la pantalla remota mueve ventanas,
    /// selecciona texto y opera sliders con un único comando acotado. CON
    /// zoom, el mismo dedo panea la vista — sin esto el zoom quedaba clavado
    /// al centro y el resto de la pantalla era inalcanzable (el drag remoto
    /// sigue disponible: basta volver a 1× con doble tap o pellizcando).
    private func gestoDeArrastre(
        sesion: RemoteSession, frame: RemoteFrame, tamano: CGSize
    ) -> some Gesture {
        DragGesture(minimumDistance: 8, coordinateSpace: .local)
            .onChanged { valor in
                guard escala > 1 else { return }
                desplazamiento = Self.desplazamientoAcotado(
                    CGSize(
                        width: desplazamientoBase.width + valor.translation.width,
                        height: desplazamientoBase.height + valor.translation.height
                    ),
                    escala: escala, tamano: tamano
                )
            }
            .onEnded { valor in
                if escala > 1 {
                    desplazamientoBase = desplazamiento
                    return
                }
                guard sesion.esControl, !sesion.esTerminal else { return }
                guard let inicio = RemoteCoordinateMapper.mapear(
                    puntoLocalX: Double(valor.startLocation.x), puntoLocalY: Double(valor.startLocation.y),
                    anchoElemento: Double(tamano.width), altoElemento: Double(tamano.height), frame: frame
                ), let fin = RemoteCoordinateMapper.mapear(
                    puntoLocalX: Double(valor.location.x), puntoLocalY: Double(valor.location.y),
                    anchoElemento: Double(tamano.width), altoElemento: Double(tamano.height), frame: frame
                ) else { return }
                ultimoPuntoRemoto = CGPoint(x: fin.x, y: fin.y)
                Task {
                    await viewModel.enviarPointer(
                        RemotePointerInput(
                            x: fin.x, y: fin.y, accion: .drag,
                            startX: inicio.x, startY: inicio.y
                        ),
                        client: session.client
                    )
                }
            }
    }

    /// Des-proyecta un punto del espacio del contenedor (post `scaleEffect` +
    /// `offset`, ambos anclados al centro) al espacio sin zoom que espera
    /// ``RemoteCoordinateMapper``. Con `escala == 1` y desplazamiento cero es
    /// la identidad, así que el camino sin zoom queda intacto.
    private func puntoSinZoom(_ punto: CGPoint, tamano: CGSize) -> CGPoint {
        let centroX = tamano.width / 2
        let centroY = tamano.height / 2
        return CGPoint(
            x: centroX + (punto.x - desplazamiento.width - centroX) / escala,
            y: centroY + (punto.y - desplazamiento.height - centroY) / escala
        )
    }

    /// Acota el paneo para que el frame nunca se salga del todo de la vista:
    /// con `scaleEffect` anclado al centro, el excedente visible por eje es
    /// `tamano * (escala - 1)`, mitad hacia cada lado. A 1× el límite es 0 y
    /// el frame queda perfectamente centrado.
    private static func desplazamientoAcotado(
        _ propuesta: CGSize, escala: CGFloat, tamano: CGSize
    ) -> CGSize {
        let limiteX = max(0, tamano.width * (escala - 1) / 2)
        let limiteY = max(0, tamano.height * (escala - 1) / 2)
        return CGSize(
            width: min(max(propuesta.width, -limiteX), limiteX),
            height: min(max(propuesta.height, -limiteY), limiteY)
        )
    }

    private func enviarPointer(
        local: CGPoint, tamanoElemento: CGSize, frame: RemoteFrame, accion: RemotePointerAccion
    ) {
        guard let punto = RemoteCoordinateMapper.mapear(
            puntoLocalX: Double(local.x), puntoLocalY: Double(local.y),
            anchoElemento: Double(tamanoElemento.width), altoElemento: Double(tamanoElemento.height),
            frame: frame
        ) else { return }
        ultimoPuntoRemoto = CGPoint(x: punto.x, y: punto.y)
        Task {
            await viewModel.enviarPointer(
                RemotePointerInput(x: punto.x, y: punto.y, accion: accion), client: session.client
            )
        }
    }

    private func reservaSinFrame(sesion: RemoteSession) -> some View {
        VStack(spacing: 10) {
            if sesion.esTerminal {
                Image(systemName: sesion.status == "denied" ? "hand.raised.slash.fill" : "checkmark.circle.fill")
                    .font(.largeTitle)
                    .foregroundStyle(.white.opacity(0.7))
                Text(
                    sesion.status == "denied"
                        ? "El companion denegó esta sesión: no va a haber ningún frame."
                        : "Esta sesión ya terminó."
                )
                .font(.footnote)
                .foregroundStyle(.white.opacity(0.7))
            } else {
                ProgressView().tint(.white)
                // Nada de "esperando aprobación": la app instalada auto-aprueba la
                // sesión (la confirmación real ya se dio en este teléfono). Lo único
                // que puede aparecer en la Mac es el diálogo de permisos de macOS.
                Text("Conectando con tu Mac… Si macOS muestra una solicitud de permisos en la Mac, acéptala ahí.")
                    .font(.footnote)
                    .foregroundStyle(.white.opacity(0.7))
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 24)
                // El motivo real del fallo (p. ej. permisos de macOS) antes
                // solo se veía fuera de esta reserva — aquí es donde la
                // persona está mirando mientras "no pasa nada".
                if let error = viewModel.errorMensaje, !error.isEmpty {
                    Text(error)
                        .font(.caption2)
                        .foregroundStyle(Color.red.opacity(0.9))
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 24)
                }
            }
        }
    }

    private func decodificarImagen(_ frame: RemoteFrame) -> UIImage? {
        guard let data = Data(base64Encoded: frame.imageB64) else { return nil }
        return UIImage(data: data)
    }

}

private struct EtiquetaEstadoSesionRemota: View {
    let status: String

    var body: some View {
        Text(etiqueta)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(color.opacity(0.18), in: Capsule())
            .foregroundStyle(color)
    }

    private var etiqueta: String {
        switch status {
        case "pending": return "Conectando"
        case "active": return "Activa"
        case "ended": return "Terminada"
        case "denied": return "Denegada"
        default: return status.capitalized
        }
    }

    private var color: Color {
        switch status {
        case "active": return .green
        case "pending": return EdecanTheme.azul
        case "denied": return .red
        default: return .secondary
        }
    }
}

/// Teclado accesorio simple del modo "Control": un campo de texto + botón
/// Enviar (manda `input_key {texto}` carácter por carácter, como si se
/// tipeara en el equipo remoto), teclas especiales y atajos que soporta
/// `edecan_companion.actions._SPECIAL_KEYS` (``RemoteSpecialKey``). El
/// clic/doble clic sobre el frame vive en ``RemotoView`` — esto es solo
/// teclado, mismo reparto de responsabilidades que
/// `apps/web/src/components/remoto/RemoteControlPanel.tsx`.
private struct ControlesPunteroView: View {
    let deshabilitado: Bool
    let onAccion: (RemotePointerAccion, Int) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Mouse y scroll").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            HStack {
                Button("Clic derecho") { onAccion(.rightClick, 0) }
                Button("Scroll ↑") { onAccion(.scroll, 420) }
                Button("Scroll ↓") { onAccion(.scroll, -420) }
            }
            .buttonStyle(.bordered)
            .disabled(deshabilitado)
            Text("Toca para hacer clic. Toca dos veces para abrir. Arrastra sobre la pantalla para mover o seleccionar. Pellizca para acercar; con zoom, arrastra para recorrer la pantalla (vuelve a 1× para arrastrar ventanas).")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(14)
        .tarjetaVidrio(esquina: 16)
    }
}

private struct TecladoRemotoView: View {
    let deshabilitado: Bool
    var compacto = false
    let onEnviarTexto: (String) -> Void
    let onEnviarTecla: (RemoteSpecialKey) -> Void
    let onEnviarAtajo: (RemoteSpecialKey, [RemoteKeyModifier]) -> Void

    @State private var texto = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Escribir en el equipo remoto")
                .font(.caption.weight(.semibold))
                .foregroundStyle(compacto ? Color.white : Color.secondary)

            HStack {
                TextField("Escribe aquí y pulsa Enviar…", text: $texto)
                    .textFieldStyle(.roundedBorder)
                    .disabled(deshabilitado)
                    .onSubmit(enviarTexto)
                Button("Enviar", action: enviarTexto)
                    .buttonStyle(.borderedProminent)
                    .tint(EdecanTheme.morado)
                    .disabled(deshabilitado || texto.isEmpty)
            }

            if !compacto { Text("Teclas especiales")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.secondary)
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(RemoteSpecialKey.allCases, id: \.self) { tecla in
                        Button(tecla.etiqueta) { onEnviarTecla(tecla) }
                            .buttonStyle(.bordered)
                            .disabled(deshabilitado)
                    }
                }
            }

            if !compacto { Text("Atajos").font(.caption2.weight(.semibold)).foregroundStyle(.secondary) }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    Button("⌘A") { onEnviarAtajo(.a, [.command]) }
                    Button("⌘C") { onEnviarAtajo(.c, [.command]) }
                    Button("⌘V") { onEnviarAtajo(.v, [.command]) }
                    Button("⌘X") { onEnviarAtajo(.x, [.command]) }
                    Button("⌘Z") { onEnviarAtajo(.z, [.command]) }
                    Button("⌘S") { onEnviarAtajo(.s, [.command]) }
                }
                .buttonStyle(.bordered)
                .disabled(deshabilitado)
            }
        }
        .padding(14)
        .background(compacto ? AnyShapeStyle(.ultraThinMaterial) : AnyShapeStyle(Color.clear), in: RoundedRectangle(cornerRadius: 16))
        .modifier(TecladoTarjetaModifier(compacto: compacto))
    }

    private func enviarTexto() {
        guard !texto.isEmpty else { return }
        onEnviarTexto(texto)
        texto = ""
    }
}

private struct TecladoTarjetaModifier: ViewModifier {
    let compacto: Bool

    @ViewBuilder
    func body(content: Content) -> some View {
        if compacto {
            content
        } else {
            content.tarjetaVidrio(esquina: 16)
        }
    }
}

// MARK: - Compartir (portapapeles + archivos)

/// URL temporal identificable para `.sheet(item:)` — la hoja de compartir de
/// iOS necesita un `Identifiable` y la `URL` sola no lo es.
private struct ArchivoCompartible: Identifiable {
    let id = UUID()
    let url: URL
}

/// Envoltorio de `UIActivityViewController` para presentar archivos traídos de
/// la Mac (Guardar en Archivos, AirDrop, Compartir…).
private struct HojaCompartir: UIViewControllerRepresentable {
    let urls: [URL]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: urls, applicationActivities: nil)
    }

    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}

/// Panel flotante del dock: portapapeles (traer/enviar) y transferencia de
/// archivos con el buzón compartido de la Mac.
private struct CompartirRemotoView: View {
    let deshabilitado: Bool
    let cargandoArchivos: Bool
    let archivos: [RemoteSharedFile]
    let onTraerPortapapeles: () -> Void
    let onEnviarPortapapeles: () -> Void
    let onEnviarArchivo: () -> Void
    let onRefrescar: () -> Void
    let onTraerArchivo: (RemoteSharedFile) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Portapapeles").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            HStack(spacing: 10) {
                Button {
                    onTraerPortapapeles()
                } label: {
                    Label("Traer de la Mac", systemImage: "arrow.down.doc")
                        .frame(maxWidth: .infinity)
                }
                Button {
                    onEnviarPortapapeles()
                } label: {
                    Label("Enviar a la Mac", systemImage: "arrow.up.doc")
                        .frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.bordered)
            .disabled(deshabilitado)

            Divider()

            HStack {
                Text("Archivos").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                Spacer()
                if cargandoArchivos {
                    ProgressView().controlSize(.mini)
                } else {
                    Button {
                        onRefrescar()
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .buttonStyle(.plain)
                }
            }

            Button {
                onEnviarArchivo()
            } label: {
                Label("Enviar un archivo a la Mac…", systemImage: "square.and.arrow.up")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled(deshabilitado)

            if archivos.isEmpty {
                Text("La carpeta «Compartidos» de tu Mac está vacía. Los archivos que envíes aparecerán ahí, y lo que dejes ahí en la Mac lo podrás traer aquí.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            } else {
                Text("En «Compartidos» de tu Mac — toca para traer:")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                ScrollView {
                    VStack(spacing: 6) {
                        ForEach(archivos) { archivo in
                            Button {
                                onTraerArchivo(archivo)
                            } label: {
                                HStack(spacing: 10) {
                                    Image(systemName: "doc")
                                    VStack(alignment: .leading, spacing: 1) {
                                        Text(archivo.name).font(.caption).lineLimit(1)
                                        Text(ByteCountFormatter.string(
                                            fromByteCount: Int64(archivo.bytes), countStyle: .file
                                        ))
                                        .font(.system(size: 10))
                                        .foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    Image(systemName: "arrow.down.circle")
                                }
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .disabled(deshabilitado)
                        }
                    }
                }
                .frame(maxHeight: 160)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
    }
}
