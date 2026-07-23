// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Screen Resolver                                                           ║
// ║  Given a user prompt, find the specific screen(s) referenced and load      ║
// ║  their actual source code from the analysis_json in the DB.                ║
// ║                                                                            ║
// ║  When user says "la pantalla de /help" → find the screen with route /help  ║
// ║  and return its full source code so Gemini can recreate the REAL UI.       ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

export interface ResolvedScreen {
  screenName: string;
  route: string;
  filePath: string;
  rawCode: string;
  texts: string[];
  components: string[];
  layout: string;
  complexity: string;
  structuralSnippet?: string;
}

interface ScreenData {
  screenName?: string;
  route?: string;
  filePath?: string;
  rawCode?: string;
  texts?: string[];
  components?: string[];
  estimatedLayout?: string;
  complexity?: string;
  structuralSnippet?: string;
}

/**
 * Resolve screens referenced in the user's prompt.
 * Searches by:
 *   1. Route match (e.g. "/help", "/wallet", "/(tabs)/mercado")
 *   2. Screen name match (e.g. "Dashboard", "AI Advisor", "Marketplace")
 *   3. Filename match (e.g. "help.tsx", "fy-advisor.tsx")
 *   4. Keyword match in texts (e.g. "credit score", "concierge")
 *
 * Returns the matching screens with their FULL source code.
 */
export function resolveScreensFromPrompt(
  prompt: string,
  analysisJson: unknown,
): ResolvedScreen[] {
  const analysis = typeof analysisJson === 'string'
    ? safeParse(analysisJson)
    : analysisJson as Record<string, unknown>;

  if (!analysis) return [];

  const screens = (analysis as Record<string, unknown>).screens as ScreenData[] | undefined;
  if (!screens || !Array.isArray(screens)) return [];

  const promptLower = prompt.toLowerCase();
  const matched: ResolvedScreen[] = [];
  const matchedIds = new Set<string>();

  // ── Strategy 1: Explicit route references ──
  // Look for patterns like "/help", "/wallet", "/(tabs)/mercado", "/fy-advisor"
  const routePatterns = promptLower.match(/\/[\w\-()\/.]+/g) || [];
  for (const routeRef of routePatterns) {
    for (const screen of screens) {
      if (!screen.route || !screen.rawCode) continue;
      const screenRoute = screen.route.toLowerCase();
      const id = screen.filePath || screen.screenName || '';

      if (matchedIds.has(id)) continue;

      // Exact route match or partial match
      if (
        screenRoute === routeRef ||
        screenRoute.endsWith(routeRef) ||
        screenRoute.includes(routeRef.replace('/', ''))
      ) {
        matched.push(toResolvedScreen(screen));
        matchedIds.add(id);
      }
    }
  }

  // ── Strategy 2: Screen name / keyword references ──
  // Common screen names that might appear in prompts
  const screenKeywords = extractScreenKeywords(promptLower);
  for (const keyword of screenKeywords) {
    for (const screen of screens) {
      if (!screen.rawCode) continue;
      const id = screen.filePath || screen.screenName || '';
      if (matchedIds.has(id)) continue;

      const nameMatch =
        screen.screenName?.toLowerCase().includes(keyword) ||
        screen.filePath?.toLowerCase().includes(keyword) ||
        screen.route?.toLowerCase().includes(keyword);

      if (nameMatch) {
        matched.push(toResolvedScreen(screen));
        matchedIds.add(id);
      }
    }
  }

  // ── Strategy 3: Content/text match ──
  // If user mentions specific feature text like "credit score", "concierge"
  if (matched.length === 0) {
    const contentKeywords = extractContentKeywords(promptLower);
    for (const keyword of contentKeywords) {
      for (const screen of screens) {
        if (!screen.rawCode) continue;
        const id = screen.filePath || screen.screenName || '';
        if (matchedIds.has(id)) continue;

        const textsMatch = screen.texts?.some(t => t.toLowerCase().includes(keyword));
        const codeMatch = screen.rawCode.toLowerCase().includes(keyword);

        if (textsMatch || codeMatch) {
          matched.push(toResolvedScreen(screen));
          matchedIds.add(id);
        }
      }
      if (matched.length >= 3) break; // Don't overmatch
    }
  }

  // ── Strategy 4: "Most important screens" fallback ──
  // When user asks for "key screens" / "main screens" / "pantallas más importantes"
  // without naming specific ones, pick the top-priority screens from the directory.
  if (matched.length === 0) {
    const wantsImportant = /\b(m[aá]s\s+importantes?|key|main|principales?|top|most\s+important|pantallas?\s+clave|core)\b/i.test(prompt);
    if (wantsImportant) {
      const withPriority = (screens as Array<ScreenData & { priority?: string }>).filter(s => s.rawCode);
      const prioritySorted = [...withPriority].sort((a, b) => {
        const order = (p?: string) => p === 'P0' ? 0 : p === 'P1' ? 1 : p === 'P2' ? 2 : 3;
        return order(a.priority) - order(b.priority);
      });
      // Try to detect requested count, default to 3
      const countMatch = prompt.match(/(\d+)\s+(?:pantallas?|screens?)/i);
      const want = countMatch ? Math.min(parseInt(countMatch[1], 10), 5) : 3;
      for (const s of prioritySorted.slice(0, want)) {
        matched.push(toResolvedScreen(s));
      }
    }
  }

  // Return max 8 screens for full context
  return matched.slice(0, 8);
}

