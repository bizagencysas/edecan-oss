// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Gemini Next Action Suggester (A3)                                         ║
// ║  After generating designs, suggests 3 context-aware next steps.            ║
// ║  Each suggestion includes a ready-to-paste prompt the user can click.      ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { callGeminiJSON, GEMINI_PRO } from './gemini-client';

export interface NextAction {
  label: string;       // "Versión en inglés" — under 30 chars
  prompt: string;      // The exact prompt the user would type
  icon?: string;       // 'language' | 'phone' | 'email' | 'image' | 'sparkle' etc.
  reason?: string;     // short tooltip
}

/**
 * Ask Gemini to suggest 3 next actions based on what was just generated.
 * Returns empty array on failure (non-blocking).
 */
export async function suggestNextActions(input: {
  lastPrompt: string;
  generatedFolders: string[];
  brand?: { name?: string; blurb?: string; industry?: string };
  projectHistory?: string[];  // labels of all variants in the project so far
}): Promise<NextAction[]> {
  const prompt = `You are fydesign's coach. The user just generated some designs. Suggest 3 NEXT actions they'd realistically want to do.

JUST GENERATED: in folder(s) [${input.generatedFolders.join(', ')}] from prompt: "${input.lastPrompt}"
BRAND: ${input.brand?.name || 'unknown'}${input.brand?.industry ? ` (${input.brand.industry})` : ''}
PROJECT SO FAR: ${input.projectHistory?.slice(0, 20).join(', ') || 'this is their first generation'}

Suggest 3 different, useful next moves. Examples:
- If they made a Spanish carousel → suggest English version
- If they made a landing → suggest matching email or social ad
- If they made a social post → suggest story version
- If they made a single ad → suggest 2 more variants for A/B test
- If they have many but no pricing → suggest a pricing page
- If they have a campaign → suggest the missing piece (e.g., "they have landing + email, suggest social ad")

Each suggestion = a prompt the user can paste back into chat to get that next thing.

Return ONLY valid JSON:
{
  "actions": [
    { "label": "Versión en inglés", "prompt": "Same 3-slide carousel but in English", "icon": "language", "reason": "Reach English-speaking US market" },
    { "label": "Story de Instagram", "prompt": "Instagram story format (1080x1920) of slide 1 hook", "icon": "phone" },
    { "label": "Email de welcome", "prompt": "Welcome email matching the carousel's tone", "icon": "email" }
  ]
}

Be SPECIFIC to this brand and this prompt. Don't suggest generic stuff. Keep labels under 30 chars.`;

  try {
    console.log(`[Suggester] Calling Gemini 2.5 Pro for next action suggestions...`);
    const result = await callGeminiJSON<{ actions: NextAction[] }>(prompt, {
      model: GEMINI_PRO,
      temperature: 0.7,
      maxTokens: 1500,
    });

    const actions = result?.actions || [];
    console.log(`[Suggester] ${actions.length} suggestion(s) generated`);
    return actions.slice(0, 3); // Never more than 3
  } catch (e) {
    console.warn('[Suggester] Failed:', e instanceof Error ? e.message : e);
    return [];
  }
}
