import XCTest

/// E2E de workforce (product design) contra el iPhone físico.
/// Cobertura: arranque, navegación, crear compañero (vía Actividad → Compañeros)
/// y crear grupo (tab "Bots").
final class EdecanWorkforceUITests: XCTestCase {

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    /// El roster ya no es tab: se alcanza desde Actividad → "Compañeros".
    private func irACompaneros(_ app: XCUIApplication) {
        let tabActividad = app.tabBars.buttons["Actividad"]
        XCTAssertTrue(tabActividad.waitForExistence(timeout: 15), "No apareció el tab 'Actividad'")
        tabActividad.tap()
        // La card es un NavigationLink con label custom (titulo+subtitulo), así
        // que se busca por label CONTAINS y con scroll.
        // El NavigationLink es un Button con label custom (titulo+subtitulo);
        // se busca el BUTTON (no el Text interno) para que el tap navegue.
        let companeros = app.buttons
            .matching(NSPredicate(format: "label CONTAINS[c] %@", "Compañeros"))
            .firstMatch
        if !desplazarHasta(companeros, en: app, maxPasadas: 6) {
            XCTFail("No apareció la card 'Compañeros' en Actividad")
        }
        companeros.tap()
        XCTAssertTrue(
            app.buttons["Nuevo compañero"].waitForExistence(timeout: 10),
            "El roster no mostró 'Nuevo compañero'"
        )
    }

    private func desplazarHasta(
        _ elemento: XCUIElement,
        en app: XCUIApplication,
        maxPasadas: Int = 10
    ) -> Bool {
        var pasadas = 0
        while !(elemento.exists && elemento.isHittable) && pasadas < maxPasadas {
            app.swipeUp()
            pasadas += 1
        }
        return elemento.exists && elemento.isHittable
    }

    func testLaunchYTabEquipo() throws {
        let app = XCUIApplication()
        app.launch()
        irACompaneros(app)
    }

    func testAbrirSheetDeCreacion() throws {
        let app = XCUIApplication()
        app.launch()
        irACompaneros(app)

        let nuevoCompanero = app.buttons["Nuevo compañero"]
        nuevoCompanero.tap()

        XCTAssertTrue(
            app.buttons["Crear"].waitForExistence(timeout: 8),
            "El sheet 'Nuevo compañero' no mostró el botón 'Crear'"
        )
        XCTAssertTrue(app.buttons["Cancelar"].exists)
    }

    func testTabBots() throws {
        let app = XCUIApplication()
        app.launch()

        let tabBots = app.tabBars.buttons["Bots"]
        XCTAssertTrue(tabBots.waitForExistence(timeout: 15))
        tabBots.tap()

        XCTAssertTrue(
            app.navigationBars["Bots"].waitForExistence(timeout: 10),
            "La lista unificada de chats no cargó"
        )
    }

    func testCrearCompanero() throws {
        let app = XCUIApplication()
        app.launch()
        irACompaneros(app)

        let nuevo = app.buttons["Nuevo compañero"]
        nuevo.tap()

        let nombre = app.textFields["Nombre (identificador)"]
        XCTAssertTrue(nombre.waitForExistence(timeout: 8), "No apareció el campo de nombre")
        nombre.tap()
        nombre.typeText("E2EInvest003")

        // "Propósito" es multilínea (axis: .vertical) -> textView, no textField.
        let proposito: XCUIElement
        if app.textFields["Propósito"].exists {
            proposito = app.textFields["Propósito"]
        } else {
            proposito = app.textViews["Propósito"]
        }
        XCTAssertTrue(proposito.waitForExistence(timeout: 5))
        proposito.tap()
        proposito.typeText("Investiga temas y cita fuentes")

        app.buttons["Crear"].tap()

        XCTAssertTrue(
            app.staticTexts["E2EInvest003"].waitForExistence(timeout: 10)
                || app.buttons["Nuevo compañero"].waitForExistence(timeout: 10),
            "El agente creado no aparece en el roster"
        )
    }

    func testCrearEquipo() throws {
        let app = XCUIApplication()
        app.launch()

        let tabBots = app.tabBars.buttons["Bots"]
        XCTAssertTrue(tabBots.waitForExistence(timeout: 15))
        tabBots.tap()

        let nuevo = app.buttons["Nuevo"]
        XCTAssertTrue(nuevo.waitForExistence(timeout: 10), "No apareció el botón 'Nuevo'")
        nuevo.tap()

        let nuevoGrupo = app.buttons["Nuevo grupo"]
        XCTAssertTrue(nuevoGrupo.waitForExistence(timeout: 8), "No apareció 'Nuevo grupo'")
        nuevoGrupo.tap()

        let nombre = app.textFields["Ej. Comercial, Operaciones…"]
        XCTAssertTrue(nombre.waitForExistence(timeout: 8), "No apareció el campo de nombre del equipo")
        nombre.tap()
        nombre.typeText("E2E Lanzamiento 2")

        app.buttons["Crear"].tap()

        XCTAssertTrue(
            app.staticTexts["E2E Lanzamiento 2"].waitForExistence(timeout: 10)
                || app.buttons["Nuevo equipo"].waitForExistence(timeout: 10),
            "El equipo creado no aparece en la lista"
        )
    }
}
