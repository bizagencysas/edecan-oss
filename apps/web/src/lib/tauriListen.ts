/**
 * Puente con el lado nativo de la app de escritorio (Tauri v2,
 * `apps/desktop/src-tauri/`). Con `app.withGlobalTauri: true` (ya
 * configurado en `tauri.conf.json`), Tauri inyecta `window.__TAURI__` en el
 * webview namespaceado igual que los módulos de `@tauri-apps/api`
 * (`.core.invoke`, `.event.listen`). Se resuelve con el MISMO criterio
 * defensivo que ya usa `apps/desktop/src-tauri/splash/index.html` (líneas
 * ~208-217): por si el shape del global cambia entre versiones de Tauri --
 * preferible degradar con gracia (ver `isTauriApp`) a que un solo nombre mal
 * adivinado tire un `TypeError` y rompa la UI en silencio.
 *
 * A propósito NO se agrega `@tauri-apps/api` como dependencia npm: ya viene
 * inyectado como global cuando esta misma web app corre empaquetada dentro
 * de Tauri. La web app tiene que seguir funcionando standalone en un
 * navegador normal (modo hosted multi-tenant, y así también se prueba en
 * desarrollo) -- todo lo de acá tiene que estar gateado por `isTauriApp()`
 * en el callsite.
 */

type TauriInvokeFn = (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
type TauriUnlistenFn = () => void;
type TauriEventPayload = { payload: unknown };
type TauriListenFn = (
  event: string,
  callback: (event: TauriEventPayload) => void,
) => Promise<TauriUnlistenFn>;

interface TauriGlobalShape {
  core?: { invoke?: TauriInvokeFn };
  invoke?: TauriInvokeFn;
  event?: { listen?: TauriListenFn };
}

function getTauriGlobal(): TauriGlobalShape | null {
  if (typeof window === "undefined" || !("__TAURI__" in window)) return null;
  return (window as unknown as { __TAURI__: TauriGlobalShape }).__TAURI__ ?? null;
}

/** `true` cuando este código corre dentro del webview empaquetado de la app
 * de escritorio (Tauri) -- `false` en el navegador normal (modo hosted
 * multi-tenant, o `next dev` fuera de Tauri). Todo lo que dependa de un
 * comando/evento nativo debe chequear esto antes de usarlo. */
export function isTauriApp(): boolean {
  return typeof window !== "undefined" && "__TAURI__" in window;
}

function resolveInvoke(): TauriInvokeFn | null {
  const tauri = getTauriGlobal();
  if (!tauri) return null;
  return (tauri.core && tauri.core.invoke) || tauri.invoke || null;
}

function resolveListen(): TauriListenFn | null {
  const tauri = getTauriGlobal();
  if (!tauri) return null;
  return (tauri.event && tauri.event.listen) || null;
}

/** Invoca un comando Tauri (`window.__TAURI__.core.invoke`), tipando el
 * resultado como `T`. No debería llamarse nunca sin comprobar `isTauriApp()`
 * antes en el callsite, pero si igual se llama fuera de Tauri falla
 * explícito con un `Error` claro en vez de un `TypeError` opaco. */
export async function tauriInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const invoke = resolveInvoke();
  if (!invoke) {
    throw new Error(
      `tauriInvoke("${cmd}") se llamó fuera de la app de escritorio (Tauri) -- comprobá isTauriApp() antes de invocar.`,
    );
  }
  try {
    return (await invoke(cmd, args)) as T;
  } catch (err) {
    // Un `Err(String)` de Rust rechaza la promesa con el string crudo. Se
    // normaliza a Error para que las pantallas muestren el motivo real.
    if (err instanceof Error) throw err;
    throw new Error(typeof err === "string" ? err : JSON.stringify(err));
  }
}

/** Se suscribe a un evento nativo (`window.__TAURI__.event.listen`) y
 * devuelve la función de "unlisten" que ya provee la API de Tauri -- o un
 * no-op si Tauri no está disponible, para poder usarse directo en un
 * `useEffect` sin gatear cada callsite con `isTauriApp()`. */
export async function tauriListenEvent<T>(
  event: string,
  callback: (payload: T) => void,
): Promise<() => void> {
  const listen = resolveListen();
  if (!listen) return () => undefined;
  return listen(event, (e) => callback(e.payload as T));
}
