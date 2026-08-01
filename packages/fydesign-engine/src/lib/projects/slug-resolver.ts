// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Slug Resolver — Human-friendly project name → URL-safe slug                  ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

export function generateSlug(name: string): string {
  const slug = name
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, '')
    .replace(/[\s_]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 80);
  return slug || `project-${Date.now().toString(36)}`;
}

export function slugMatchScore(query: string, slug: string): number {
  const q = query.toLowerCase().trim();
  const s = slug.toLowerCase().trim();

  if (q === s) return 1.0;
  if (s.startsWith(q)) return 0.9;
  if (s.includes(q)) return 0.7;

  // Word-level match: how many query words appear in slug
  const qWords = q.split(/[\s-]+/).filter(Boolean);
  if (qWords.length > 0) {
    const matched = qWords.filter((w) => s.includes(w));
    return (matched.length / qWords.length) * 0.5;
  }

  return 0;
}

export function normalizeForSearch(input: string): string {
  return generateSlug(input);
}
