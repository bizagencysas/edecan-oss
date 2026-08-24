import { messageBlocks, publicHttpUrl } from "./chat-blocks";

export interface ChatSource {
  title: string;
  url: string;
  site?: string | null;
  snippet?: string | null;
}

const HIT_LINE = /^(\d+)\.\s+(.+?)\s+—\s+(https?:\/\/\S+)\s*$/;

function hostnameOf(url: string): string | null {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/** Extrae chips de fuentes desde `buscar_web` (bloques + preview), igual que iOS. */
export function messageSources(toolCalls: unknown[] | null): ChatSource[] {
  if (!Array.isArray(toolCalls)) return [];
  const sources: ChatSource[] = [];
  const seen = new Set<string>();

  function add(source: ChatSource) {
    const url = publicHttpUrl(source.url);
    if (!url || seen.has(url)) return;
    seen.add(url);
    sources.push({
      title: source.title.trim() || hostnameOf(url) || url,
      url,
      site: source.site ?? hostnameOf(url),
      snippet: source.snippet ?? null,
    });
  }

  for (const block of messageBlocks(toolCalls)) {
    if (block.type === "link_preview") {
      add({
        title: block.title,
        url: block.url,
        site: block.site_name,
        snippet: block.description,
      });
    }
  }

  for (const item of toolCalls) {
    const call = record(item);
    if (!call || call.type !== "tool_end") continue;
    const name = typeof call.name === "string" ? call.name : "";
    if (name !== "buscar_web" && name !== "buscar_noticias" && name !== "deep_research") continue;
    const preview = typeof call.result_preview === "string" ? call.result_preview : "";
    const lines = preview.split("\n");
    for (let index = 0; index < lines.length; index += 1) {
      const match = lines[index].trim().match(HIT_LINE);
      if (!match) continue;
      const snippet = lines[index + 1]?.trim();
      add({
        title: match[2],
        url: match[3],
        snippet: snippet && !HIT_LINE.test(snippet) ? snippet : null,
      });
    }
  }

  return sources.slice(0, 12);
}
