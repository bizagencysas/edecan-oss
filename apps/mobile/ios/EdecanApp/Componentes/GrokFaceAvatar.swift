import EdecanKit
import SwiftUI

/// Cara de bot estilo Grok: COLOR PLANO saturado + ojitos blancos inclinados,
/// sobre la forma que asignó el backend (círculo, óvalo, hexágono…).
/// VIVA por movimiento, no por ruido visual: parpadea, la mirada deambula,
/// se inclina con curiosidad, respira y flota; al trabajar habla (boca que
/// abre-cierra) y se enciende (aro de Liquid Glass + halo pulsante).
///
/// Reparto (investigación SwiftUI/iOS 26): los gestos lentos son
/// `.repeatForever` de Core Animation (cero CPU por frame); parpadeo, mirada
/// y curiosidad son EVENTOS aleatorios con fase por bot; el reloj
/// (`TimelineView` a 30 fps) solo alimenta la boca mientras habla.
struct CaraOrbe: View {
    let nombre: String
    let formaBot: String
    let fillHex: String
    let accentHex: String?
    let ojoIzq: OjoDeCara?
    let ojoDer: OjoDeCara?
    var size: CGFloat = 40
    var showOnline: Bool = false
    var animado: Bool = true
    var activo: Bool = false

    /// Fase estable derivada del nombre (cada cara respira a su ritmo).
    private var fase: Double {
        let suma = nombre.unicodeScalars.reduce(0) { $0 &+ Int($1.value) }
        return Double(suma % 628) / 100.0
    }

    // Gestos lentos (Core Animation, repeatForever).
    @State private var respiracion: CGFloat = 1
    @State private var flotacion: CGFloat = 0
    @State private var inclinacion: Double = 0
    // Entrada con rebote.
    @State private var entrada = false
    // Eventos de personalidad.
    @State private var aperturaOjos: CGFloat = 1
    @State private var miradaX: CGFloat = 0
    @State private var miradaY: CGFloat = 0
    @State private var curiosidad: Double = 0
    // Halo de trabajando.
    @State private var pulsoHalo: CGFloat = 1
    @State private var haloEncendido = false

    var body: some View {
        ZStack {
            cara
            if showOnline {
                Circle()
                    .fill(Color.green)
                    .frame(width: size * 0.24, height: size * 0.24)
                    .overlay(Circle().stroke(Color(.systemBackground), lineWidth: 2))
                    .offset(x: size * 0.34, y: size * 0.34)
                    .accessibilityHidden(true)
            }
        }
        .scaleEffect(entrada ? 1 : 0.5)
        .accessibilityLabel(nombre)
        .task(id: nombre) {
            guard animado else { return }
            await cicloVida()
        }
        .onAppear {
            withAnimation(.spring(response: 0.55, dampingFraction: 0.62)) {
                entrada = true
            }
            withAnimation(.easeInOut(duration: activo ? 1.4 : 2.6).repeatForever(autoreverses: true)) {
                respiracion = 1 + (activo ? 0.055 : 0.035)
            }
            withAnimation(.easeInOut(duration: activo ? 1.8 : 3.1).repeatForever(autoreverses: true)) {
                flotacion = size * (activo ? 0.05 : 0.03)
            }
            withAnimation(.easeInOut(duration: activo ? 1.9 : 3.6).repeatForever(autoreverses: true)) {
                inclinacion = activo ? 3.0 : 1.6
            }
        }
    }

    // MARK: - La cara

    @ViewBuilder
    private var cara: some View {
        if animado {
            TimelineView(.periodic(from: .now, by: 1.0 / 30.0)) { timeline in
                contenido(t: timeline.date.timeIntervalSinceReferenceDate + fase)
            }
        } else {
            contenido(t: fase)
        }
    }

