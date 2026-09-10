#if canImport(UIKit)
import UIKit

/// Render estático de la cara Grok del bot para Communication Notifications.
///
/// **Layout lock (paridad Grok Bot):**
/// - **Grande:** cara del bot que habla (forma/color del worker; varía por bot).
/// - **Pequeño abajo-derecha:** badge AppIcon Edecán (~25 %, aro blanco) vía `compositePNG`.
/// **Nunca invertir:** logo solo como remitente grande ni cara del bot solo como badge.
public enum BotPushAvatarRenderer {
    public static let tamanoPredeterminado: CGFloat = 128
    /// Marca visible en Communication Notifications (como «Grok» en su app).
    public static let serviceDisplayName = "Edecán"

    /// Escala del badge AppIcon sobre la cara (22–28 % del lado).
    public static let badgeScale: CGFloat = 0.25
    /// Inset del badge respecto al borde inferior-derecho (fracción del lado).
    public static let badgeInsetFraction: CGFloat = 0.04

    /// Imagen del remitente para Communication Notifications: cara Grok a tamaño
    /// completo con badge AppIcon en la esquina inferior derecha (estilo iMessage/Grok).
    /// Nunca usa el logo como imagen grande cuando puede renderizar una cara.
    public static func senderPNGData(
        avatarFromPayload: Bool,
        shape: String,
        fillHex: String,
        accentHex: String?,
        displayName: String,
        leftEye: PushAvatarEye? = nil,
        rightEye: PushAvatarEye? = nil,
        size: CGFloat = tamanoPredeterminado
    ) -> Data? {
        _ = avatarFromPayload
        guard size > 0 else { return nil }
        guard let face = faceUIImage(
            shape: shape,
            fillHex: fillHex,
            accentHex: accentHex,
            displayName: displayName,
            leftEye: leftEye,
            rightEye: rightEye,
            size: size
        ) else {
            return nil
        }
        return compositePNG(face: face, size: size)
    }

    /// Ojos deterministas cuando el push no trae `avatar_eyes` (espeja avatars.py / GrokFaceAvatar).
    public static func fallbackEyes(seed: String) -> (PushAvatarEye, PushAvatarEye) {
        let suma = seed.unicodeScalars.reduce(0) { $0 &+ Int($1.value) }
        let indice = Double(suma % 100) / 100.0
        let rotacion = CGFloat((indice - 0.5) * 24)
        let y = CGFloat(0.40 + (indice - 0.5) * 0.03)
        let izq = PushAvatarEye(x: 0.34, y: y, rx: 0.058, ry: 0.078, rotation: rotacion)
        let der = PushAvatarEye(x: 0.66, y: y, rx: 0.058, ry: 0.078, rotation: rotacion)
        return (izq, der)
    }

    /// Logo de la app host (AppIcon) escalado, sin composición.
    public static func appLogoPNGData(size: CGFloat = tamanoPredeterminado) -> Data? {
        guard size > 0, let image = hostAppIconImage() else { return nil }
        let rect = CGRect(x: 0, y: 0, width: size, height: size)
        let renderer = UIGraphicsImageRenderer(size: rect.size)
        let scaled = renderer.image { _ in
            image.draw(in: rect)
        }
        return scaled.pngData()
    }

    /// PNG con fondo transparente; `nil` si los parámetros son inválidos.
    public static func pngData(
        shape: String,
        fillHex: String,
        accentHex: String?,
        displayName: String,
        leftEye: PushAvatarEye? = nil,
        rightEye: PushAvatarEye? = nil,
        size: CGFloat = tamanoPredeterminado
    ) -> Data? {
        faceUIImage(
            shape: shape,
            fillHex: fillHex,
            accentHex: accentHex,
            displayName: displayName,
            leftEye: leftEye,
            rightEye: rightEye,
            size: size
        )?.pngData()
    }

    /// Composición cara + badge; expuesto para pruebas.
    public static func compositePNG(face: UIImage, size: CGFloat = tamanoPredeterminado) -> Data? {
        guard size > 0 else { return nil }
        let rect = CGRect(x: 0, y: 0, width: size, height: size)
        let renderer = UIGraphicsImageRenderer(size: rect.size)
        let image = renderer.image { _ in
            face.draw(in: rect)
            if let logo = hostAppIconImage() {
                dibujarBadge(logo: logo, en: rect)
            }
        }
        return image.pngData()
    }

