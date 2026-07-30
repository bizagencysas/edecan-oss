"use client";

/**
 * Tarjeta "Servidores MCP" de `/app/configuracion` (`ARCHITECTURE.md` §15,
 * WP-V6-07; wishlist `REQUISITOS_V2.md` categoría 👨‍💻 Programador — "MCP
 * Servers"). A diferencia del resto de tarjetas de esta pantalla
 * (`CardCredencial`, pensada para UNA credencial activa a la vez), acá el
 * tenant puede conectar VARIOS servidores MCP — así que esta tarjeta arma su
 * propia lista + formulario de alta directo con los primitivos de `ui.tsx`,
 * sin envolver en `CardCredencial`.
 *
 * `localMode` (de `GET /v1/setup/detect`, ya lo carga `page.tsx`) decide si
 * la opción "stdio (comando local)" del selector de transporte está
 * habilitada — fuera de modo local el backend la rechaza siempre
 * (`edecan_mcp.seguridad.validar_comando_mcp`), así que se deshabilita acá
 * de entrada con un tooltip explicando por qué, en vez de dejar que el
 * usuario complete el formulario para toparse con un 400.
 */

import { useCallback, useEffect, useState } from "react";

import { CheckIcon, CodeIcon, PlusIcon, TrashIcon, XIcon } from "@/components/icons";
import { OfficialLink, SetupStep, SetupSteps } from "@/components/configuracion/SetupGuide";
import { Alert, Badge, Button, Card, CardBody, CardHeader, Field, Input, Select, Spinner } from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  deleteMcpServer,
  getMcpServerTools,
  getMcpServers,
  putMcpServer,
  type MCPServerOut,
  type MCPTransporte,
  type MCPToolOut,
} from "@/lib/api-mcp";
import { META_ADS_MCP_GUIDE } from "@/lib/connector-guides";

function mensajeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "No se pudo conectar con el servidor MCP.";
}

interface HeaderPar {
  clave: string;
  valor: string;
}

