// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  MODULE 5: Self-Healing Loop                                               ║
// ║  If the generated code has errors, automatically re-prompt Claude.          ║
// ║  The user just sees "it takes a bit longer" but gets working code.         ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { extractCode, validateHTML } from './code-extractor';
import { callDeepSeekBuilder } from '@/lib/ai/deepseek-builder';

const MAX_HEAL_ATTEMPTS = 2;

interface HealResult {
  html: string;
  healed: boolean;
  attempts: number;
}

/**
 * Attempt to self-heal code that failed validation or has known issues.
 * Sends the error back to Claude for correction.
 */
export async function selfHeal(
  brokenHTML: string,
  errors: string[],
  originalBrief: string,
  width: number,
  height: number,
): Promise<HealResult> {
  let currentHTML = brokenHTML;
  let attempts = 0;
  let currentErrors = errors;

  for (let i = 0; i < MAX_HEAL_ATTEMPTS; i++) {
    const issues = currentErrors.length > 0 ? currentErrors : validateHTML(currentHTML);
    if (issues.length === 0) {
      return { html: currentHTML, healed: attempts > 0, attempts };
    }

    attempts++;
    console.log(`[SelfHealer] Attempt ${attempts}/${MAX_HEAL_ATTEMPTS} — fixing: ${issues.join(', ')}`);

    const healPrompt = `The following HTML code was generated for a ${width}×${height}px design but has errors.
Fix ALL issues and return ONLY the corrected, complete HTML code. No explanations.

ERRORS FOUND:
${issues.map(e => `- ${e}`).join('\n')}

ORIGINAL DESIGN BRIEF:
${originalBrief}

BROKEN CODE:
\`\`\`html
${currentHTML}
\`\`\`

RULES:
1. Return ONLY the fixed HTML code — no markdown, no explanations
2. The design must be ${width}×${height}px
3. Must include <!DOCTYPE html>, <html>, <head>, <style>, <body>
4. Must have proper CSS styling and visual design
5. Keep all the good parts of the original code, just fix the errors
6. Preserve the brand's color identity — do NOT change background/accent colors

Output the complete fixed HTML now:`;

    try {
      // Sonnet 4.6 fixes HTML — follows Opus critique instructions
      const raw = await callDeepSeekBuilder(healPrompt, {
        temperature: 0.15,
        maxTokens: 28000,
      });
      const fixedCode = extractCode(raw);

      if (fixedCode.length > currentHTML.length * 0.5) {
        currentHTML = fixedCode;
        currentErrors = []; // re-validate fresh on next loop
      } else {
        console.warn('[SelfHealer] Fixed code too short, keeping original');
        break;
      }
    } catch (e) {
      console.error('[SelfHealer] Heal attempt failed:', e);
      break;
    }
  }

  return { html: currentHTML, healed: attempts > 0, attempts };
}

/**
 * Wrap HTML with error-catching script for client-side error detection.
 * Injects a tiny script that catches JS errors and communicates them
 * back to the parent via postMessage.
 */
export function wrapWithErrorCatcher(html: string): string {
  let output = html;

  if (!/<meta\s+name=["']viewport["']/i.test(output)) {
    const viewport = '<meta name="viewport" content="width=device-width, initial-scale=1.0">';
    if (/<head[^>]*>/i.test(output)) {
      output = output.replace(/<head([^>]*)>/i, `<head$1>\n${viewport}`);
    }
  }

  if (output.includes('data-fydesign-runtime')) return output;

  const runtimeScript = `
<script data-fydesign-runtime>
(function () {
  if (window.fydesign) return;

  function post(type, payload) {
    try { window.parent && window.parent.postMessage(Object.assign({ type: type }, payload || {}), '*'); } catch (_) {}
  }

  window.fydesign = {
    async ai(prompt, options) {
      if (!prompt || !String(prompt).trim()) throw new Error('Prompt is required');
      const res = await fetch('/api/prototype/anthropic', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(Object.assign({ prompt: String(prompt) }, options || {}))
      });
      const data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.error || 'AI call failed');
      return data.response || '';
    },
    speak(text, options) {
      return new Promise(function (resolve, reject) {
        if (!('speechSynthesis' in window) || !('SpeechSynthesisUtterance' in window)) {
          reject(new Error('Speech synthesis is not available in this browser'));
          return;
        }
        var utterance = new SpeechSynthesisUtterance(String(text || ''));
        if (options && options.lang) utterance.lang = options.lang;
        if (options && options.rate) utterance.rate = options.rate;
        if (options && options.pitch) utterance.pitch = options.pitch;
        utterance.onend = function () { resolve(true); };
        utterance.onerror = function (event) { reject(event.error || new Error('Speech synthesis failed')); };
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(utterance);
      });
    },
    listen(options) {
      return new Promise(function (resolve, reject) {
        var Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!Recognition) {
          reject(new Error('Speech recognition is not available in this browser'));
          return;
        }
        var recognition = new Recognition();
        recognition.lang = (options && options.lang) || 'en-US';
        recognition.interimResults = !!(options && options.interimResults);
        recognition.maxAlternatives = 1;
        recognition.onresult = function (event) {
          var transcript = Array.from(event.results).map(function (r) { return r[0] && r[0].transcript; }).filter(Boolean).join(' ');
          resolve(transcript);
        };
        recognition.onerror = function (event) { reject(event.error || new Error('Speech recognition failed')); };
        recognition.start();
      });
    },
    save(key, value) {
      localStorage.setItem('fydesign-artifact:' + key, JSON.stringify(value));
      return value;
    },
    load(key, fallback) {
      try {
        var raw = localStorage.getItem('fydesign-artifact:' + key);
        return raw ? JSON.parse(raw) : fallback;
      } catch (_) {
        return fallback;
      }
    },
    report(error) {
      post('DESIGN_ERROR', { error: {
        message: error && error.message ? error.message : String(error),
        stack: error && error.stack ? error.stack : undefined
      } });
    }
  };

  window.onerror = function (msg, url, line, col, error) {
    post('DESIGN_ERROR', { error: { message: msg, line: line, col: col, stack: error && error.stack } });
    return true;
  };
  window.addEventListener('unhandledrejection', function (event) {
    window.fydesign.report(event.reason || 'Unhandled promise rejection');
  });
  window.addEventListener('load', function () {
    post('DESIGN_READY');
  });
})();
</script>`;

  if (output.includes('</body>')) {
    return output.replace('</body>', `${runtimeScript}\n</body>`);
  }
  return output + runtimeScript;
}
