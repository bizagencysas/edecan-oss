// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  fydesign Visual Critic — Vision Edition                                   ║
// ║  Uses REAL screenshots + Gemini Pro Vision for pixel-accurate critique.    ║
// ║  Catches visual issues text-based review misses: alignment, spacing,       ║
// ║  mockup realism, typography rendering, color harmony in context.           ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callGemini, GEMINI_PRO } from './gemini-client';
import { renderHtmlToPNG } from '@/lib/screenshot-renderer';
import { refineDesign } from './visual-critic';
import { validateHTML } from '@/lib/design-engine/code-extractor';
import type { VisualCritique } from './visual-critic';

// ─── Language Detection ───────────────────────────────────────────────

/**
 * Heuristic to detect if the design context is in Spanish.
 * Checks for Spanish-specific characters, design terms, and common articles.
 */
function detectSpanish(...texts: (string | undefined)[]): boolean {
  const combined = texts.filter(Boolean).join(' ');
  if (!combined) return false;

  // Spanish-specific characters — strong signal
  if (/[áéíóúüñ¿¡]/i.test(combined)) return true;

  // Common Spanish design terms
  const spanishDesignTerms =
    /\b(diseñ[oó]|marca|colores|tipografí[ae]|maquetación|interfaz|usuario|botón|pantalla|móvil|navegación|contenido|encabezado|párrafo|sección|formulario|logotipo|icono|animación|transición|posición|tamaño|imagen|texto|fondo|espacio|estilo)\b/i;
  if (spanishDesignTerms.test(combined)) return true;

  // Check for frequent Spanish articles (3+ occurrences is a strong signal)
  const articles = combined.match(
    /\b(el|la|los|las|del|para|con|por|que|esta|este|como|más|pero|son)\b/gi,
  );
  if (articles && articles.length >= 3) return true;

  return false;
}

// ─── Prompt Builders ──────────────────────────────────────────────────

function buildCritiquePrompt(
  width: number,
  height: number,
  userContext: string,
  brief: string,
  brandContext: string,
  spanish: boolean,
): string {
  if (spanish) {
    return `Eres un crítico de diseño visual senior en una agencia top. Revisa este screenshot de ${width}×${height}px como si lo vieras en pantalla.

${userContext}RESUMEN DEL PROYECTO:
${brief}${brandContext}

CRITERIOS DE EVALUACIÓN VISUAL (examina la IMAGEN real renderizada, NO el código):

1. FIDELIDAD DE MARCA: Usa los colores, estilo e identidad de la marca correctamente? Se siente como la marca indicada?
2. COMPOSICIÓN Y EQUILIBRIO: El layout está bien balanceado? Buen uso de espacios en blanco? Elementos alineados correctamente?
3. JERARQUÍA TIPOGRÁFICA: Jerarquía clara? Títulos con tamaño adecuado? Buen contraste y legibilidad?
4. REALISMO DEL MOCKUP DE TELÉFONO: Si hay un mockup de teléfono — marco realista? Dynamic Island? Barra de estado? UI real adentro (no solo un rectángulo vacío)?
5. ARMONÍA DE COLOR: Los colores funcionan juntos? Contraste adecuado? Fiel a la marca?
6. ESPACIADO: Espacio de respiro adecuado? Padding y márgenes consistentes?
7. CALIDAD PROFESIONAL: Pasaría como diseño premium, específico y memorable? O se ve genérico/como plantilla?
8. LIENZO: El contenido ocupa los ${width}×${height}px completos? Hay espacios vacíos no intencionales?

Escala de puntuación:
- 10: diseño de agencia top, perfecto, representación de marca impecable
- 7-9: sólido, profesional, listo para enviar con ajustes menores
- 4-6: amateur — problemas significativos en múltiples áreas
- 0-3: roto, ignora la marca, o fundamentalmente incorrecto

Responde SOLO con JSON válido (sin markdown, sin explicación):
{
  "score": <0-10>,
  "worth_fixing": <true si score < 8>,
  "issues": ["problema concreto y accionable 1", "..."],
  "improvements": ["cambio específico de CSS/HTML 1", "..."]
}`;
  }

  return `You are a senior visual design critic at a top agency. Review this ${width}×${height}px screenshot as if seeing it on a screen.

${userContext}DESIGN BRIEF:
${brief}${brandContext}

VISUAL EVALUATION CRITERIA (examine the ACTUAL rendered image, NOT the code):

1. BRAND FIDELITY: Are the brand's colors, style, and identity used correctly? Does it feel like the right brand?
2. COMPOSITION/BALANCE: Is the layout well-balanced? Good use of whitespace? Elements properly aligned?
3. TYPOGRAPHY HIERARCHY: Clear hierarchy? Headlines appropriately sized? Good contrast and readability?
4. PHONE MOCKUP REALISM: If a phone mockup is present — realistic frame, Dynamic Island, status bar, real UI content inside (not just a blank rectangle)?
5. COLOR HARMONY: Do colors work together? Proper contrast? On-brand?
6. SPACING/WHITESPACE: Adequate breathing room? Consistent padding and margins?
7. PROFESSIONAL QUALITY: Would this pass as premium, specific, memorable design? Or does it look generic/templated?
8. CANVAS: Does content fill the full ${width}×${height}px? Any unintended empty space?

Score rubric:
- 10: top-tier agency design, pixel-perfect, flawless brand representation
- 7-9: solid, professional, ship-ready with minor polish
- 4-6: amateurish — significant issues in multiple areas
- 0-3: broken, ignores the brand, or fundamentally wrong

Return ONLY valid JSON (no markdown, no explanation):
{
  "score": <0-10>,
  "worth_fixing": <true if score < 8>,
  "issues": ["concrete, actionable issue 1", "..."],
  "improvements": ["specific CSS/HTML change 1", "..."]
}`;
}