export function CardServidoresMcp({
  localMode,
  hideHeader = false,
  onServerConnected,
}: {
  localMode: boolean;
  /** Oculta el `CardHeader` (título/descripción/badge/botón "Configurar") —
   * para cuando esta tarjeta se embebe en un lugar que ya trae su propio
   * título (p. ej. el wizard de bienvenida, que usa `PasoWizard`). Sin
   * header no hay botón "Configurar" para desplegar el formulario, así que
   * en ese caso arranca ya desplegado (ver `useState` de `expandido` abajo). */
  hideHeader?: boolean;
  /** Se dispara cada vez que se conecta un servidor nuevo — el wizard lo usa
   * para decidir cuándo mostrar su botón "Continuar". */
  onServerConnected?: () => void;
}) {
  const [servidores, setServidores] = useState<MCPServerOut[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [eliminando, setEliminando] = useState<string | null>(null);
  const [expandido, setExpandido] = useState(hideHeader);

  const cargar = useCallback(async () => {
    setError(null);
    try {
      setServidores(await getMcpServers());
    } catch (err) {
      setError(mensajeError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  async function handleEliminar(nombre: string) {
    if (!window.confirm(`¿Quitar el servidor MCP «${nombre}»? Sus herramientas dejarán de estar disponibles en el chat.`)) {
      return;
    }
    setEliminando(nombre);
    try {
      await deleteMcpServer(nombre);
      await cargar();
    } catch (err) {
      setError(mensajeError(err));
    } finally {
      setEliminando(null);
    }
  }

  const hayServidores = (servidores?.length ?? 0) > 0;

  return (
    <Card>
      {!hideHeader && (
        <CardHeader
          title={
            <span className="flex items-center gap-2">
              <CodeIcon className="h-4 w-4 text-brand-600" />
              Herramientas externas (MCP)
            </span>
          }
          description="Añade capacidades de otros servicios. Edecan prueba la conexión antes de guardar y siempre pide confirmación antes de usar una herramienta MCP."
          actions={
            <div className="flex items-center gap-2">
              <Badge variant={hayServidores ? "success" : "neutral"}>
                {hayServidores ? `${servidores!.length} guardado${servidores!.length === 1 ? "" : "s"}` : "Sin configurar"}
              </Badge>
              <Button size="sm" variant={hayServidores ? "secondary" : "primary"} onClick={() => setExpandido((v) => !v)}>
                {expandido ? "Cerrar" : "Configurar"}
              </Button>
            </div>
          }
        />
      )}
      <CardBody className="space-y-4">
        {error && <Alert variant="error">{error}</Alert>}

        {loading ? (
          <div className="flex justify-center py-6">
            <Spinner className="h-5 w-5 text-slate-400" />
          </div>
        ) : (
          <>
            {hayServidores && (
              <div className="space-y-2">
                {servidores!.map((servidor) => (
                  <FilaServidor
                    key={servidor.nombre}
                    servidor={servidor}
                    onEliminar={() => handleEliminar(servidor.nombre)}
                    eliminando={eliminando === servidor.nombre}
                  />
                ))}
              </div>
            )}
            {!hayServidores && !expandido && (
              <p className="text-sm text-slate-400">Aún no conectaste ningún servidor MCP.</p>
            )}
            {expandido && (
              <div className={hayServidores ? "border-t border-slate-100 pt-4 dark:border-slate-800" : ""}>
                <FormularioAlta
                  localMode={localMode}
                  onConnected={() => {
                    void cargar();
                    onServerConnected?.();
                  }}
                />
              </div>
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}

function FilaServidor({
  servidor,
  onEliminar,
  eliminando,
}: {
  servidor: MCPServerOut;
  onEliminar: () => void;
  eliminando: boolean;
}) {
  const [herramientas, setHerramientas] = useState<MCPToolOut[] | null>(null);
  const [cargandoHerramientas, setCargandoHerramientas] = useState(false);
  const [errorHerramientas, setErrorHerramientas] = useState<string | null>(null);
  const [comprobado, setComprobado] = useState(false);

  async function verHerramientas() {
    if (herramientas !== null) {
      setHerramientas(null); // toggle: ya estaban visibles, las oculta
      return;
    }
    setCargandoHerramientas(true);
    setErrorHerramientas(null);
    try {
      const resultado = await getMcpServerTools(servidor.nombre);
      setHerramientas(resultado.tools);
      setComprobado(true);
    } catch (err) {
      setErrorHerramientas(mensajeError(err));
      setComprobado(true);
    } finally {
      setCargandoHerramientas(false);
    }
  }

  return (
    <div className="rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-700">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-slate-700 dark:text-slate-200">
          <CheckIcon
            className={`h-3.5 w-3.5 shrink-0 ${
              comprobado && !errorHerramientas
                ? "text-emerald-600 dark:text-emerald-400"
                : "text-slate-400"
            }`}
          />
          <strong className="font-medium">{servidor.nombre}</strong>
          <Badge variant={comprobado ? (errorHerramientas ? "warning" : "success") : "neutral"}>
            {comprobado ? (errorHerramientas ? "No disponible" : "Disponible ahora") : "Guardado"}
          </Badge>
          {servidor.autenticacion_configurada && <Badge variant="neutral">Acceso cifrado</Badge>}
          {servidor.estado !== "active" && <Badge variant="warning">{servidor.estado}</Badge>}
        </span>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => void verHerramientas()}
            disabled={cargandoHerramientas}
            className="text-xs font-medium text-brand-600 hover:text-brand-700 disabled:opacity-50 dark:text-brand-400"
          >
            {cargandoHerramientas
              ? "Comprobando…"
              : herramientas !== null
                ? "Ocultar herramientas"
                : "Comprobar ahora"}
          </button>
          <button
            type="button"
            onClick={onEliminar}
            disabled={eliminando}
            className="inline-flex items-center gap-1 text-xs font-medium text-rose-600 hover:text-rose-700 disabled:opacity-50 dark:text-rose-400"
          >
            {eliminando ? <Spinner className="h-3 w-3" /> : <TrashIcon className="h-3 w-3" />}
            Quitar
          </button>
        </div>
      </div>
      <p className="mt-1 truncate text-xs text-slate-400">{servidor.url ?? servidor.comando ?? ""}</p>
      {errorHerramientas && <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">{errorHerramientas}</p>}
      {herramientas !== null && (
        <ul className="mt-2 space-y-1 border-t border-slate-100 pt-2 dark:border-slate-800">
          {herramientas.length === 0 ? (
            <li className="text-xs text-slate-400">Este servidor no expone ninguna herramienta.</li>
          ) : (
            herramientas.map((tool) => (
              <li key={tool.name} className="text-xs text-slate-500 dark:text-slate-400">
                <code className="text-slate-700 dark:text-slate-200">{tool.name}</code>
                {tool.description ? ` — ${tool.description}` : ""}
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}

function FormularioAlta({ localMode, onConnected }: { localMode: boolean; onConnected: () => void }) {
  const [nombre, setNombre] = useState("");
  const [transporte, setTransporte] = useState<MCPTransporte>("http");
  const [url, setUrl] = useState("");
  const [comando, setComando] = useState("");
  const [headers, setHeaders] = useState<HeaderPar[]>([]);
  const [env, setEnv] = useState<HeaderPar[]>([]);
  const [plantillaMetaAds, setPlantillaMetaAds] = useState(false);
  const [busy, setBusy] = useState(false);
  const [resultado, setResultado] = useState<{ ok: boolean; mensaje: string } | null>(null);

  function agregarHeader() {
    setHeaders((prev) => [...prev, { clave: "", valor: "" }]);
  }

  function actualizarHeader(indice: number, campo: "clave" | "valor", valor: string) {
    setHeaders((prev) => prev.map((h, i) => (i === indice ? { ...h, [campo]: valor } : h)));
  }

  function quitarHeader(indice: number) {
    setHeaders((prev) => prev.filter((_, i) => i !== indice));
  }

  function usarMetaAdsLocal() {
    setPlantillaMetaAds(true);
    setNombre("meta-ads");
    setTransporte("stdio");
    setUrl("");
    setComando(META_ADS_MCP_GUIDE.localCommand);
    setHeaders([]);
    setEnv([{ clave: META_ADS_MCP_GUIDE.tokenEnvName, valor: "" }]);
    setResultado(null);
  }

  function usarConfiguracionManual() {
    setPlantillaMetaAds(false);
    setNombre("");
    setTransporte("http");
    setUrl("");
    setComando("");
    setHeaders([]);
    setEnv([]);
    setResultado(null);
  }

  function actualizarEnv(indice: number, campo: "clave" | "valor", valor: string) {
    setEnv((prev) => prev.map((item, i) => (i === indice ? { ...item, [campo]: valor } : item)));
  }

  function agregarEnv() {
    setEnv((prev) => [...prev, { clave: "", valor: "" }]);
  }

  function quitarEnv(indice: number) {
    setEnv((prev) => prev.filter((_, i) => i !== indice));
  }

  async function probarYConectar() {
    setBusy(true);
    setResultado(null);
    try {
      const headersObjeto = Object.fromEntries(
        headers.filter((h) => h.clave.trim().length > 0).map((h) => [h.clave.trim(), h.valor]),
      );
      const envObjeto = Object.fromEntries(
        env
          .filter((item) => item.clave.trim().length > 0)
          .map((item) => [item.clave.trim(), item.valor]),
      );
      await putMcpServer({
        nombre: nombre.trim(),
        transporte,
        url: transporte === "http" ? url.trim() : undefined,
        comando: transporte === "stdio" ? comando.trim() : undefined,
        headers: headersObjeto,
        env: transporte === "stdio" ? envObjeto : undefined,
        validate: true,
      });
      setResultado({ ok: true, mensaje: "Conectado y validado — sus herramientas ya están disponibles en el chat." });
      setNombre("");
      setUrl("");
      setComando("");
      setHeaders([]);
      setEnv([]);
      setPlantillaMetaAds(false);
      onConnected();
    } catch (err) {
      setResultado({ ok: false, mensaje: mensajeError(err) });
    } finally {
      setBusy(false);
    }
  }

  const puedeConectar =
    nombre.trim().length > 0 &&
    (transporte === "http" ? url.trim().length > 0 : comando.trim().length > 0) &&
    env.every((item) => item.clave.trim().length > 0 && item.valor.trim().length > 0);

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-brand-100 bg-brand-50/50 p-3 dark:border-brand-900 dark:bg-brand-950/20">
        <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">
          ¿Qué quieres conectar?
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            variant={plantillaMetaAds ? "primary" : "secondary"}
            onClick={usarMetaAdsLocal}
            disabled={!localMode || busy}
          >
            Meta Ads por MCP
          </Button>
          <Button
            type="button"
            size="sm"
            variant={!plantillaMetaAds ? "primary" : "secondary"}
            onClick={usarConfiguracionManual}
            disabled={busy}
          >
            Otro servidor
          </Button>
        </div>
        {!localMode && (
          <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
            Meta Ads por MCP local necesita la app de escritorio. La conexión nativa de Meta Ads
            funciona en cualquier instalación.
          </p>
        )}
      </div>

      {plantillaMetaAds && (
        <div className="space-y-3">
          <Alert variant="info">
            Esta plantilla usa un servidor comunitario de solo lectura, no un producto oficial de
            Meta. Revisa su código antes de ejecutarlo. Para crear campañas con confirmación y
            siempre pausadas, usa la conexión nativa de Meta Ads.
          </Alert>
          <SetupSteps>
            <SetupStep number={1}>
              <OfficialLink href={META_ADS_MCP_GUIDE.metaAppsUrl}>Abrir mis apps de Meta</OfficialLink>
              <span className="mt-1 block">
                Genera un token propio con permiso <code>ads_read</code>.
              </span>
            </SetupStep>
            <SetupStep number={2}>
              <OfficialLink href={META_ADS_MCP_GUIDE.communitySourceUrl}>
                Revisar el servidor MCP comunitario
              </OfficialLink>
            </SetupStep>
            <SetupStep number={3}>
              Pega el token abajo. Edecan lo guarda cifrado y lo entrega solo a ese proceso local.
            </SetupStep>
          </SetupSteps>
          <p className="text-xs leading-5 text-slate-500 dark:text-slate-400">
            El MCP oficial de Meta está en beta en <code>{META_ADS_MCP_GUIDE.officialEndpoint}</code>,
            pero exige OAuth interactivo. Edecan no reutiliza tokens de Graph para saltarse ese
            flujo ni los coloca en URLs.
          </p>
        </div>
      )}

      <Field label="Nombre" htmlFor="mcp_nombre" hint="Un nombre corto para identificarlo, p. ej. 'mi-servidor' o 'notion'.">
        <Input
          id="mcp_nombre"
          value={nombre}
          onChange={(e) => setNombre(e.target.value)}
          placeholder="mi-servidor-mcp"
          autoComplete="off"
          disabled={busy}
          readOnly={plantillaMetaAds}
        />
      </Field>

      <Field label="Transporte" htmlFor="mcp_transporte">
        <Select
          id="mcp_transporte"
          value={transporte}
          onChange={(e) => setTransporte(e.target.value as MCPTransporte)}
          disabled={busy || plantillaMetaAds}
        >
          <option value="http">Servidor remoto por URL</option>
          <option value="stdio" disabled={!localMode} title={localMode ? undefined : "Solo en modo local (app de escritorio)"}>
            stdio (comando local){localMode ? "" : " — solo en modo local"}
          </option>
        </Select>
        {!localMode && (
          <p className="mt-1 text-xs text-slate-400">
            «stdio» (ejecutar un comando local) solo está disponible en la app de escritorio — en un servidor hospedado,
            conecta un servidor MCP por HTTP.
          </p>
        )}
      </Field>

      {transporte === "http" ? (
        <Field label="URL" htmlFor="mcp_url" hint="https:// obligatorio en modo hospedado.">
          <Input
            id="mcp_url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://mi-servidor-mcp.example.com/rpc"
            autoComplete="off"
            disabled={busy}
          />
        </Field>
      ) : (
        <Field label="Aplicación local" htmlFor="mcp_comando" hint="El comando que inicia la herramienta en esta computadora.">
          <Input
            id="mcp_comando"
            value={comando}
            onChange={(e) => setComando(e.target.value)}
            placeholder="npx -y mi-servidor-mcp"
            autoComplete="off"
            disabled={busy || !localMode}
            readOnly={plantillaMetaAds}
          />
        </Field>
      )}

      {transporte === "http" && (
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
              Headers (opcional — p. ej. autenticación de tu servidor)
            </span>
            <button
              type="button"
              onClick={agregarHeader}
              disabled={busy}
              className="inline-flex items-center gap-1 text-xs font-medium text-brand-600 hover:text-brand-700 disabled:opacity-50 dark:text-brand-400"
            >
              <PlusIcon className="h-3 w-3" /> Agregar
            </button>
          </div>
          {headers.length === 0 ? (
            <p className="text-xs text-slate-400">Sin headers adicionales.</p>
          ) : (
            <div className="space-y-2">
              {headers.map((h, i) => (
                <div key={i} className="flex items-center gap-2">
                  <Input
                    value={h.clave}
                    onChange={(e) => actualizarHeader(i, "clave", e.target.value)}
                    placeholder="Authorization"
                    autoComplete="off"
                    disabled={busy}
                    aria-label="Nombre del header"
                  />
                  <Input
                    type="password"
                    value={h.valor}
                    onChange={(e) => actualizarHeader(i, "valor", e.target.value)}
                    placeholder="Bearer …"
                    autoComplete="new-password"
                    disabled={busy}
                    aria-label="Valor del header"
                  />
                  <button
                    type="button"
                    onClick={() => quitarHeader(i)}
                    disabled={busy}
                    className="shrink-0 rounded p-1.5 text-slate-400 hover:bg-slate-100 hover:text-rose-600 disabled:opacity-50 dark:hover:bg-slate-800"
                    aria-label="Quitar header"
                  >
                    <XIcon className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {transporte === "stdio" && (
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
              Acceso secreto para esta herramienta
            </span>
            {!plantillaMetaAds && (
              <button
                type="button"
                onClick={agregarEnv}
                disabled={busy}
                className="inline-flex items-center gap-1 text-xs font-medium text-brand-600 hover:text-brand-700 disabled:opacity-50 dark:text-brand-400"
              >
                <PlusIcon className="h-3 w-3" /> Agregar
              </button>
            )}
          </div>
          {env.length === 0 ? (
            <p className="text-xs text-slate-400">
              Esta herramienta no necesita credenciales adicionales.
            </p>
          ) : (
            <div className="space-y-2">
              {env.map((item, i) => (
                <div key={`${item.clave}-${i}`} className="flex items-center gap-2">
                  <Input
                    value={item.clave}
                    onChange={(event) => actualizarEnv(i, "clave", event.target.value)}
                    placeholder="NOMBRE_DEL_TOKEN"
                    autoComplete="off"
                    disabled={busy}
                    readOnly={plantillaMetaAds}
                    aria-label="Nombre de la credencial local"
                  />
                  <Input
                    type="password"
                    value={item.valor}
                    onChange={(event) => actualizarEnv(i, "valor", event.target.value)}
                    placeholder="Pega aquí tu token"
                    autoComplete="new-password"
                    disabled={busy}
                    aria-label="Valor secreto de la credencial local"
                  />
                  {!plantillaMetaAds && (
                    <button
                      type="button"
                      onClick={() => quitarEnv(i)}
                      disabled={busy}
                      className="shrink-0 rounded p-1.5 text-slate-400 hover:bg-slate-100 hover:text-rose-600 disabled:opacity-50 dark:hover:bg-slate-800"
                      aria-label="Quitar credencial local"
                    >
                      <XIcon className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
          <p className="mt-1 text-xs text-slate-400">
            Los valores se cifran y nunca vuelven a mostrarse ni se incluyen en el comando.
          </p>
        </div>
      )}

      {resultado && <Alert variant={resultado.ok ? "success" : "error"}>{resultado.mensaje}</Alert>}

      <Button size="sm" onClick={() => void probarYConectar()} loading={busy} disabled={!puedeConectar}>
        Probar y conectar
      </Button>
    </div>
  );
}