/**
 * Build a context string from resolved screens for the system prompt.
 * This gives Gemini the ACTUAL source code to recreate faithfully.
 */
export function buildScreenContext(screens: ResolvedScreen[]): string {
  if (screens.length === 0) return '';

  const sections = screens.map((screen, i) => {
    // Truncate very long code files to avoid token limits
    const code = screen.rawCode.length > 12000
      ? screen.rawCode.slice(0, 12000) + '\n// ... (truncated for brevity)'
      : screen.rawCode;

    return `
═══ REAL APP SCREEN ${i + 1}: "${screen.screenName}" (route: ${screen.route}) ═══
File: ${screen.filePath}
Layout: ${screen.layout} | Complexity: ${screen.complexity}
UI Texts: ${screen.texts.slice(0, 20).join(' | ')}
Components: ${screen.components.slice(0, 20).join(', ')}

${screen.structuralSnippet ? `STRUCTURAL SNIPPET (JSX/HTML Hierarchy):\n\`\`\`jsx\n${screen.structuralSnippet}\n\`\`\`\n` : ''}
ACTUAL SOURCE CODE (React Native / Expo):
\`\`\`tsx
${code}
\`\`\`
`;
  });

  return `
╔═══════════════════════════════════════════════════════════════╗
║  REAL APP SCREENS — SOURCE CODE FROM GITHUB                   ║
║  Recreate these screens FAITHFULLY in your HTML design.       ║
║  Study the layout, components, text, and styling carefully.   ║
╚═══════════════════════════════════════════════════════════════╝

${sections.join('\n')}

INSTRUCTIONS FOR RECREATING THESE SCREENS:
1. Study the React Native code above and understand the UI layout
2. Identify all visible elements: headers, cards, buttons, inputs, lists, icons
3. Map the React Native components to HTML/CSS equivalents
4. Use the EXACT text strings from the code (in the original language)
5. Match the visual hierarchy, spacing, and proportions
6. Apply the brand's color tokens to the recreated UI
7. The phone mockup should show this ACTUAL screen, not a generic placeholder
8. Include all interactive elements (buttons, inputs, toggles) styled realistically
`;
}

/**
 * Get a summary of ALL available screens for the planner.
 */
