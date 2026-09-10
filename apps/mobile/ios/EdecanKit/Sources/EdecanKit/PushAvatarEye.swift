import CoreGraphics

/// Un ojo del avatar push (proporciones 0–1 del cuadro), como en el payload o el generador.
public struct PushAvatarEye: Sendable, Equatable {
    public let x: CGFloat
    public let y: CGFloat
    public let rx: CGFloat
    public let ry: CGFloat
    public let rotation: CGFloat

    public init(x: CGFloat, y: CGFloat, rx: CGFloat, ry: CGFloat, rotation: CGFloat) {
        self.x = x
        self.y = y
        self.rx = rx
        self.ry = ry
        self.rotation = rotation
    }
}