    private static func faceUIImage(
        shape: String,
        fillHex: String,
        accentHex: String?,
        displayName: String,
        leftEye: PushAvatarEye?,
        rightEye: PushAvatarEye?,
        size: CGFloat
    ) -> UIImage? {
        guard size > 0 else { return nil }
        let fill = uiColor(hex: fillHex) ?? UIColor(red: 0.39, green: 0.40, blue: 0.95, alpha: 1)
        let rect = CGRect(x: 0, y: 0, width: size, height: size)
        let ojos = resolvedEyes(left: leftEye, right: rightEye, seed: displayName)
        let renderer = UIGraphicsImageRenderer(size: rect.size)
        return renderer.image { ctx in
            let cg = ctx.cgContext
            cg.clear(rect)

            let path = forma(shape: shape, in: rect)
            cg.saveGState()
            path.addClip()

            let top = fill.mezclado(con: .white, t: 0.14)
            let colors = [top.cgColor, fill.cgColor] as CFArray
            if let gradient = CGGradient(
                colorsSpace: CGColorSpaceCreateDeviceRGB(),
                colors: colors,
                locations: [0, 1]
            ) {
                cg.drawLinearGradient(
                    gradient,
                    start: CGPoint(x: rect.midX, y: rect.minY),
                    end: CGPoint(x: rect.midX, y: rect.maxY),
                    options: []
                )
            } else {
                cg.setFillColor(fill.cgColor)
                cg.addPath(path.cgPath)
                cg.fillPath()
            }
            cg.restoreGState()

            cg.setStrokeColor(UIColor.white.withAlphaComponent(0.18).cgColor)
            cg.setLineWidth(max(size * 0.02, 0.7))
            cg.addPath(path.cgPath)
            cg.strokePath()

            dibujarOjos(en: rect, fill: fill, left: ojos.left, right: ojos.right)
        }
    }

    private static func resolvedEyes(
        left: PushAvatarEye?,
        right: PushAvatarEye?,
        seed: String
    ) -> (left: PushAvatarEye, right: PushAvatarEye) {
        if left != nil || right != nil {
            let fallback = fallbackEyes(seed: seed)
            return (left ?? fallback.0, right ?? fallback.1)
        }
        return fallbackEyes(seed: seed)
    }

    private static func dibujarBadge(logo: UIImage, en rect: CGRect) {
        let lado = rect.width
        let badgeSize = lado * badgeScale
        let inset = lado * badgeInsetFraction
        let badgeRect = CGRect(
            x: rect.maxX - badgeSize - inset,
            y: rect.maxY - badgeSize - inset,
            width: badgeSize,
            height: badgeSize
        )
        let ringWidth = max(badgeSize * 0.08, 1.5)
        let outerRect = badgeRect.insetBy(dx: -ringWidth * 0.35, dy: -ringWidth * 0.35)

        UIColor.white.withAlphaComponent(0.95).setFill()
        UIBezierPath(ovalIn: outerRect).fill()

        UIColor.white.withAlphaComponent(0.55).setStroke()
        let ring = UIBezierPath(ovalIn: outerRect)
        ring.lineWidth = ringWidth
        ring.stroke()

        let clipPath = UIBezierPath(roundedRect: badgeRect, cornerRadius: badgeSize * 0.22)
        let ctx = UIGraphicsGetCurrentContext()
        ctx?.saveGState()
        clipPath.addClip()
        logo.draw(in: badgeRect)
        ctx?.restoreGState()
    }

    private static func dibujarOjos(
        en rect: CGRect,
        fill: UIColor,
        left: PushAvatarEye,
        right: PushAvatarEye
    ) {
        dibujarOjo(left, en: rect, fill: fill)
        dibujarOjo(right, en: rect, fill: fill)
    }