// ─── Vision Critique ──────────────────────────────────────────────────

/**
 * Vision-based design critique using Gemini Pro Vision.
 *
 * Renders the HTML to a PNG screenshot at the exact design dimensions,
 * then sends the screenshot to Gemini Pro Vision for pixel-accurate
 * visual analysis. Returns structured critique in the same shape as
 * the text-based critiqueDesign.
 *
 * This catches visual issues that text-only review misses:
 * - Pixel-level alignment and spacing inconsistencies
 * - Actual visual hierarchy and balance (not just code analysis)
 * - Mockup realism (Dynamic Island, status bar, bezels, real UI)
 * - Color harmony in rendered context
 * - Font rendering, sizing, and readability
 *
 * @param html - Full HTML document to critique
 * @param brief - Design brief for context
 * @param width - Screenshot width in pixels
 * @param height - Screenshot height in pixels
 * @param originalPrompt - Original user request (optional)
 * @param brandTokens - Brand identity guidelines (optional)
 * @returns Structured critique or null on failure
 */
export async function critiqueDesignVision(
  html: string,
  brief: string,
  width: number,
  height: number,
  originalPrompt?: string,
  brandTokens?: string,
): Promise<VisualCritique | null> {
  // ── Step 1: Render HTML to PNG screenshot ──────────────────────
  let screenshotBuffer: Buffer;
  try {
    screenshotBuffer = await renderHtmlToPNG(html, width, height);
  } catch (e) {
    console.warn(
      '[VisualCriticVision] Screenshot render failed:',
      e instanceof Error ? e.message : e,
    );
    return null;
  }

  // ── Step 2: Convert to base64 (no data: prefix — gemini-client
  //    adds the inlineData wrapper) ──────────────────────────────
  const base64 = screenshotBuffer.toString('base64');

  // ── Step 3: Build the critique prompt ──────────────────────────
  const brandContext = brandTokens
    ? `\nBRAND IDENTITY (the design MUST reflect this):\n${brandTokens}\n`
    : '';
  const userContext = originalPrompt
    ? `\nORIGINAL USER REQUEST: "${originalPrompt}"\n`
    : '';
  const isSpanish = detectSpanish(brief, originalPrompt, html);
  const prompt = buildCritiquePrompt(
    width,
    height,
    userContext,
    brief,
    brandContext,
    isSpanish,
  );

  // ── Step 4: Send to Gemini Pro Vision ──────────────────────────
  let raw: string;
  try {
    raw = await callGemini(prompt, {
      model: GEMINI_PRO,
      temperature: 0.2,
      maxTokens: 4000,
      json: true,
      image: { mimeType: 'image/png', data: base64 },
    });
  } catch (e) {
    console.warn(
      '[VisualCriticVision] Gemini API call failed:',
      e instanceof Error ? e.message : e,
    );
    return null;
  }

  // ── Step 5: Parse and validate the JSON response ───────────────
  try {
    const parsed = JSON.parse(raw) as VisualCritique;

    // Validate that all required fields are present with the right types
    if (typeof parsed.score !== 'number') {
      throw new Error('Missing or invalid "score" field');
    }
    if (typeof parsed.worth_fixing !== 'boolean') {
      throw new Error('Missing or invalid "worth_fixing" field');
    }
    if (!Array.isArray(parsed.issues)) {
      throw new Error('Missing or invalid "issues" field');
    }
    if (!Array.isArray(parsed.improvements)) {
      throw new Error('Missing or invalid "improvements" field');
    }

    // Clamp score to 0-10
    parsed.score = Math.max(0, Math.min(10, parsed.score));

    return parsed;
  } catch (e) {
    console.warn(
      '[VisualCriticVision] Failed to parse critique JSON:',
      e instanceof Error ? e.message : e,
    );
    console.warn('[VisualCriticVision] Raw response:', raw.slice(0, 500));
    return null;
  }
}

