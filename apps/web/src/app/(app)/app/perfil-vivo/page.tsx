"use client";

/**
 * `/app/perfil-vivo` — perfil vivo del usuario (`ROADMAP_V2.md` §21/§7.4,
 * WP-V2-13). Ver `docs/perfil-vivo.md` para la explicación completa de
 * producto: qué es, cómo se construye (consolidación de memoria ->
 * `user_profiles` -> espejo en `memory_items`) y cómo influye en cada
 * respuesta del asistente.
 *
 * Sin librería de componentes ni modal (`ROADMAP_V2.md` §7.10: prohibido
 * agregar dependencias npm) — "Borrar perfil" usa una confirmación de dos
 * pasos inline (`confirmingDelete`) en vez de `window.confirm`, para quedar
 * en el mismo lenguaje visual que el resto de la app (`Alert` + botones).
 */

import { useEffect, useState } from "react";

import { PlusIcon, XIcon } from "@/components/icons";
import {
  Alert,
  Button,
  Card,
  CardBody,
  CardHeader,
  Input,
  PageHeader,
  Spinner,
  Textarea,
} from "@/components/ui";
import {
  ApiError,
  CATEGORIAS_PERFIL,
  deletePerfilVivo,
  getPerfilVivo,
  rebuildPerfilVivo,
  updatePerfilVivo,
  type CategoriaPerfil,
  type IdentidadPerfil,
  type PerfilVivo,
} from "@/lib/api-perfil";
import { formatDateTime } from "@/lib/format";

const PERFIL_VACIO: PerfilVivo = {
  resumen: "",
  datos: {
    identidad: {
      nombre_preferido: "",
      nombre_completo: "",
      pronombres: "",
      fecha_nacimiento: "",
      pais: "",
      ciudad: "",
      zona_horaria: "",
      ocupacion: "",
      idioma_preferido: "",
      forma_de_trato: "",
      biografia: "",
    },
    gustos: [], proyectos: [], metas: [], relaciones: [], empresas: [], habitos: [],
  },
  version: 0,
  updated_at: null,
};

const RESUMEN_MAX_CHARS = 500;

function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Ocurrió un error inesperado.";
}

// ---------------------------------------------------------------------------
// Una sección de chips agregables/eliminables (una de las 6 categorías)
// ---------------------------------------------------------------------------

