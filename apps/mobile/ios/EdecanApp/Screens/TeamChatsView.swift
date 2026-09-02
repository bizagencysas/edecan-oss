import SwiftUI
import EdecanKit

/// Los chats de grupo viven en la misma lista que los bots 1:1 (``BotsChatsView``).
struct TeamChatsView: View {
    var body: some View {
        BotsChatsView()
    }
}

extension APIClient.APIError {
    /// `true` cuando el error es "esta ruta todavía no aterrizó" (404/501) —
    /// el caso de los contratos en paralelo. La app muestra "Próximamente" en
    /// vez de un error rojo y nunca finge éxito (directiva §153).
    var esProximamente: Bool {
        if case .servidor(let status, _) = self {
            return status == 404 || status == 501
        }
        return false
    }
}
