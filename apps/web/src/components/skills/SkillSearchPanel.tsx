"use client";

/**
 * Buscador del marketplace de "Agent Skills" (`/app/skills`, WP-V3-04): un
 * input hace doble función — «Buscar» pega la palabra clave contra el índice
 * de skills.sh (`POST /v1/skills/search`), «Instalar directo» instala lo que
 * esté escrito TAL CUAL como fuente (`owner/repo`, `owner/repo/sub/path`, o
 * una URL de GitHub/skills.sh) sin depender del índice — mismo espíritu que
 * `edecan_skills.tools.BuscarSkillsTool`: el índice es solo descubrimiento
 * conveniente, instalar por `owner/repo` directo siempre funciona igual.
 *
 * `handleInstall` distingue `fuente` (WP-V5-04, `edecan_skills.security`): un resultado de
 * este buscador viene de `POST /v1/skills/search`, que hoy SOLO pega contra el índice de
 * skills.sh (`edecan_api.routers.skills`) — instalar uno de esos resultados pasa
 * `fuente="skills_sh"` para que quede marcada `trust_tier="indexada"`; «Instalar directo»
 * usa el default `"directo"` (`trust_tier="sin_revisar"`), porque el usuario armó `source`
 * a mano sin pasar por ningún índice curado. Mismo ajuste quirúrgico fuera de las rutas
 * asignadas a WP-V5-04 que `InstalledSkillItem.tsx` — ver su docstring.
 */

import { useState } from "react";

import { PlusIcon, SearchIcon } from "@/components/icons";
import { Alert, Button, Input, Spinner } from "@/components/ui";
import {
  ApiError,
  installSkill,
  searchSkills,
  type SkillDetail,
  type SkillFuente,
  type SkillSearchHit,
} from "@/lib/api-skills";

function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Ocurrió un error inesperado.";
}

export function SkillSearchPanel({
  onInstalled,
}: {
  onInstalled: (skill: SkillDetail) => void;
}) {
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [results, setResults] = useState<SkillSearchHit[]>([]);
  const [searchError, setSearchError] = useState<string | null>(null);

  const [installingSource, setInstallingSource] = useState<string | null>(null);
  const [installError, setInstallError] = useState<string | null>(null);

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    setSearchError(null);
    setInstallError(null);
    try {
      setResults(await searchSkills(q));
      setSearched(true);
    } catch (err) {
      setSearchError(describeError(err));
    } finally {
      setSearching(false);
    }
  }

  async function handleInstall(source: string, fuente: SkillFuente = "directo") {
    const s = source.trim();
    if (!s || installingSource) return;
    setInstallingSource(s);
    setInstallError(null);
    try {
      const skill = await installSkill(s, fuente);
      onInstalled(skill);
      setResults((prev) => prev.filter((r) => r.source !== s));
    } catch (err) {
      setInstallError(describeError(err));
    } finally {
      setInstallingSource(null);
    }
  }

  return (
    <div className="space-y-4">
      <form onSubmit={handleSearch} className="flex flex-col gap-2 sm:flex-row">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Palabra clave, o 'owner/repo' para instalar directo"
          className="flex-1"
        />
        <div className="flex gap-2">
          <Button type="submit" variant="secondary" loading={searching} disabled={!query.trim()}>
            <SearchIcon className="h-4 w-4" /> Buscar
          </Button>
          <Button
            type="button"
            loading={installingSource === query.trim() && installingSource !== null}
            disabled={!query.trim() || installingSource !== null}
            onClick={() => void handleInstall(query)}
          >
            <PlusIcon className="h-4 w-4" /> Instalar directo
          </Button>
        </div>
      </form>

      {searchError && <Alert variant="error">{searchError}</Alert>}
      {installError && <Alert variant="error">{installError}</Alert>}

      {searching ? (
        <div className="flex justify-center py-6">
          <Spinner className="h-5 w-5 text-slate-400" />
        </div>
      ) : searched && results.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Sin resultados en el índice de skills.sh para «{query}». Si ya sabes el
          <span className="font-mono"> owner/repo</span>, usa «Instalar directo» arriba.
        </p>
      ) : results.length > 0 ? (
        <ul className="space-y-2">
          {results.map((hit) => (
            <li
              key={hit.source}
              className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-slate-100 px-3 py-2.5 dark:border-slate-800"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">
                  {hit.nombre}{" "}
                  <span className="font-mono text-xs font-normal text-slate-400">
                    {hit.source}
                  </span>
                </p>
                {hit.descripcion && (
                  <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                    {hit.descripcion}
                  </p>
                )}
                {hit.installs !== null && (
                  <p className="mt-0.5 text-xs text-slate-400">
                    {new Intl.NumberFormat("es").format(hit.installs)} instalaciones
                  </p>
                )}
              </div>
              <Button
                size="sm"
                loading={installingSource === hit.source}
                disabled={installingSource !== null}
                onClick={() => void handleInstall(hit.source, "skills_sh")}
              >
                <PlusIcon className="h-3.5 w-3.5" /> Instalar
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
