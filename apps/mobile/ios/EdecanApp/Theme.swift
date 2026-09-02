import SwiftUI

/// Colores y helpers visuales compartidos por toda la app — acentos
/// morado/azul del mockup del panel web (`apps/web/src/app/(app)/app/`,
/// ver `DIRECCION_ACTUAL.md`) y el material Liquid Glass que usa
/// ``RootTabView``.
enum EdecanTheme {
    /// Morado principal del mockup. Valor fijo (no `Color("AccentColor")`)
    /// para que los degradados de esta paleta no dependan del asset
    /// catalog — `AccentColor` en `Assets.xcassets` usa el mismo tono para
    /// que tintes del sistema (switches, links) coincidan.
    static let morado = Color(red: 0.51, green: 0.36, blue: 0.96) // #8257F5
    static let azul = Color(red: 0.29, green: 0.49, blue: 0.98) // #4A7DFA

    static let degradado = LinearGradient(
        colors: [morado, azul],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )

    /// Fondo translúcido para tarjetas sobre Liquid Glass — funciona en
    /// claro y oscuro sin condicionales propios.
    static func fondoTarjeta(_ scheme: ColorScheme) -> Color {
        scheme == .dark ? Color.white.opacity(0.06) : Color.black.opacity(0.04)
    }

    // MARK: - Bots / Grok Bot (light + Liquid Glass)

    /// Degradado airy para pantallas Bots — light mode puro, sin oscurecer.
    static let fondoBotsLight = LinearGradient(
        colors: [
            Color(red: 0.98, green: 0.98, blue: 1.0),
            Color(red: 0.94, green: 0.96, blue: 1.0),
            Color(red: 0.97, green: 0.95, blue: 0.99),
        ],
        startPoint: .top,
        endPoint: .bottom
    )

    /// Negro sólido del botón enviar estilo Grok.
    static let botonEnviarNegro = Color(red: 0.08, green: 0.08, blue: 0.10)
}

/// Aplica el material Liquid Glass real de iOS 26 (`glassEffect(_:in:)`,
/// `SwiftUICore.Glass`) con fallback a `.ultraThinMaterial` para builds con
/// un deployment target menor. `deploymentTarget.iOS` en `project.yml` ya
/// está fijo en `26.0` (todo el proyecto exige iOS 26+), así que la rama
/// `else` de abajo nunca corre en un build real de este target — se deja de
/// todas formas como código defensivo real (no un `#if` que dependa de que
/// el SDK local tenga el símbolo) por si algún día el deployment target
/// baja, o si `EdecanKit`/otro target reutiliza este modifier con un
/// mínimo distinto.
struct TarjetaVidrio: ViewModifier {
    var esquina: CGFloat = 20
    var tint: Color?
    /// Sombra suave para tarjetas flotantes sobre el fondo airy (lista Bots).
    var flotante: Bool = false

    func body(content: Content) -> some View {
        let forma = RoundedRectangle(cornerRadius: esquina, style: .continuous)
        Group {
            if #available(iOS 26, *) {
                let efecto: Glass = {
                    if let tint {
                        return .regular.tint(tint.opacity(0.12))
                    }
                    return .regular
                }()
                content
                    .glassEffect(efecto, in: .rect(cornerRadius: esquina, style: .continuous))
            } else {
                content
                    .background(.ultraThinMaterial, in: forma)
                    .overlay(forma.strokeBorder(.white.opacity(0.15), lineWidth: 1))
            }
        }
        .shadow(
            color: flotante ? Color.black.opacity(0.06) : .clear,
            radius: flotante ? 18 : 0,
            y: flotante ? 8 : 0
        )
    }
}

/// Capsula de Liquid Glass para cabeceras y composer flotante.
struct CapsulaVidrio: ViewModifier {
    func body(content: Content) -> some View {
        if #available(iOS 26, *) {
            content
                .glassEffect(.regular, in: .capsule)
        } else {
            content
                .background(.ultraThinMaterial, in: Capsule())
                .overlay(Capsule().strokeBorder(.white.opacity(0.18), lineWidth: 0.8))
        }
    }
}

