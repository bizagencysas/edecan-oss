"use client";

/**
 * Árbol de archivos lateral del IDE embebido, cargado perezosamente carpeta
 * por carpeta (`GET /v1/ide/tree?path=...&max_depth=1` por cada expansión,
 * ver `WP-V2-08` en `ROADMAP_V2.md` §7.8) — nunca trae el sandbox completo
 * de una sola vez.
 */

import { useEffect, useState } from "react";

import { ChevronDownIcon, FileIcon } from "@/components/icons";
import { Spinner } from "@/components/ui";
import { getIdeTree, type IdeTreeNode } from "@/lib/api-ide";

function joinPath(parent: string, name: string): string {
  return parent ? `${parent}/${name}` : name;
}

export function FileTree({
  activePath,
  onOpenFile,
}: {
  activePath: string | null;
  onOpenFile: (path: string) => void;
}) {
  const [root, setRoot] = useState<IdeTreeNode[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [childrenByPath, setChildrenByPath] = useState<Record<string, IdeTreeNode[]>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [loadingPath, setLoadingPath] = useState<string | null>(null);

  useEffect(() => {
    void loadRoot();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadRoot() {
    setError(null);
    try {
      const tree = await getIdeTree(undefined, 1);
      setRoot(tree.entries);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar el árbol de archivos.");
    }
  }

  async function toggleDir(path: string) {
    const wasExpanded = !!expanded[path];
    setExpanded((prev) => ({ ...prev, [path]: !wasExpanded }));
    if (wasExpanded || childrenByPath[path]) return;

    setLoadingPath(path);
    try {
      const tree = await getIdeTree(path, 1);
      setChildrenByPath((prev) => ({ ...prev, [path]: tree.entries }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo abrir la carpeta.");
    } finally {
      setLoadingPath(null);
    }
  }

  function renderEntries(entries: IdeTreeNode[], parentPath: string) {
    return (
      <ul className="space-y-0.5">
        {entries.map((entry) => {
          const path = joinPath(parentPath, entry.name);

          if (!entry.is_dir) {
            const isActive = activePath === path;
            return (
              <li key={path}>
                <button
                  type="button"
                  onClick={() => onOpenFile(path)}
                  title={path}
                  className={`flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-left text-xs hover:bg-slate-100 dark:hover:bg-slate-800 ${
                    isActive
                      ? "bg-brand-50 text-brand-700 dark:bg-brand-950/40 dark:text-brand-300"
                      : "text-slate-600 dark:text-slate-300"
                  }`}
                >
                  <FileIcon className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                  <span className="truncate">{entry.name}</span>
                </button>
              </li>
            );
          }

          const isOpen = !!expanded[path];
          const children = childrenByPath[path];
          return (
            <li key={path}>
              <button
                type="button"
                onClick={() => void toggleDir(path)}
                className="flex w-full items-center gap-1 rounded-md px-1.5 py-1 text-left text-xs font-medium text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800"
              >
                <ChevronDownIcon
                  className={`h-3 w-3 shrink-0 text-slate-400 transition-transform ${
                    isOpen ? "" : "-rotate-90"
                  }`}
                />
                <span className="truncate">{entry.name}</span>
                {loadingPath === path && <Spinner className="h-3 w-3 shrink-0 text-slate-400" />}
              </button>
              {isOpen && children && (
                <div className="ml-3.5 border-l border-slate-100 pl-1.5 dark:border-slate-800">
                  {children.length === 0 ? (
                    <p className="px-1.5 py-1 text-xs text-slate-400">vacía</p>
                  ) : (
                    renderEntries(children, path)
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    );
  }

  if (!root && error) {
    return <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>;
  }
  if (!root) {
    return (
      <div className="flex justify-center py-6">
        <Spinner className="h-4 w-4 text-slate-400" />
      </div>
    );
  }
  if (root.length === 0) {
    return <p className="text-xs text-slate-400">El sandbox del companion está vacío.</p>;
  }

  return (
    <div>
      {error && <p className="mb-2 text-xs text-rose-600 dark:text-rose-400">{error}</p>}
      {renderEntries(root, "")}
    </div>
  );
}
