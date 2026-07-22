import Foundation
import Observation
import EdecanKit

/// Estado y *polling* de ``RemotoView`` — control remoto tipo TeamViewer del
/// Mac/PC companion (`ARCHITECTURE.md` §13.c/§14, `apps/api/edecan_api/
/// routers/remote.py`, `docs/control-remoto.md`). Sigue el mismo flujo que la
/// referencia ya probada del panel web
/// (`apps/web/src/app/(app)/app/remoto/page.tsx`): *polling* HTTP de frames
/// sueltos (nunca WebRTC/streaming, ver §1.1 de ese documento), doble
/// aprobación (el consentimiento explícito de ``RemotoView`` + la aprobación
/// LOCAL que pide el companion antes del primer frame), un indicador de
/// sesión activa SIEMPRE visible y un botón Terminar SIEMPRE alcanzable —
/// el guardrail no negociable de `DIRECCION_ACTUAL.md` ("Control remoto del
/// Mac/PC desde el móvil": emparejamiento explícito + aprobación humana,
/// nunca un backdoor silencioso).
@MainActor
@Observable
final class RemotoViewModel {
    private(set) var sesion: RemoteSession?
    private(set) var frame: RemoteFrame?

    private(set) var iniciando = false
    /// `true` mientras hay un `GET .../frame` en vuelo — cubre TANTO el
    /// primer pedido (que puede tardar hasta ~30s: el companion espera una
    /// aprobación local real, `docs/control-remoto.md`) como los siguientes.
    private(set) var actualizandoFrame = false
    private(set) var terminando = false
    private(set) var enviandoInput = false
    var errorMensaje: String?

    /// Toggle de "actualizar automático" — ``RemotoView`` lo enlaza a un
    /// `Toggle` y llama ``iniciarPollingFrame(client:)``/``detenerPollingFrame()``
    /// explícitamente en su `onChange` (nada de lógica oculta en un
    /// `didSet`, mismo criterio explícito que el resto de la app).
    var autoActualizar = false

    /// Vista interactiva comprimida: ~2.8 FPS, por encima del límite
    /// server-side de 0.25s y sin solapar solicitudes.
    private let intervaloPollingFrame: Duration = .milliseconds(350)
    private var tareaPollingFrame: Task<Void, Never>?
    /// Cola FIFO de input remoto. Antes, `enviandoInput == true` descartaba
    /// silenciosamente cualquier toque/tecla que llegara mientras el comando
    /// anterior seguía en vuelo. En control remoto eso se siente como clics o
    /// letras perdidas. Cada comando espera al anterior y conserva el orden.
    private var tareaInputAnterior: Task<Void, Never>?

    // MARK: - Iniciar sesión / pedir frame