// ─── Vision Improve Loop ─────────────────────────────────────────────

/**
 * Full critique → refine loop using REAL vision (screenshots + Gemini Pro Vision).
 *
 * Each iteration:
 * 1. Renders the current HTML to a PNG screenshot
 * 2. Sends the screenshot to Gemini Pro Vision for visual critique
 * 3. If the design needs improvement, refines the HTML using the critique
 * 4. Validates the refined HTML before continuing
 *
 * Stops when:
 * - Score reaches 9+ (design is good enough)
 * - worth_fixing is false (no meaningful issues found)
 * - No improvements suggested by the critic
 * - Refined HTML fails validation
 * - Max iterations reached
 *
 * @param html - Initial HTML to improve
 * @param brief - Design brief
 * @param width - Design width in pixels
 * @param height - Design height in pixels
 * @param systemPrompt - System prompt for the refine step
 * @param maxIterations - Maximum critique→refine cycles (default 2)
 * @param originalPrompt - Original user request (optional)
 * @param brandTokens - Brand identity guidelines (optional)
 * @returns Final HTML, final score, and number of iterations performed
 */
export async function visuallyImproveVision(
  html: string,
  brief: string,
  width: number,
  height: number,
  systemPrompt: string,
  maxIterations = 2,
  originalPrompt?: string,
  brandTokens?: string,
): Promise<{ html: string; finalScore: number | null; iterations: number }> {
  let current = html;
  let iterations = 0;
  let lastScore: number | null = null;

  for (let i = 0; i < maxIterations; i++) {
    iterations++;

    // ── Critique ──────────────────────────────────────────────
    const critique = await critiqueDesignVision(
      current,
      brief,
      width,
      height,
      originalPrompt,
      brandTokens,
    );

    if (!critique) {
      console.warn(
        '[VisualCriticVision] Critique returned null — stopping loop',
      );
      break;
    }

    lastScore = critique.score;
    console.log(
      `[VisualCriticVision] Iteration ${iterations} score: ${critique.score}/10 ` +
        `(worth_fixing=${critique.worth_fixing})`,
    );
    console.log(
      `[VisualCriticVision] Issues: ${critique.issues.join(' | ')}`,
    );

    // ── Check stopping conditions ─────────────────────────────
    if (!critique.worth_fixing || critique.score >= 9) {
      console.log(
        `[VisualCriticVision] Score ${critique.score}/10 — design is good enough, stopping`,
      );
      break;
    }

    if (critique.improvements.length === 0) {
      console.warn(
        '[VisualCriticVision] No improvements suggested — stopping',
      );
      break;
    }

    // ── Refine ────────────────────────────────────────────────
    try {
      current = await refineDesign(
        current,
        critique,
        brief,
        width,
        height,
        systemPrompt,
      );

      // Validate the refined HTML
      const issues = validateHTML(current);
      if (issues.length > 0) {
        console.warn(
          '[VisualCriticVision] Refined HTML has validation issues, stopping:',
          issues.join(', '),
        );
        break;
      }

      console.log(
        `[VisualCriticVision] Iteration ${iterations} refine complete ` +
          `(${current.length} chars)`,
      );
    } catch (e) {
      console.warn(
        '[VisualCriticVision] Refine failed:',
        e instanceof Error ? e.message : e,
      );
      break;
    }
  }

  return { html: current, finalScore: lastScore, iterations };
}
