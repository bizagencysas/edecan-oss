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
              Servidores MCP
            </span>
          }
          description="Conecta tus propios servidores MCP (Model Context Protocol) — sus herramientas aparecen en el chat, misiones y automatizaciones. Bring-your-own: tu servidor, tus credenciales."
          actions={
            <div className="flex items-center gap-2">
              <Badge variant={hayServidores ? "success" : "neutral"}>
                {hayServidores ? `${servidores!.length} conectado${servidores!.length === 1 ? "" : "s"}` : "Sin conectar"}
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
    } catch (err) {
      setErrorHerramientas(mensajeError(err));
    } finally {
      setCargandoHerramientas(false);
    }
  }

  return (
    <div className="rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-700">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-slate-700 dark:text-slate-200">
          <CheckIcon className="h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
          <strong className="font-medium">{servidor.nombre}</strong>
          <Badge variant="neutral">{servidor.transporte}</Badge>
          {servidor.estado !== "active" && <Badge variant="warning">{servidor.estado}</Badge>}
        </span>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => void verHerramientas()}
            disabled={cargandoHerramientas}
            className="text-xs font-medium text-brand-600 hover:text-brand-700 disabled:opacity-50 dark:text-brand-400"
          >
            {cargandoHerramientas ? "Conectando…" : herramientas !== null ? "Ocultar herramientas" : "Ver herramientas"}
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

  async function probarYConectar() {
    setBusy(true);
    setResultado(null);
    try {
      const headersObjeto = Object.fromEntries(
        headers.filter((h) => h.clave.trim().length > 0).map((h) => [h.clave.trim(), h.valor]),
      );
      await putMcpServer({
        nombre: nombre.trim(),
        transporte,
        url: transporte === "http" ? url.trim() : undefined,
        comando: transporte === "stdio" ? comando.trim() : undefined,
        headers: headersObjeto,
        validate: true,
      });
      setResultado({ ok: true, mensaje: "Conectado y validado — sus herramientas ya están disponibles en el chat." });
      setNombre("");
      setUrl("");
      setComando("");
      setHeaders([]);
      onConnected();
    } catch (err) {
      setResultado({ ok: false, mensaje: mensajeError(err) });
    } finally {
      setBusy(false);
    }
  }

  const puedeConectar =
    nombre.trim().length > 0 && (transporte === "http" ? url.trim().length > 0 : comando.trim().length > 0);

  return (
    <div className="space-y-3">
      <Field label="Nombre" htmlFor="mcp_nombre" hint="Un nombre corto para identificarlo, p. ej. 'mi-servidor' o 'notion'.">
        <Input
          id="mcp_nombre"
          value={nombre}
          onChange={(e) => setNombre(e.target.value)}
          placeholder="mi-servidor-mcp"
          autoComplete="off"
          disabled={busy}
        />
      </Field>

      <Field label="Transporte" htmlFor="mcp_transporte">
        <Select
          id="mcp_transporte"
          value={transporte}
          onChange={(e) => setTransporte(e.target.value as MCPTransporte)}
          disabled={busy}
        >
          <option value="http">HTTP (servidor remoto)</option>
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
        <Field label="Comando" htmlFor="mcp_comando" hint="El comando completo para arrancar el servidor, p. ej. 'npx -y mi-servidor-mcp'.">
          <Input
            id="mcp_comando"
            value={comando}
            onChange={(e) => setComando(e.target.value)}
            placeholder="npx -y mi-servidor-mcp"
            autoComplete="off"
            disabled={busy || !localMode}
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
                    value={h.valor}
                    onChange={(e) => actualizarHeader(i, "valor", e.target.value)}
                    placeholder="Bearer …"
                    autoComplete="off"
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

      {resultado && <Alert variant={resultado.ok ? "success" : "error"}>{resultado.mensaje}</Alert>}

      <Button size="sm" onClick={() => void probarYConectar()} loading={busy} disabled={!puedeConectar}>
        Probar y conectar
      </Button>
    </div>
  );
}
