// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  fydesign — AI Marketing Copy Engine                                       ║
// ║  Uses Gemini Flash for App Store marketing headlines and translations      ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { ScreenInfo, Language, GeneratedCopy, AppAnalysis } from './types';
import { callDeepSeek as callGemini } from './ai/deepseek-client';

interface CopyRequest {
  appName: string;
  appDescription: string;
  screen: ScreenInfo;
  language: Language;
  tone?: string;
}

/**
 * Generate marketing copy for multiple screens × languages
 */
export async function generateAllCopy(
  analysis: AppAnalysis,
  screenIds: string[],
  languages: Language[],
): Promise<GeneratedCopy[]> {
  const screens = analysis.screens.filter((s) =>
    screenIds.includes(s.id),
  );

  const requests: CopyRequest[] = [];
  for (const screen of screens) {
    for (const lang of languages) {
      requests.push({
        appName: analysis.appName,
        appDescription: analysis.description,
        screen,
        language: lang,
      });
    }
  }

  return generateCopyBatch(requests, analysis);
}

async function generateCopyBatch(
  requests: CopyRequest[],
  analysis: AppAnalysis,
): Promise<GeneratedCopy[]> {
  const screensContext = requests
    .map(
      (r, i) => `
Screen ${i + 1}:
- ID: ${r.screen.id}
- Name: ${r.screen.screenName}
- Layout: ${r.screen.estimatedLayout}
- Language: ${r.language}
- Key texts found in code: ${r.screen.texts.slice(0, 10).join(' | ')}
- Components: ${r.screen.components.slice(0, 5).join(', ')}
- Icons: ${r.screen.icons.slice(0, 5).join(', ')}`,
    )
    .join('\n');

  const brandIntel = analysis.brandIntelligence || {};

  const systemPrompt = `You are an elite ASO (App Store Optimization) copywriter who creates headlines for app store screenshots. You ALWAYS respond with valid JSON only.`;

  const userPrompt = `APP CONTEXT:
- App Name: ${requests[0].appName}
- Description: ${requests[0].appDescription || 'Premium mobile application'}

BRAND INTELLIGENCE (Strictly follow this voice and style):
- Brand Voice: ${brandIntel.brandVoice || 'Professional and engaging'}
- Target Audience: ${brandIntel.targetAudience || 'General users'}
- Marketing Angles: ${(brandIntel.marketingAngles || []).join(' | ') || 'Convenience, Design, Power'}

For each screen below, generate:
1. "headline": A powerful, benefit-driven headline (3-6 words max). Think Apple-level marketing copy. MUST align with the Brand Voice.
2. "subtitle": A supporting line (5-12 words max). Explains the feature briefly.

RULES:
- Headlines must be IMPACTFUL and BENEFIT-DRIVEN (not feature descriptions)
- Use the screen's detected texts and layout to understand what it does
- Match the language specified for each screen
- For Spanish: Use natural Latin American Spanish, not Castilian
- Do NOT include the app name in the headline
- Do NOT use generic phrases like "All in one place" or "Everything you need"
- Make each headline UNIQUE — no repetition across screens

SCREENS TO WRITE FOR:
${screensContext}

Respond ONLY with a valid JSON array:
[
  {"screenId": "...", "language": "...", "headline": "...", "subtitle": "..."},
  ...
]`;

  try {
    const content = await callGemini(userPrompt, {
      // model handled by deepseek-client
      system: systemPrompt,
      temperature: 0.8,
      maxTokens: 4000,
      json: true,
    });

    const parsed = JSON.parse(content);

    // Robustly extract array of copy items from any format xAI returns
    let items: GeneratedCopy[] = [];

    if (Array.isArray(parsed)) {
      // Direct array: [{ screenId, headline, ... }, ...]
      items = parsed;
    } else if (typeof parsed === 'object' && parsed !== null) {
      // Look for any array value in the object
      const arrayKey = Object.keys(parsed).find(k => Array.isArray(parsed[k]));
      if (arrayKey) {
        items = parsed[arrayKey];
      } else if (parsed.screenId && parsed.headline) {
        // Single object with screenId — wrap in array
        items = [parsed];
      }
    }

    if (items.length > 0) {
      return items.map((item: GeneratedCopy) => ({
        screenId: item.screenId,
        language: item.language,
        headline: item.headline || 'Your App, Elevated',
        subtitle: item.subtitle || 'A premium experience',
      }));
    }

    console.warn('xAI returned unexpected format, using fallback. Keys:', Object.keys(parsed));
    return requests.map((r) => fallbackCopy(r));
  } catch (error) {
    console.error('AI copy generation error:', error);
    return requests.map((r) => fallbackCopy(r));
  }
}