    private static func dibujarOjo(_ eye: PushAvatarEye, en rect: CGRect, fill: UIColor) {
        let size = rect.width
        let ancho = size * eye.rx * 2 * 1.22
        let alto = size * eye.ry * 2 * 1.22
        var t = CGAffineTransform(translationX: size * eye.x, y: size * eye.y)
            .rotated(by: eye.rotation * .pi / 180)
            .translatedBy(x: -ancho / 2, y: -alto / 2)
        let ojoRect = CGRect(x: 0, y: 0, width: ancho, height: alto).applying(t)
        UIColor.white.setFill()
        UIBezierPath(ovalIn: ojoRect).fill()
        let pupila = CGRect(
            x: ojoRect.midX - ancho * 0.19,
            y: ojoRect.midY - alto * 0.12,
            width: ancho * 0.38,
            height: max(alto * 0.5, 1.2)
        )
        fill.mezclado(con: .black, t: 0.45).setFill()
        UIBezierPath(ovalIn: pupila).fill()
    }

    private static func forma(shape: String, in rect: CGRect) -> UIBezierPath {
        switch shape {
        case "rounded_square":
            let inset = rect.width * 0.03
            return UIBezierPath(
                roundedRect: rect.insetBy(dx: inset, dy: inset),
                cornerRadius: rect.width * 0.22
            )
        case "oval":
            return UIBezierPath(
                ovalIn: rect.insetBy(dx: rect.width * 0.11, dy: rect.height * 0.03)
            )
        case "hexagon":
            return hexagono(en: rect)
        case "squircle":
            let inset = rect.width * 0.05
            return UIBezierPath(
                roundedRect: rect.insetBy(dx: inset, dy: inset),
                cornerRadius: rect.width * 0.32
            )
        default:
            return UIBezierPath(ovalIn: rect)
        }
    }

    private static func hexagono(en rect: CGRect) -> UIBezierPath {
        let cx = rect.midX
        let cy = rect.midY
        let r = min(rect.width, rect.height) * 0.5
        let path = UIBezierPath()
        for i in 0..<6 {
            let angle = CGFloat(i) * .pi / 3 - .pi / 2
            let point = CGPoint(x: cx + r * cos(angle), y: cy + r * sin(angle))
            if i == 0 { path.move(to: point) } else { path.addLine(to: point) }
        }
        path.close()
        return path
    }

    private static var hostAppBundle: Bundle {
        var bundle = Bundle.main
        if bundle.bundleURL.pathExtension == "appex" {
            let hostURL = bundle.bundleURL
                .deletingLastPathComponent()
                .deletingLastPathComponent()
            if let host = Bundle(url: hostURL) {
                bundle = host
            }
        }
        return bundle
    }

    private static func hostAppIconImage() -> UIImage? {
        let bundle = hostAppBundle
        if let icons = bundle.infoDictionary?["CFBundleIcons"] as? [String: Any],
           let primary = icons["CFBundlePrimaryIcon"] as? [String: Any],
           let files = primary["CFBundleIconFiles"] as? [String] {
            for name in files.reversed() {
                if let image = UIImage(named: name, in: bundle, compatibleWith: nil) {
                    return image
                }
            }
        }
        return UIImage(named: "AppIcon", in: bundle, compatibleWith: nil)
    }

    private static func uiColor(hex: String) -> UIColor? {
        var texto = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        if texto.hasPrefix("#") { texto.removeFirst() }
        if texto.count == 3 {
            texto = texto.map { String(repeating: $0, count: 2) }.joined()
        }
        guard texto.count == 6, let value = UInt64(texto, radix: 16) else { return nil }
        let r = CGFloat((value >> 16) & 0xFF) / 255
        let g = CGFloat((value >> 8) & 0xFF) / 255
        let b = CGFloat(value & 0xFF) / 255
        return UIColor(red: r, green: g, blue: b, alpha: 1)
    }
}

private extension UIColor {
    func mezclado(con otra: UIColor, t: CGFloat) -> UIColor {
        var r1: CGFloat = 0, g1: CGFloat = 0, b1: CGFloat = 0, a1: CGFloat = 0
        var r2: CGFloat = 0, g2: CGFloat = 0, b2: CGFloat = 0, a2: CGFloat = 0
        getRed(&r1, green: &g1, blue: &b1, alpha: &a1)
        otra.getRed(&r2, green: &g2, blue: &b2, alpha: &a2)
        return UIColor(
            red: r1 + (r2 - r1) * t,
            green: g1 + (g2 - g1) * t,
            blue: b1 + (b2 - b1) * t,
            alpha: a1 + (a2 - a1) * t
        )
    }
}
#endif
