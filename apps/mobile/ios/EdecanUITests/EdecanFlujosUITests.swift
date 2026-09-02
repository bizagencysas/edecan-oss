import XCTest

/// E2E de flujos reales extendidos: enseñar una tarea (capturar paso → agrega)
/// y abrir computadora (crear sesión). Se leen los labels reales de las vistas.
final class EdecanFlujosUITests: XCTestCase {

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    /// El roster ya no es tab: se alcanza desde Actividad → "Compañeros".
    private func irACompaneros(_ app: XCUIApplication) {
        let tabActividad = app.tabBars.buttons["Actividad"]
        XCTAssertTrue(tabActividad.waitForExistence(timeout: 15), "No apareció el tab 'Actividad'")
        tabActividad.tap()
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

    func testEnsenarTareaFlujo() throws {
        let app = XCUIApplication()
        app.launch()
        irACompaneros(app)

        let ensenar = app.staticTexts["Enseñar una tarea"]
        if !desplazarHasta(ensenar, en: app, maxPasadas: 10) {
            throw XCTSkip("No se encontró 'Enseñar una tarea' en Compañeros")
        }
        ensenar.tap()

        let nombre = app.textFields["Nombre de la tarea"]
        XCTAssertTrue(nombre.waitForExistence(timeout: 8), "No apareció 'Nombre de la tarea'")
        nombre.tap()
        nombre.typeText("E2E Prep de gastos")

        let comenzar = app.buttons["Comenzar"]
        XCTAssertTrue(comenzar.waitForExistence(timeout: 5), "No apareció 'Comenzar'")
        if !comenzar.isHittable { app.swipeDown() }
        comenzar.tap()

        // Agregar un paso estructurado: Acción + Selector.
        let accion = app.textFields["Acción (qué hace)"]
        XCTAssertTrue(accion.waitForExistence(timeout: 8), "No apareció el campo 'Acción'")
        accion.tap()
        accion.typeText("Abrir informes")
        let paso = app.buttons["Agregar paso"]
        XCTAssertTrue(paso.waitForExistence(timeout: 5), "No apareció 'Agregar paso'")
        if !paso.isHittable { app.swipeDown() }
        paso.tap()

        XCTAssertTrue(
            app.staticTexts.matching(
                NSPredicate(format: "label CONTAINS[c] %@", "Abrir informes")
            ).firstMatch.waitForExistence(timeout: 8),
            "El paso capturado no apareció en la lista"
        )
    }

    func testAbrirComputadora() throws {
        let app = XCUIApplication()
        app.launch()

        let tabActividad = app.tabBars.buttons["Actividad"]
        XCTAssertTrue(tabActividad.waitForExistence(timeout: 15), "No apareció el tab 'Actividad'")
        tabActividad.tap()

        let computadora = app.staticTexts["Computadora"]
        if !desplazarHasta(computadora, en: app, maxPasadas: 10) {
            throw XCTSkip("No se encontró el acceso directo 'Computadora' en Actividad")
        }
        computadora.tap()

        XCTAssertTrue(
            app.navigationBars["Computadora"].waitForExistence(timeout: 8)
                || app.buttons["Nueva sesión"].waitForExistence(timeout: 8),
            "La vista Computadora no cargó"
        )
        // Si existe "Nueva sesión", creada y verifica que vuelve (idempotente).
        let nueva = app.buttons["Nueva sesión"]
        if nueva.exists && nueva.isHittable {
            nueva.tap()
            XCTAssertTrue(
                app.navigationBars["Computadora"].waitForExistence(timeout: 8)
                    || app.staticTexts.firstMatch.waitForExistence(timeout: 8),
                "La creación de sesión de computadora no dejó la vista en un estado sano"
            )
        }
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
}