    /// `kind`: `"view"` o `"control"`. Crea la sesión y de inmediato pide el
    /// primer frame — es lo que dispara la aprobación LOCAL en el companion
    /// (mismo orden que `handleStart` en `apps/web/.../remoto/page.tsx`).
    func iniciar(kind: String, client: APIClient?) async {
        guard let client, !iniciando else { return }
        iniciando = true
        errorMensaje = nil
        defer { iniciando = false }
        do {
            let creada = try await client.createRemoteSession(kind: kind)
            sesion = creada
            frame = nil
            await pedirFrame(client: client)
        } catch APIClient.APIError.servidor(_, let mensaje) {
            errorMensaje = mensaje
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    /// `GET .../frame`. Éxito: guarda el frame y refleja `status="active"` en
    /// ``sesion`` (nunca vuelve a `pending`). `429`: silencioso — el
    /// *polling* automático pisó el intervalo mínimo, se reintenta solo en
    /// el próximo *tick*. `403`/`409`: el servidor ya cambió el estado de la
    /// sesión (denegada/terminada) — se refleja acá y se apaga el *polling*.
    /// Cualquier otro código (501/502/503, típicamente mientras se espera la
    /// aprobación local) deja el mensaje visible para que la persona
    /// reintente a mano desde ``RemotoView``.
    func pedirFrame(client: APIClient?) async {
        guard let client, let sesionActual = sesion else { return }
        actualizandoFrame = true
        defer { actualizandoFrame = false }
        do {
            let nuevoFrame = try await client.getRemoteFrame(sessionId: sesionActual.id)
            frame = nuevoFrame
            errorMensaje = nil
            if sesion?.id == sesionActual.id {
                sesion = sesionActual.conFrame(nuevoFrame)
            }
            if !autoActualizar {
                autoActualizar = true
                iniciarPollingFrame(client: client)
            }
        } catch APIClient.APIError.servidor(let status, let mensaje) {
            if status == 429 { return }
            errorMensaje = mensaje
            if (status == 403 || status == 409), sesion?.id == sesionActual.id {
                detenerPollingFrame()
                autoActualizar = false
                sesion = sesionActual.conEstado(status == 403 ? "denied" : "ended")
            }
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    /// Arranca (o reinicia) el *polling* automático de frames. No hace nada
    /// si no hay sesión — ``RemotoView`` solo lo llama con una ya creada.
    func iniciarPollingFrame(client: APIClient?) {
        detenerPollingFrame()
        guard let client, sesion != nil else { return }
        tareaPollingFrame = Task { [intervaloPollingFrame] in
            while !Task.isCancelled {
                try? await Task.sleep(for: intervaloPollingFrame)
                guard !Task.isCancelled else { return }
                guard self.autoActualizar, let sesion = self.sesion, sesion.sigueViva else { continue }
                await self.pedirFrame(client: client)
            }
        }
    }

    /// Cancela el *polling* automático — llamado por ``RemotoView`` al
    /// apagar el toggle, al terminar la sesión, y desde `onDisappear` (ver
    /// ``limpiar()``). Nunca deja un `Task` huérfano corriendo en segundo
    /// plano.
    func detenerPollingFrame() {
        tareaPollingFrame?.cancel()
        tareaPollingFrame = nil
    }

    // MARK: - Terminar

    /// `POST .../end` — idempotente. Igual que la página web: aunque el
    /// `POST` falle en el servidor (p. ej. sin red), la vista se suelta
    /// LOCAL de todas formas — no tiene sentido dejar a la persona atrapada
    /// en el visor por un error de red al querer salir de una sesión de
    /// control remoto.
    func terminar(client: APIClient?) async {
        guard let client, let sesionActual = sesion, !terminando else { return }
        terminando = true
        errorMensaje = nil
        detenerPollingFrame()
        autoActualizar = false
        defer { terminando = false }
        do {
            _ = try await client.endRemoteSession(id: sesionActual.id)
        } catch {
            errorMensaje = error.localizedDescription
        }
        sesion = nil
        frame = nil
    }

    // MARK: - Input (solo sesiones kind="control" activas)

    /// `POST .../input {tipo: "pointer", ...}`.
    func enviarPointer(_ input: RemotePointerInput, client: APIClient?) async {
        await enviarInput(.pointer(input), client: client)
    }

    /// `POST .../input {tipo: "key", ...}`.
    func enviarKey(_ input: RemoteKeyInput, client: APIClient?) async {
        await enviarInput(.key(input), client: client)
    }

    /// `403`/`409`: el servidor ya cambió el estado de la sesión (denegada/
    /// terminada, o todavía no `active`) — se refleja acá y se apaga el
    /// *polling*, mismo criterio que ``pedirFrame(client:)`` (ver `status`
    /// documentado en `APIClient.sendRemoteInput`).
    private func enviarInput(_ input: RemoteInput, client: APIClient?) async {
        guard let client, let sesionActual = sesion else { return }
        let anterior = tareaInputAnterior
        let actual = Task { @MainActor [weak self] in
            _ = await anterior?.value
            guard let self, self.sesion?.id == sesionActual.id else { return }
            await self.ejecutarInput(input, client: client, sesionActual: sesionActual)
        }
        tareaInputAnterior = actual
        await actual.value
    }

    private func ejecutarInput(
        _ input: RemoteInput, client: APIClient, sesionActual: RemoteSession
    ) async {
        enviandoInput = true
        errorMensaje = nil
        defer { enviandoInput = false }
        do {
            _ = try await client.sendRemoteInput(sessionId: sesionActual.id, input: input)
        } catch APIClient.APIError.servidor(let status, let mensaje) {
            errorMensaje = mensaje
            if (status == 403 || status == 409), sesion?.id == sesionActual.id {
                // 403: el usuario denegó ESTE comando en su companion --
                // deniega la SESIÓN completa (mismo criterio conservador que
                // el backend, ver el docstring de
                // `routers/remote.py::send_input`). 409: la sesión ya no
                // está `active` (terminada del lado del servidor, o todavía
                // no arrancó).
                detenerPollingFrame()
                autoActualizar = false
                sesion = sesionActual.conEstado(status == 403 ? "denied" : "ended")
            }
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    // MARK: - Limpieza

    /// Llamado desde `onDisappear` de ``RemotoView`` — nunca deja *polling*
    /// huérfano corriendo en segundo plano. NO termina la sesión (mismo
    /// criterio que la página web: navegar fuera de la pantalla no cierra
    /// una sesión remota por sí solo, eso exige el botón "Terminar" a
    /// propósito).
    func limpiar() {
        detenerPollingFrame()
        tareaInputAnterior?.cancel()
        tareaInputAnterior = nil
    }
}
