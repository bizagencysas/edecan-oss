import assert from "node:assert/strict";
import test from "node:test";

import { messageSources } from "./src/lib/chat-sources.ts";

test("extrae fuentes de bloques link_preview y del preview de buscar_web", () => {
  const sources = messageSources([
    {
      type: "tool_end",
      name: "buscar_web",
      result_preview: "1. Guía oficial — https://example.com/docs\n   Cómo instalar.\n2. Extra — https://news.example.org/a\n   Nota.",
      blocks_version: 1,
      blocks: [
        {
          schema_version: 1,
          type: "link_preview",
          url: "https://example.com/docs",
          title: "Guía oficial",
          site_name: "example.com",
          description: "Cómo instalar.",
        },
      ],
    },
  ]);

  assert.equal(sources.length, 2);
  assert.equal(sources[0].url, "https://example.com/docs");
  assert.equal(sources[0].site, "example.com");
  assert.equal(sources[1].url, "https://news.example.org/a");
});

test("ignora URLs privadas y tools que no son búsqueda", () => {
  const sources = messageSources([
    {
      type: "tool_end",
      name: "calculadora",
      result_preview: "1. Interno — https://example.com/ok",
      blocks_version: 1,
      blocks: [
        {
          schema_version: 1,
          type: "link_preview",
          url: "http://127.0.0.1/admin",
          title: "Local",
        },
      ],
    },
  ]);
  assert.equal(sources.length, 0);
});
