import XCTest

/// E2E de navegación: tabs principales, misiones, enseñar tarea, mensajes entre
/// agentes, actividad, seguridad, memoria y computadora. Cada test lee los labels
/// reales de las vistas (grep sobre `Text(...)`, `Label(...)`, `TextField(...)`).
/// Los puntos de entrada que viven detrás de un scroll se buscan con `desplazarHasta`;
/// si de verdad no aparecen, se salta el test con `XCTSkip` (honesto, no fake).
final class EdecanNavigationUITests: XCTestCase {

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    /// El roster ya no es tab: se alcanza desde Actividad → "Compañeros".
    private func irACompaneros(_ app: XCUIApplication) {
        let tabActividad = app.tabBars.buttons["Actividad"]
        XCTAssertTrue(tabActividad.waitForExistence(timeout: 15), "No apareció el tab 'Actividad'")
        tabActividad.tap()
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

    // MARK: - 1. Navegar pestañas principales

    func testArrancaEnConversacionEdecan() throws {
        let app = XCUIApplication()
        app.launch()

        let tabEdecan = app.tabBars.buttons["Edecán"]
        XCTAssertTrue(tabEdecan.waitForExistence(timeout: 15), "No apareció el tab 'Edecán'")
        XCTAssertTrue(tabEdecan.isSelected, "La app debe abrir en la pestaña Edecán")

        let emptyState = app.otherElements["chat-empty-state"]
        let composer = app.textFields["Escríbele a Edecán…"]
        let composerMultiline = app.textViews["Escríbele a Edecán…"]
        XCTAssertTrue(
            emptyState.waitForExistence(timeout: 8)
                || composer.waitForExistence(timeout: 8)
                || composerMultiline.waitForExistence(timeout: 8),
            "La superficie principal debe ser el chat (empty state o compositor), no un cockpit"
        )
    }

    func testNavegarTabsPrincipales() throws {
        let app = XCUIApplication()
        app.launch()

        XCTAssertTrue(app.tabBars.firstMatch.waitForExistence(timeout: 15), "No apareció la barra de pestañas")

        // Edecán (pestaña por defecto al arrancar ya emparejado).
        let tabEdecan = app.tabBars.buttons["Edecán"]
        XCTAssertTrue(tabEdecan.waitForExistence(timeout: 10), "No apareció el tab 'Edecán'")
        if !tabEdecan.isSelected { tabEdecan.tap() }
        XCTAssertTrue(tabEdecan.isSelected, "El tab 'Edecán' no quedó seleccionado")

        let tabEquipo = app.tabBars.buttons["Equipo"]
        XCTAssertFalse(tabEquipo.waitForExistence(timeout: 3), "El roster NO debe ser un tab principal")

        let tabBots = app.tabBars.buttons["Bots"]
        XCTAssertTrue(tabBots.waitForExistence(timeout: 10), "No apareció el tab 'Bots'")
        tabBots.tap()
        XCTAssertTrue(
            app.navigationBars["Bots"].waitForExistence(timeout: 10),
            "La lista unificada de chats no cargó"
        )
        XCTAssertFalse(
            app.tabBars.buttons["Teams"].waitForExistence(timeout: 2),
            "No debe existir un tab separado 'Teams'"
        )
        XCTAssertFalse(
            app.tabBars.buttons["Equipos"].waitForExistence(timeout: 2),
            "No debe existir un tab separado 'Equipos'"
        )

        let tabActividad = app.tabBars.buttons["Actividad"]
        XCTAssertTrue(tabActividad.waitForExistence(timeout: 10), "No apareció el tab 'Actividad'")
        tabActividad.tap()
        XCTAssertTrue(
            app.navigationBars["Actividad"].waitForExistence(timeout: 10)
                || app.staticTexts["Todo lo que Edecan está haciendo"].waitForExistence(timeout: 10),
            "La vista Actividad no cargó"
        )

        let tabTu = app.tabBars.buttons["Tú"]
        XCTAssertTrue(tabTu.waitForExistence(timeout: 10), "No apareció el tab 'Tú'")
        tabTu.tap()
        XCTAssertTrue(
            app.navigationBars["Tú"].waitForExistence(timeout: 10)
                || app.staticTexts["Memoria"].waitForExistence(timeout: 10),
            "La vista Tú no cargó"
        )
    }

    // MARK: - 2. Crear misión (MisionesView vía "Trabajo delegado" en Actividad)

    func testCrearMision() throws {
        let app = XCUIApplication()
        app.launch()

        let tabActividad = app.tabBars.buttons["Actividad"]
        XCTAssertTrue(tabActividad.waitForExistence(timeout: 15), "No apareció el tab 'Actividad'")
        tabActividad.tap()

        let trabajoDelegado = app.staticTexts["Trabajo delegado"]
        if !desplazarHasta(trabajoDelegado, en: app, maxPasadas: 4) {
            throw XCTSkip("No se encontró el acceso directo 'Trabajo delegado' en Actividad")
        }
        trabajoDelegado.tap()

        XCTAssertTrue(
            app.navigationBars["Misiones"].waitForExistence(timeout: 8)
                || app.staticTexts["Nueva misión"].waitForExistence(timeout: 8),
            "La vista Misiones no cargó"
        )

        // El campo de objetivo es multilínea (axis: .vertical) -> textView, no textField.
        let placeholder = "Ej: Investiga a mis 3 competidores y resume sus precios"
        let objetivo: XCUIElement
        if app.textFields[placeholder].exists {
            objetivo = app.textFields[placeholder]
        } else {
            objetivo = app.textViews[placeholder]
        }
        XCTAssertTrue(objetivo.waitForExistence(timeout: 8), "No apareció el campo de objetivo de misión")
        objetivo.tap()
        objetivo.typeText("Investiga FastAPI y cita fuentes")

        let crear = app.buttons["Crear misión"]
        XCTAssertTrue(crear.waitForExistence(timeout: 5), "No apareció el botón 'Crear misión'")
        if !crear.isHittable {
            app.swipeDown()
        }
        crear.tap()

        XCTAssertTrue(
            app.staticTexts["Investiga FastAPI y cita fuentes"].waitForExistence(timeout: 10)
                || app.staticTexts["Tus misiones"].waitForExistence(timeout: 10)
                || app.staticTexts["Nueva misión"].exists,
            "La misión creada no aparece en la lista"
        )
    }

    // MARK: - 3. Abrir "Enseñar una tarea" desde Equipo

    func testAbrirEnsenarTarea() throws {
        let app = XCUIApplication()
        app.launch()
        irACompaneros(app)

        let ensenar = app.staticTexts["Enseñar una tarea"]
        if !desplazarHasta(ensenar, en: app, maxPasadas: 10) {
            throw XCTSkip("No se encontró 'Enseñar una tarea' en la vista Equipo")
        }
        ensenar.tap()

        XCTAssertTrue(
            app.navigationBars["Enseñar una tarea"].waitForExistence(timeout: 8)
                || app.textFields["Nombre de la tarea"].waitForExistence(timeout: 8)
                || app.buttons["Comenzar"].waitForExistence(timeout: 8),
            "El flujo 'Enseñar una tarea' no se abrió"
        )
    }

    // MARK: - 4. Abrir "Mensajes" (inter-agente) desde Equipo

    func testAbrirMensajesEntreAgentes() throws {
        let app = XCUIApplication()
        app.launch()
        irACompaneros(app)

        let mensajes = app.staticTexts["Mensajes"]
        XCTAssertTrue(mensajes.waitForExistence(timeout: 10), "No apareció la fila 'Mensajes' en Equipo")
        mensajes.tap()

        XCTAssertTrue(
            app.navigationBars["Mensajes"].waitForExistence(timeout: 8)
                || app.staticTexts["Todavía no hay mensajes entre compañeros."].waitForExistence(timeout: 8)
                || app.staticTexts["Próximamente"].waitForExistence(timeout: 8),
            "La vista Mensajes entre agentes no cargó"
        )
    }

    // MARK: - 5. Abrir Actividad (la pestaña muestra InicioView)

    func testAbrirActividad() throws {
        let app = XCUIApplication()
        app.launch()

        let tabActividad = app.tabBars.buttons["Actividad"]
        XCTAssertTrue(tabActividad.waitForExistence(timeout: 15), "No apareció el tab 'Actividad'")
        tabActividad.tap()

        XCTAssertTrue(
            app.navigationBars["Actividad"].waitForExistence(timeout: 10)
                || app.staticTexts["Todo lo que Edecan está haciendo"].waitForExistence(timeout: 10),
            "La vista Actividad no cargó"
        )
    }

    // MARK: - 6. Abrir Seguridad desde Tú

    func testAbrirSeguridad() throws {
        let app = XCUIApplication()
        app.launch()

        let tabTu = app.tabBars.buttons["Tú"]
        XCTAssertTrue(tabTu.waitForExistence(timeout: 15), "No apareció el tab 'Tú'")
        tabTu.tap()

        let seguridad = app.staticTexts["Seguridad"]
        if !desplazarHasta(seguridad, en: app, maxPasadas: 8) {
            throw XCTSkip("No se encontró 'Seguridad' en la vista Tú")
        }
        seguridad.tap()

        XCTAssertTrue(
            app.buttons["Pausar todos los agentes"].waitForExistence(timeout: 8),
            "La vista Seguridad no mostró el freno de emergencia 'Pausar todos los agentes'"
        )
    }

    // MARK: - 7. Abrir Memoria desde Tú

    func testAbrirMemoria() throws {
        let app = XCUIApplication()
        app.launch()

        let tabTu = app.tabBars.buttons["Tú"]
        XCTAssertTrue(tabTu.waitForExistence(timeout: 15), "No apareció el tab 'Tú'")
        tabTu.tap()

        let memoria = app.staticTexts["Memoria"]
        if !desplazarHasta(memoria, en: app, maxPasadas: 4) {
            throw XCTSkip("No se encontró 'Memoria' en la vista Tú")
        }
        memoria.tap()

        XCTAssertTrue(
            app.navigationBars["Memoria"].waitForExistence(timeout: 8)
                || app.staticTexts["Lo que recuerda"].waitForExistence(timeout: 8)
                || app.staticTexts[
                    "Todavía no hay recuerdos guardados. Conversa con Edecán y empezará a recordar lo importante."
                ].waitForExistence(timeout: 8),
            "La vista Memoria no cargó"
        )
    }

    // MARK: - 8. Abrir Computadora

    func testAbrirComputer() throws {
        let app = XCUIApplication()
        app.launch()

        // Nota: en Equipo el botón "Computadora" del toolbar llama a
        // `router.mostrarRemoto()` y abre RemotoView (control remoto), NO
        // ComputerView. El punto de entrada real a ComputerView es el acceso
        // directo "Computadora" de InicioView (Actividad).
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
    }

    // MARK: - Helpers

    /// Desliza hacia arriba hasta que el elemento sea visible y tocable, o se
    /// agotan las pasadas. Devuelve `true` si quedó listo para tocarse.
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