export function buildScreenDirectory(analysisJson: unknown): string {
  const analysis = typeof analysisJson === 'string'
    ? safeParse(analysisJson)
    : analysisJson as Record<string, unknown>;

  if (!analysis) return '';

  const screens = (analysis as Record<string, unknown>).screens as Array<ScreenData & { priority?: string }> | undefined;
  if (!screens || !Array.isArray(screens) || screens.length === 0) return '';

  // Sort by priority (P0 first) — gives Opus a clear hierarchy when user says "most important screens"
  const sorted = [...screens].sort((a, b) => {
    const order = (p?: string) => p === 'P0' ? 0 : p === 'P1' ? 1 : p === 'P2' ? 2 : 3;
    return order(a.priority) - order(b.priority);
  });

  const lines = sorted.map(s => {
    const prio = s.priority ? `[${s.priority}] ` : '';
    return `  • ${prio}${s.screenName || 'Unknown'} — route: ${s.route || 'N/A'} — layout: ${s.estimatedLayout || '?'} — file: ${s.filePath || '?'}`;
  });

  return `
AVAILABLE APP SCREENS (${screens.length} total — reference these by route or name. P0 = most important, P1 = secondary, P2 = supporting):
${lines.join('\n')}

RULES:
- When the user mentions a specific screen (e.g. "/help", "dashboard", "wallet"), find it in the list above and describe its ACTUAL UI in the brief.
- When the user says "the most important screens", "key screens", "main screens", "pantallas más importantes", or "top N screens" without naming them, ALWAYS pick from the P0 tier first, then P1.
- NEVER invent screens that aren't in this list. If the user asks for a screen that doesn't exist, pick the closest match by purpose.`;
}

// ── Internal helpers ──

function toResolvedScreen(s: ScreenData): ResolvedScreen {
  return {
    screenName: s.screenName || 'Unknown',
    route: s.route || '/',
    filePath: s.filePath || '',
    rawCode: s.rawCode || '',
    texts: s.texts || [],
    components: s.components || [],
    layout: s.estimatedLayout || 'generic',
    complexity: s.complexity || 'medium',
    structuralSnippet: s.structuralSnippet || '',
  };
}

function extractScreenKeywords(prompt: string): string[] {
  const keywords: string[] = [];

  // Common screen name patterns
  const patterns: [RegExp, string][] = [
    [/\b(?:pantalla|screen|page|vista|página)\s+(?:de\s+)?(\w+)/gi, '$1'],
    [/\b(?:dashboard|home|inicio|principal)/gi, 'dashboard'],
    [/\b(?:wallet|billetera|cartera)/gi, 'wallet'],
    [/\b(?:profile|perfil)/gi, 'profile'],
    [/\b(?:settings|ajustes|configuraci[oó]n)/gi, 'settings'],
    [/\b(?:marketplace|mercado|market)/gi, 'marketplace'],
    [/\b(?:advisor|asesor|concierge|chat|asistente)/gi, 'advisor'],
    [/\b(?:help|ayuda|soporte|support)/gi, 'help'],
    [/\b(?:login|iniciar\s*sesi[oó]n|sign\s*in)/gi, 'login'],
    [/\b(?:register|registro|sign\s*up)/gi, 'register'],
    [/\b(?:onboarding|bienvenida|welcome|intro)/gi, 'onboarding'],
    [/\b(?:invest|invertir|inversión|inversion)/gi, 'invest'],
    [/\b(?:savings|ahorros)/gi, 'savings'],
    [/\b(?:payments|pagos)/gi, 'payments'],
    [/\b(?:notifications|notificaciones)/gi, 'notifications'],
    [/\b(?:auto[\s-]?invest)/gi, 'auto-invest'],
    [/\b(?:portfolio|portafolio)/gi, 'portafolio'],
    [/\b(?:credit|crédito|credito)/gi, 'credit'],
    [/\b(?:challenge|reto|desafío)/gi, 'challenge'],
  ];

  for (const [regex, replacement] of patterns) {
    const matches = prompt.match(regex);
    if (matches) {
      const kw = replacement === '$1'
        ? matches.map(m => m.replace(regex, replacement).toLowerCase())
        : [replacement.toLowerCase()];
      keywords.push(...kw);
    }
  }

  return [...new Set(keywords)];
}

function extractContentKeywords(prompt: string): string[] {
  // Extract meaningful content words that might match screen texts
  const words = prompt
    .replace(/[^\w\sáéíóúñü]/g, ' ')
    .split(/\s+/)
    .filter(w => w.length > 3)
    .filter(w => !['para', 'como', 'esta', 'este', 'crea', 'crear', 'hacer', 'quiero', 'necesito', 'mockup', 'slide', 'post', 'design'].includes(w.toLowerCase()));

  return [...new Set(words.map(w => w.toLowerCase()))];
}

function safeParse(val: unknown): Record<string, unknown> | null {
  if (!val) return null;
  if (typeof val === 'object') return val as Record<string, unknown>;
  try { return JSON.parse(val as string) as Record<string, unknown>; } catch { return null; }
}