/* ── Fallback Copy (when AI is unavailable) ───────────────────────────────── */

const FALLBACK_HEADLINES: Record<string, Record<Language, { headline: string; subtitle: string }>> = {
  dashboard: {
    en: { headline: 'Your Wealth at a Glance', subtitle: 'Track investments in real time' },
    es: { headline: 'Tu Riqueza de un Vistazo', subtitle: 'Rastrea inversiones en tiempo real' },
    pt: { headline: 'Sua Riqueza em um Olhar', subtitle: 'Acompanhe investimentos em tempo real' },
    fr: { headline: 'Votre Richesse en un Coup', subtitle: 'Suivez vos investissements en temps réel' },
  },
  marketplace: {
    en: { headline: 'Discover Opportunities', subtitle: 'Curated investments tailored to you' },
    es: { headline: 'Descubre Oportunidades', subtitle: 'Inversiones curadas para ti' },
    pt: { headline: 'Descubra Oportunidades', subtitle: 'Investimentos selecionados para você' },
    fr: { headline: 'Découvrez les Opportunités', subtitle: 'Des investissements sur mesure' },
  },
  wallet: {
    en: { headline: 'Total Control', subtitle: 'Manage your funds seamlessly' },
    es: { headline: 'Control Total', subtitle: 'Administra tus fondos sin fricción' },
    pt: { headline: 'Controle Total', subtitle: 'Gerencie seus fundos sem fricção' },
    fr: { headline: 'Contrôle Total', subtitle: 'Gérez vos fonds sans friction' },
  },
  profile: {
    en: { headline: 'Your Identity, Secured', subtitle: 'Verified and protected' },
    es: { headline: 'Tu Identidad, Segura', subtitle: 'Verificada y protegida' },
    pt: { headline: 'Sua Identidade, Segura', subtitle: 'Verificada e protegida' },
    fr: { headline: 'Votre Identité, Sécurisée', subtitle: 'Vérifiée et protégée' },
  },
  settings: {
    en: { headline: 'Make It Yours', subtitle: 'Personalize every detail' },
    es: { headline: 'Hazlo Tuyo', subtitle: 'Personaliza cada detalle' },
    pt: { headline: 'Faça do Seu Jeito', subtitle: 'Personalize cada detalhe' },
    fr: { headline: 'Faites-le Vôtre', subtitle: 'Personnalisez chaque détail' },
  },
  auth: {
    en: { headline: 'Start in Seconds', subtitle: 'Quick, secure onboarding' },
    es: { headline: 'Empieza en Segundos', subtitle: 'Registro rápido y seguro' },
    pt: { headline: 'Comece em Segundos', subtitle: 'Cadastro rápido e seguro' },
    fr: { headline: 'Commencez en Secondes', subtitle: 'Inscription rapide et sécurisée' },
  },
  chart: {
    en: { headline: 'See the Future', subtitle: 'AI-powered predictions and insights' },
    es: { headline: 'Mira el Futuro', subtitle: 'Predicciones e insights con IA' },
    pt: { headline: 'Veja o Futuro', subtitle: 'Previsões e insights com IA' },
    fr: { headline: 'Voyez le Futur', subtitle: "Prédictions et insights par l'IA" },
  },
  generic: {
    en: { headline: 'Powerful & Elegant', subtitle: 'Premium experience, every screen' },
    es: { headline: 'Potente y Elegante', subtitle: 'Experiencia premium, cada pantalla' },
    pt: { headline: 'Poderoso e Elegante', subtitle: 'Experiência premium, cada tela' },
    fr: { headline: 'Puissant et Élégant', subtitle: 'Expérience premium, chaque écran' },
  },
};

function fallbackCopy(request: CopyRequest): GeneratedCopy {
  const layout = request.screen.estimatedLayout;
  const lang = request.language;
  const copy = FALLBACK_HEADLINES[layout]?.[lang] || FALLBACK_HEADLINES.generic[lang];

  return {
    screenId: request.screen.id,
    language: lang,
    headline: copy.headline,
    subtitle: copy.subtitle,
  };
}