    private func contenido(t: Double) -> some View {
        // El look: COLOR PLANO saturado + ojitos blancos. Sin auroras oscuras
        // ni brillos que emborronen en modo oscuro — la vida viene del
        // MOVIMIENTO (parpadeo, mirada, vaivén), no del ruido visual. Solo un
        // degradado casi imperceptible da el volumen.
        return ZStack {
            forma
                .fill(
                    LinearGradient(
                        colors: [fill.mezclado(con: .white, 0.14), fill],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
            ojos(apertura: aperturaOjos, miradaX: miradaX, miradaY: miradaY)
            if activo {
                bocaHablando(t: t)
            }
        }
        .frame(width: size, height: size)
        .clipShape(forma)
        .overlay {
            forma.stroke(Color.white.opacity(0.18), lineWidth: max(size * 0.02, 0.7))
        }
        .scaleEffect(respiracion, anchor: .center)
        .offset(y: flotacion)
        .rotationEffect(.degrees(inclinacion + curiosidad))
        .overlay {
            if activo {
                aroTrabajando
            }
        }
    }

    private var forma: FormaAvatarBot {
        FormaAvatarBot(nombre: formaBot)
    }

    private var fill: Color { Color(hex: fillHex) }

    private var acento: Color {
        if let accentHex, !accentHex.isEmpty {
            return Color(hex: accentHex)
        }
        return EdecanTheme.morado
    }

    /// Boca solo cuando TRABAJA: abre-cierra suave (~2.6 Hz) — la señal de
    /// «está activo». En reposo, sin boca: mirada limpia como la de Grok.
    @ViewBuilder
    private func bocaHablando(t: Double) -> some View {
        let ancho = size * 0.26
        let apertura = max(0.18, sin(t * 2 * .pi * 2.6)) * ancho * 0.38
        Ellipse()
            .fill(.white.opacity(0.95))
            .frame(width: ancho, height: max(apertura, 1.4))
            .position(x: size * 0.5, y: size * 0.68)
    }

    /// Anillo «encendido»: pulso del acento + aro blanco de Liquid Glass.
    private var aroTrabajando: some View {
        let acento = acento
        return ZStack {
            Circle()
                .stroke(acento.opacity(0.65), lineWidth: max(size * 0.055, 1.5))
                .blur(radius: max(size * 0.06, 1.5))
                .scaleEffect(pulsoHalo)
                .opacity(haloEncendido ? 0.9 : 0.35)
            Circle()
                .stroke(.white.opacity(0.75), lineWidth: max(size * 0.022, 0.6))
                .frame(width: size * 1.06, height: size * 1.06)
                .glassEffect(.regular.tint(acento.opacity(0.22)), in: .circle)
        }
        .frame(width: size, height: size)
        .onAppear {
            withAnimation(.easeInOut(duration: 1.1).repeatForever(autoreverses: true)) {
                pulsoHalo = 1.13
                haloEncendido = true
            }
        }
    }

    // MARK: - Ojos

    private func ojos(apertura: CGFloat, miradaX: CGFloat, miradaY: CGFloat) -> some View {
        ZStack {
            if let left = ojoIzq {
                ojo(left, apertura: apertura, miradaX: miradaX, miradaY: miradaY)
            }
            if let right = ojoDer {
                ojo(right, apertura: apertura, miradaX: miradaX, miradaY: miradaY)
            }
        }
        .frame(width: size, height: size)
    }

    private func ojo(
        _ eye: OjoDeCara,
        apertura: CGFloat,
        miradaX: CGFloat,
        miradaY: CGFloat
    ) -> some View {
        // Ojitos blancos inclinados, con pupila que deambula (evento).
        let ancho = size * eye.rx * 2 * 1.22
        let alto = max(size * eye.ry * 2 * apertura * 1.22, 1.6)
        return Ellipse()
            .fill(.white)
            .frame(width: ancho, height: alto)
            .overlay {
                Circle()
                    .fill(fill.mezclado(con: .black, 0.45))
                    .frame(width: ancho * 0.38, height: max(alto * 0.5, 1.2))
                    .offset(
                        x: ancho * (0.06 + 0.14 * miradaX),
                        y: alto * (0.10 + 0.14 * miradaY)
                    )
            }
            .rotationEffect(.degrees(eye.rotation))
            .position(x: size * eye.x, y: size * eye.y)
    }

    // MARK: - Ciclo de vida (eventos de personalidad)

    /// Parpadeo, mirada y curiosidad como eventos aleatorios desincronizados
    /// por bot: el personaje parpadea, mira hacia un lado, y de vez en cuando
    /// inclina la cabeza como preguntando.
    private func cicloVida() async {
        try? await Task.sleep(for: .seconds(0.8 + fase.truncatingRemainder(dividingBy: 3)))
        while !Task.isCancelled {
            withAnimation(.easeIn(duration: 0.08)) { aperturaOjos = 0.06 }
            try? await Task.sleep(for: .seconds(0.09))
            withAnimation(.easeOut(duration: 0.12)) { aperturaOjos = 1 }

            if Double.random(in: 0...1) < 0.45 {
                let objetivo = (x: CGFloat.random(in: -1...1), y: CGFloat.random(in: -0.6...0.6))
                withAnimation(.spring(response: 0.4, dampingFraction: 0.7)) {
                    miradaX = objetivo.x
                    miradaY = objetivo.y
                }
                try? await Task.sleep(for: .seconds(Double.random(in: 0.7...1.3)))
                withAnimation(.spring(response: 0.5, dampingFraction: 0.8)) {
                    miradaX = 0
                    miradaY = 0
                }
            }

            if Double.random(in: 0...1) < 0.18 {
                withAnimation(.spring(response: 0.5, dampingFraction: 0.55)) {
                    curiosidad = Double.random(in: -7...7)
                }
                try? await Task.sleep(for: .seconds(Double.random(in: 0.8...1.4)))
                withAnimation(.spring(response: 0.6, dampingFraction: 0.7)) {
                    curiosidad = 0
                }
            }
            try? await Task.sleep(for: .seconds(Double.random(in: 2.2...5.5)))
        }
    }
}

/// Un ojo, como lo genera el backend (proporciones 0-1 del cuadro).
struct OjoDeCara: Sendable {
    var x: CGFloat
    var y: CGFloat
    var rx: CGFloat
    var ry: CGFloat
    var rotation: CGFloat

    init(x: CGFloat, y: CGFloat, rx: CGFloat, ry: CGFloat, rotation: CGFloat) {
        self.x = x
        self.y = y
        self.rx = rx
        self.ry = ry
        self.rotation = rotation
    }
}

/// La forma del avatar, parametrizada al `rect` disponible (el tamaño del
/// avatar varía: 22 pt en eventos, 30 en fila, 44 en lista, 96 en el hero).
struct FormaAvatarBot: Shape {
    let nombre: String

    func path(in rect: CGRect) -> Path {
        switch nombre {
        case "rounded_square":
            let inset = rect.width * 0.03
            return Path(
                roundedRect: rect.insetBy(dx: inset, dy: inset),
                cornerRadius: rect.width * 0.22
            )
        case "oval":
            return Path(ellipseIn: rect.insetBy(dx: rect.width * 0.11, dy: rect.height * 0.03))
        case "hexagon":
            return HexagonoAvatarShape().path(in: rect)
        case "squircle":
            let inset = rect.width * 0.05
            return Path(
                roundedRect: rect.insetBy(dx: inset, dy: inset),
                cornerRadius: rect.width * 0.32
            )
        default:
            return Path(ellipseIn: rect)
        }
    }
}

/// Hexágono regular parametrizado al `rect`.
struct HexagonoAvatarShape: Shape {
    func path(in rect: CGRect) -> Path {
        let cx = rect.midX
        let cy = rect.midY
        let r = min(rect.width, rect.height) * 0.5
        var path = Path()
        for i in 0..<6 {
            let angle = CGFloat(i) * .pi / 3 - .pi / 2
            let point = CGPoint(x: cx + r * cos(angle), y: cy + r * sin(angle))
            if i == 0 { path.move(to: point) } else { path.addLine(to: point) }
        }
        path.closeSubpath()
        return path
    }
}

/// Wrapper: mapea el `PersistentWorker` del backend a la cara-orbe.
struct GrokFaceAvatar: View {
    let bot: PersistentWorker
    var size: CGFloat = 40
    var showOnline: Bool = false
    var animado: Bool = true
    var activo: Bool = false

    var body: some View {
        CaraOrbe(
            nombre: bot.nombreVisible,
            formaBot: bot.avatarShape ?? "circle",
            fillHex: bot.avatarFillHex ?? "#6366f1",
            accentHex: bot.avatarAccentHex,
            ojoIzq: bot.avatarEyes.left.map {
                OjoDeCara(x: $0.x, y: $0.y, rx: $0.rx, ry: $0.ry, rotation: $0.rotation)
            },
            ojoDer: bot.avatarEyes.right.map {
                OjoDeCara(x: $0.x, y: $0.y, rx: $0.rx, ry: $0.ry, rotation: $0.rotation)
            },
            size: size,
            showOnline: showOnline,
            animado: animado,
            activo: activo
        )
    }
}

extension Color {
    /// Mezcla lineal hacia `otra` (0 = este color, 1 = la otra).
    func mezclado(con otra: Color, _ t: Double) -> Color {
        guard t > 0 else { return self }
        guard t < 1 else { return otra }
        var r1: CGFloat = 0, g1: CGFloat = 0, b1: CGFloat = 0, a1: CGFloat = 0
        var r2: CGFloat = 0, g2: CGFloat = 0, b2: CGFloat = 0, a2: CGFloat = 0
        UIColor(self).getRed(&r1, green: &g1, blue: &b1, alpha: &a1)
        UIColor(otra).getRed(&r2, green: &g2, blue: &b2, alpha: &a2)
        let k = CGFloat(t)
        return Color(
            red: Double(r1 + (r2 - r1) * k),
            green: Double(g1 + (g2 - g1) * k),
            blue: Double(b1 + (b2 - b1) * k),
            opacity: Double(a1 + (a2 - a1) * k)
        )
    }

    init(hex: String) {
        let cleaned = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var value: UInt64 = 0
        Scanner(string: cleaned).scanHexInt64(&value)
        let r = Double((value >> 16) & 0xFF) / 255
        let g = Double((value >> 8) & 0xFF) / 255
        let b = Double(value & 0xFF) / 255
        self.init(red: r, green: g, blue: b)
    }
}