function SeccionPerfil({
  label,
  items,
  busy,
  onChange,
}: {
  label: string;
  items: string[];
  busy: boolean;
  onChange: (nuevaLista: string[]) => Promise<boolean>;
}) {
  const [nuevo, setNuevo] = useState("");

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    const valor = nuevo.trim();
    if (!valor) return;
    if (items.some((it) => it.toLowerCase() === valor.toLowerCase())) {
      setNuevo("");
      return;
    }
    const ok = await onChange([...items, valor]);
    if (ok) setNuevo("");
  }

  async function handleRemove(item: string) {
    await onChange(items.filter((it) => it !== item));
  }

  return (
    <Card>
      <CardHeader
        title={label}
        actions={busy ? <Spinner className="h-3.5 w-3.5 text-slate-400" /> : undefined}
      />
      <CardBody>
        <div className="mb-3 flex min-h-[1.75rem] flex-wrap gap-1.5">
          {items.length === 0 ? (
            <p className="text-xs text-slate-400">Sin entradas todavía.</p>
          ) : (
            items.map((item) => (
              <span
                key={item}
                className="inline-flex items-center gap-1 rounded-full bg-brand-100 py-0.5 pl-2.5 pr-1 text-xs font-medium text-brand-700 dark:bg-brand-900/60 dark:text-brand-300"
              >
                {item}
                <button
                  type="button"
                  onClick={() => void handleRemove(item)}
                  disabled={busy}
                  className="rounded-full p-0.5 hover:bg-brand-200 disabled:opacity-50 dark:hover:bg-brand-800"
                  aria-label={`Quitar «${item}» de ${label}`}
                >
                  <XIcon className="h-3 w-3" />
                </button>
              </span>
            ))
          )}
        </div>
        <form onSubmit={handleAdd} className="flex gap-1.5">
          <Input
            value={nuevo}
            onChange={(e) => setNuevo(e.target.value)}
            placeholder="Agregar…"
            disabled={busy}
            className="h-8 py-1 text-xs"
          />
          <Button
            type="submit"
            size="sm"
            variant="secondary"
            disabled={busy || !nuevo.trim()}
            aria-label={`Agregar a ${label}`}
          >
            <PlusIcon className="h-3.5 w-3.5" />
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Página
// ---------------------------------------------------------------------------

export default function PerfilVivoPage() {
  const [perfil, setPerfil] = useState<PerfilVivo>(PERFIL_VACIO);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const [resumenDraft, setResumenDraft] = useState("");
  const [identidadDraft, setIdentidadDraft] = useState<IdentidadPerfil>(
    PERFIL_VACIO.datos.identidad,
  );
  const [savingIdentidad, setSavingIdentidad] = useState(false);
  const [savingResumen, setSavingResumen] = useState(false);
  const [categoriaOcupada, setCategoriaOcupada] = useState<CategoriaPerfil | null>(null);

  const [rebuilding, setRebuilding] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    void load();
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const result = await getPerfilVivo();
      setPerfil(result);
      setResumenDraft(result.resumen);
      setIdentidadDraft(result.datos.identidad);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleGuardarIdentidad(e: React.FormEvent) {
    e.preventDefault();
    setSavingIdentidad(true);
    setError(null);
    try {
      const actualizado = await updatePerfilVivo({ datos: { identidad: identidadDraft } });
      setPerfil(actualizado);
      setIdentidadDraft(actualizado.datos.identidad);
      setInfo("Perfil personal guardado. Edecán lo usará desde tu próximo mensaje.");
    } catch (err) {
      setError(describeError(err));
    } finally {
      setSavingIdentidad(false);
    }
  }

  async function handleGuardarResumen(e: React.FormEvent) {
    e.preventDefault();
    setSavingResumen(true);
    setError(null);
    try {
      const actualizado = await updatePerfilVivo({ resumen: resumenDraft.trim() });
      setPerfil(actualizado);
      setResumenDraft(actualizado.resumen);
      setInfo("Resumen guardado.");
    } catch (err) {
      setError(describeError(err));
    } finally {
      setSavingResumen(false);
    }
  }

  async function handleCategoriaChange(
    campo: CategoriaPerfil,
    nuevaLista: string[],
  ): Promise<boolean> {
    setCategoriaOcupada(campo);
    setError(null);
    try {
      const actualizado = await updatePerfilVivo({ datos: { [campo]: nuevaLista } });
      setPerfil(actualizado);
      return true;
    } catch (err) {
      setError(describeError(err));
      return false;
    } finally {
      setCategoriaOcupada(null);
    }
  }

  async function handleRebuild() {
    setRebuilding(true);
    setError(null);
    setInfo(null);
    try {
      const respuesta = await rebuildPerfilVivo();
      setInfo(respuesta.mensaje);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setRebuilding(false);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    setError(null);
    try {
      await deletePerfilVivo();
      setPerfil(PERFIL_VACIO);
      setResumenDraft("");
      setIdentidadDraft(PERFIL_VACIO.datos.identidad);
      setInfo("Tu perfil fue borrado. Se irá reconstruyendo a medida que converses de nuevo.");
    } catch (err) {
      setError(describeError(err));
    } finally {
      setDeleting(false);
      setConfirmingDelete(false);
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <Spinner className="h-6 w-6 text-slate-400" />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Perfil vivo"
        description="Quién eres, cómo quieres que te hable y lo que Edecán aprende contigo. Tu perfil personal se usa siempre, en computador, iOS y Android."
        actions={
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void handleRebuild()}
            loading={rebuilding}
          >
            Reconstruir desde mis memorias
          </Button>
        }
      />

      {error && (
        <div className="mb-4">
          <Alert variant="error">{error}</Alert>
        </div>
      )}
      {info && (
        <div className="mb-4">
          <Alert variant="success">{info}</Alert>
        </div>
      )}

      <Card className="mb-6">
        <CardHeader
          title="Quién eres"
          description="Tú controlas estos datos. Edecán no los cambia ni los inventa automáticamente."
        />
        <CardBody>
          <form onSubmit={handleGuardarIdentidad} className="space-y-4">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {([
                ["nombre_preferido", "Nombre preferido", "Como quieres que Edecán te llame"],
                ["nombre_completo", "Nombre completo", "Tu nombre completo"],
                ["pronombres", "Pronombres", "Ej. él, ella, elle"],
                ["fecha_nacimiento", "Fecha de nacimiento", "Ej. 8 de enero de 1996"],
                ["pais", "País", "Ej. Venezuela"],
                ["ciudad", "Ciudad", "Ej. Medellín"],
                ["zona_horaria", "Zona horaria", "Ej. America/Bogota"],
                ["ocupacion", "A qué te dedicas", "Ej. Fundador de productos"],
                ["idioma_preferido", "Idioma preferido", "Ej. Español de Venezuela"],
                ["forma_de_trato", "Cómo quieres que te hable", "Ej. Cercano, directo y de tú"],
              ] as Array<[keyof IdentidadPerfil, string, string]>).map(([campo, label, placeholder]) => (
                <label key={campo} className="space-y-1.5 text-sm font-medium text-slate-700 dark:text-slate-200">
                  <span>{label}</span>
                  <Input
                    value={identidadDraft[campo]}
                    onChange={(e) => setIdentidadDraft((actual) => ({ ...actual, [campo]: e.target.value }))}
                    placeholder={placeholder}
                    maxLength={160}
                  />
                </label>
              ))}
            </div>
            <label className="block space-y-1.5 text-sm font-medium text-slate-700 dark:text-slate-200">
              <span>Sobre ti</span>
              <Textarea
                value={identidadDraft.biografia}
                onChange={(e) => setIdentidadDraft((actual) => ({ ...actual, biografia: e.target.value }))}
                rows={4}
                maxLength={1000}
                placeholder="Cuéntale a Edecán lo que debería saber para ayudarte mejor."
              />
            </label>
            <div className="flex justify-end">
              <Button type="submit" loading={savingIdentidad}>
                Guardar mi perfil
              </Button>
            </div>
          </form>
        </CardBody>
      </Card>

      <Card className="mb-6">
        <CardHeader
          title="Resumen"
          description={
            perfil.version > 0
              ? `Versión ${perfil.version} · actualizado ${formatDateTime(perfil.updated_at)}`
              : "Todavía no se ha construido un perfil — escribe uno a mano o dale clic a «Reconstruir desde mis memorias» arriba."
          }
        />
        <CardBody>
          <form onSubmit={handleGuardarResumen} className="space-y-3">
            <Textarea
              value={resumenDraft}
              onChange={(e) => setResumenDraft(e.target.value.slice(0, RESUMEN_MAX_CHARS))}
              rows={3}
              placeholder="Prefieres respuestas breves y directas…"
            />
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400">
                {resumenDraft.length}/{RESUMEN_MAX_CHARS}
              </span>
              <Button
                type="submit"
                size="sm"
                loading={savingResumen}
                disabled={resumenDraft === perfil.resumen}
              >
                Guardar resumen
              </Button>
            </div>
          </form>
        </CardBody>
      </Card>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {CATEGORIAS_PERFIL.map(({ campo, label }) => (
          <SeccionPerfil
            key={campo}
            label={label}
            items={perfil.datos[campo]}
            busy={categoriaOcupada === campo}
            onChange={(nuevaLista) => handleCategoriaChange(campo, nuevaLista)}
          />
        ))}
      </div>

      <Card className="mt-6">
        <CardHeader title="Privacidad" />
        <CardBody>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Este perfil es solo tuyo y permanece aislado de las demás cuentas. Nadie fuera de tu
            espacio puede verlo ni modificarlo. Puedes editarlo en cualquier momento arriba o
            borrarlo por completo abajo.
          </p>
        </CardBody>
      </Card>

      <Card className="mt-6 border-rose-200 dark:border-rose-900">
        <CardHeader title="Zona de peligro" />
        <CardBody>
          {!confirmingDelete ? (
            <Button variant="danger" size="sm" onClick={() => setConfirmingDelete(true)}>
              Borrar perfil
            </Button>
          ) : (
            <div className="space-y-3">
              <Alert variant="error">
                ¿Seguro que quieres borrar tu perfil? Esto elimina el resumen, las 6 categorías y su
                copia en tu memoria. No se puede deshacer (aunque tu asistente puede reconstruirlo
                más adelante a partir de nuevas conversaciones).
              </Alert>
              <div className="flex gap-2">
                <Button
                  variant="danger"
                  size="sm"
                  loading={deleting}
                  onClick={() => void handleDelete()}
                >
                  Sí, borrar mi perfil
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={deleting}
                  onClick={() => setConfirmingDelete(false)}
                >
                  Cancelar
                </Button>
              </div>
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