/// Botón circular de vidrio para cabeceras Bots (búsqueda, crear).
struct BotonVidrioCircular: View {
    let sistema: String
    let etiqueta: String
    var accion: (() -> Void)?

    var body: some View {
        Button {
            accion?()
        } label: {
            Image(systemName: sistema)
                .font(.system(size: 18, weight: .medium))
                .foregroundStyle(.primary)
                .frame(width: 44, height: 44)
                .modifier(CapsulaVidrio())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(etiqueta)
    }
}

/// Botón enviar sólido negro estilo Grok Bot.
struct BotonEnviarNegro: View {
    var habilitado: Bool
    var cargando: Bool = false
    var accion: () -> Void

    var body: some View {
        Button(action: accion) {
            Group {
                if cargando {
                    ProgressView()
                        .tint(.white)
                } else {
                    Image(systemName: "arrow.up")
                        .font(.system(size: 17, weight: .bold))
                        .foregroundStyle(.white)
                }
            }
            .frame(width: 36, height: 36)
            .background(
                Circle()
                    .fill(habilitado ? EdecanTheme.botonEnviarNegro : Color.secondary.opacity(0.35))
            )
        }
        .disabled(!habilitado || cargando)
        .accessibilityLabel("Enviar")
        .animation(.easeOut(duration: 0.15), value: habilitado)
    }
}

/// Agrupa piezas de vidrio para morphing (`GlassEffectContainer` + IDs).
struct ContenedorVidrioBots<Content: View>: View {
    var spacing: CGFloat = 12
    @ViewBuilder var content: () -> Content

    var body: some View {
        if #available(iOS 26, *) {
            GlassEffectContainer(spacing: spacing) {
                content()
            }
        } else {
            content()
        }
    }
}

/// Fondo light mode compartido por pantallas Bots.
struct FondoBotsLight: View {
    var body: some View {
        EdecanTheme.fondoBotsLight
            .ignoresSafeArea()
    }
}

extension View {
    func tarjetaVidrio(esquina: CGFloat = 20, tint: Color? = nil, flotante: Bool = false) -> some View {
        modifier(TarjetaVidrio(esquina: esquina, tint: tint, flotante: flotante))
    }

    /// Tarjeta de vidrio con sombra para filas flotantes en ScrollView (sin List).
    func tarjetaVidrioFlotante(esquina: CGFloat = 18, tint: Color? = nil) -> some View {
        tarjetaVidrio(esquina: esquina, tint: tint, flotante: true)
    }

    /// Concrete `ModifiedContent` avoids opaque MainActor-isolated `some View`
    /// returns that Swift 6 RegionIsolation rejects inside Sendable closures
    /// (e.g. `PhotosPicker` labels).
    func capsulaVidrio() -> ModifiedContent<Self, CapsulaVidrio> {
        modifier(CapsulaVidrio())
    }

    /// Morph ID for Liquid Glass containers. String IDs only (call sites use
    /// literals). Modifier keeps the iOS 26 availability check off opaque
    /// extension returns that Swift 6 RegionIsolation rejects.
    func vidrioMorphID(_ id: String, in namespace: Namespace.ID) -> ModifiedContent<Self, VidrioMorphIDModifier> {
        modifier(VidrioMorphIDModifier(id: id, namespace: namespace))
    }

    func estiloPantallaBots() -> some View {
        self
            .preferredColorScheme(.light)
            .background(FondoBotsLight())
    }
}

struct VidrioMorphIDModifier: ViewModifier {
    let id: String
    let namespace: Namespace.ID

    func body(content: Content) -> some View {
        if #available(iOS 26, *) {
            content.glassEffectID(id, in: namespace)
        } else {
            content
        }
    }
}
