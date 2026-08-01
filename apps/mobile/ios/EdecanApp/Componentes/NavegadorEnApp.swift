import EdecanKit
import SafariServices
import SwiftUI

/// Navegador real dentro de la app, para URLs que el usuario decide abrir.
///
/// Por qué `SFSafariViewController` y no el `WKWebView` de ``SecurePreview``:
/// ese está capado a propósito (`allowsContentJavaScript = false`) porque su
/// trabajo es previsualizar **artefactos** — contenido que puede venir de
/// terceros y que no debe ejecutar nada. Esa dureza es correcta ahí, pero como
/// navegador no sirve: cualquier página moderna sale rota o en blanco.
///
/// `SFSafariViewController` resuelve las dos cosas a la vez, y de hecho aísla
/// MÁS que un `WKWebView` embebido:
///
/// - Corre **fuera del proceso** de la app. Edecán no puede leer el contenido
///   de la página, ni sus cookies, ni inyectarle JavaScript. Un `WKWebView`
///   propio sí podría, así que esto es menos superficie, no más.
/// - Trae su propia barra con dominio visible, recargar, compartir y "Listo",
///   así que el usuario siempre sabe en qué sitio está parado.
/// - Renderiza como Safari de verdad: con JavaScript, sesiones y todo.
///
/// Lo que SÍ se pierde respecto al visor capado es la lista de reglas que
/// bloqueaba subrecursos hacia la red local. No hay forma de instalarla en
/// `SFSafariViewController` (justamente porque es otro proceso). El filtro de
/// destino sigue puesto igual — ``ChatAction.httpURLSegura`` rechaza
/// `localhost`, `.local`, `.internal` y todo el rango privado/reservado antes
/// de abrir nada — así que no se puede navegar A la red local; el riesgo
/// residual es el mismo que abrir ese enlace en Safari, que es exactamente lo
/// que el usuario está pidiendo hacer.
struct NavegadorEnApp: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        let configuration = SFSafariViewController.Configuration()
        // Sin precarga: abrir el navegador no debe disparar tráfico hacia un
        // sitio que el usuario todavía no decidió visitar.
        configuration.entersReaderIfAvailable = false
        let controller = SFSafariViewController(url: url, configuration: configuration)
        controller.dismissButtonStyle = .done
        controller.preferredControlTintColor = UIColor(EdecanTheme.morado)
        return controller
    }

    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}
}

/// Destino de navegación web abierto desde el chat.
///
/// `Identifiable` para poder presentarlo con `.sheet(item:)`, igual que
/// ``SecurePreviewTarget``.
struct DestinoNavegador: Identifiable {
    let url: URL

    var id: String { url.absoluteString }

    /// Construye el destino solo si la URL pasa el filtro de hosts públicos.
    ///
    /// Devuelve `nil` para cualquier cosa que apunte a la red local o a un
    /// esquema que no sea http/https: es la misma frontera que ya aplicaba el
    /// visor seguro, y se comprueba ANTES de abrir, no dentro del navegador.
    init?(_ rawValue: String) {
        guard let segura = ChatAction.httpURLSegura(rawValue) else { return nil }
        self.url = segura
    }
}
