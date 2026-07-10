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

    func body(content: Content) -> some View {
        if #available(iOS 26, *) {
            content
                .glassEffect(.regular, in: .rect(cornerRadius: esquina, style: .continuous))
        } else {
            content
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: esquina, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: esquina, style: .continuous)
                        .strokeBorder(.white.opacity(0.15), lineWidth: 1)
                )
        }
    }
}

extension View {
    func tarjetaVidrio(esquina: CGFloat = 20) -> some View {
        modifier(TarjetaVidrio(esquina: esquina))
    }
}
