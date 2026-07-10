"use client";

import { Fragment, type ReactNode } from "react";

/** Render de Markdown minimalista sin dependencias nuevas: negritas,
 * cursivas, tachado, código (inline y en bloque), enlaces, encabezados,
 * listas y separadores. Antes los mensajes del asistente se mostraban con
 * los símbolos de Markdown literales en pantalla (p. ej. "**texto**"); el
 * LLM siempre responde en Markdown así que esto es necesario, no opcional.
 */

const INLINE_RE =
  /(`[^`]+`)|(\[[^\]]+\]\([^)]+\))|(\*\*[^*]+\*\*)|(__[^_]+__)|(~~[^~]+~~)|(\*[^*]+\*)|(?<!\w)(_[^_]+_)(?!\w)/g;

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let lastIndex = 0;
  let i = 0;
  for (const match of text.matchAll(INLINE_RE)) {
    const token = match[0];
    const index = match.index ?? 0;
    if (index > lastIndex) nodes.push(text.slice(lastIndex, index));
    const key = `${keyPrefix}-${i++}`;
    if (token.startsWith("`")) {
      nodes.push(
        <code
          key={key}
          className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[0.85em] dark:bg-slate-800"
        >
          {token.slice(1, -1)}
        </code>,
      );
    } else if (token.startsWith("[")) {
      const linkMatch = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
      nodes.push(
        linkMatch ? (
          <a
            key={key}
            href={linkMatch[2]}
            target="_blank"
            rel="noreferrer"
            className="underline underline-offset-2 hover:text-brand-600"
          >
            {linkMatch[1]}
          </a>
        ) : (
          token
        ),
      );
    } else if (token.startsWith("**") || token.startsWith("__")) {
      nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("~~")) {
      nodes.push(<del key={key}>{token.slice(2, -2)}</del>);
    } else {
      nodes.push(<em key={key}>{token.slice(1, -1)}</em>);
    }
    lastIndex = index + token.length;
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return nodes;
}

export function MarkdownText({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let listItems: string[] = [];
  let codeLines: string[] | null = null;
  let key = 0;

  function flushList() {
    if (listItems.length === 0) return;
    blocks.push(
      <ul key={`ul-${key++}`} className="my-1 list-disc space-y-0.5 pl-5">
        {listItems.map((item, idx) => (
          <li key={idx}>{renderInline(item, `li-${key}-${idx}`)}</li>
        ))}
      </ul>,
    );
    listItems = [];
  }

  for (const rawLine of lines) {
    const line = rawLine.replace(/\r$/, "");

    if (/^\s*```/.test(line)) {
      if (codeLines === null) {
        flushList();
        codeLines = [];
      } else {
        blocks.push(
          <pre
            key={`code-${key++}`}
            className="my-1 overflow-x-auto rounded-lg bg-slate-100 p-2 text-xs dark:bg-slate-800"
          >
            <code>{codeLines.join("\n")}</code>
          </pre>,
        );
        codeLines = null;
      }
      continue;
    }
    if (codeLines !== null) {
      codeLines.push(line);
      continue;
    }

    if (/^(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line.trim())) {
      flushList();
      blocks.push(<hr key={`hr-${key++}`} className="my-2 border-slate-200 dark:border-slate-700" />);
      continue;
    }

    const headerMatch = /^(#{1,6})\s+(.*)$/.exec(line);
    if (headerMatch) {
      flushList();
      const level = headerMatch[1].length;
      const sizes: Record<number, string> = {
        1: "text-lg font-bold",
        2: "text-base font-bold",
        3: "text-base font-semibold",
        4: "text-sm font-semibold",
        5: "text-sm font-semibold",
        6: "text-sm font-semibold",
      };
      blocks.push(
        <div key={`h-${key++}`} className={`mt-1.5 mb-0.5 ${sizes[level]}`}>
          {renderInline(headerMatch[2], `h-${key}`)}
        </div>,
      );
      continue;
    }

    const bulletMatch = /^\s*[-*+]\s+(.*)$/.exec(line);
    if (bulletMatch) {
      listItems.push(bulletMatch[1]);
      continue;
    }

    const quoteMatch = /^>\s?(.*)$/.exec(line);
    flushList();
    if (quoteMatch) {
      blocks.push(
        <div
          key={`q-${key++}`}
          className="my-1 border-l-2 border-slate-300 pl-2 text-slate-500 dark:border-slate-600 dark:text-slate-400"
        >
          {renderInline(quoteMatch[1], `q-${key}`)}
        </div>,
      );
    } else if (line.trim() === "") {
      blocks.push(<br key={`br-${key++}`} />);
    } else {
      blocks.push(
        <Fragment key={`p-${key++}`}>
          {renderInline(line, `l-${key}`)}
          <br />
        </Fragment>,
      );
    }
  }
  flushList();
  if (codeLines !== null) {
    blocks.push(
      <pre
        key={`code-${key++}`}
        className="my-1 overflow-x-auto rounded-lg bg-slate-100 p-2 text-xs dark:bg-slate-800"
      >
        <code>{codeLines.join("\n")}</code>
      </pre>,
    );
  }
  return <>{blocks}</>;
}
