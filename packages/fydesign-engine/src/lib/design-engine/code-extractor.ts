// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  MODULE 3: Code Extractor                                                  ║
// ║  LLMs are "charlatanes" — they add explanatory text around code blocks.    ║
// ║  This module strips EVERYTHING except the pure HTML/CSS/JS code.           ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

/**
 * Extract clean code from an LLM response.
 * Handles multiple formats:
 * 1. ```html ... ``` blocks
 * 2. ```tsx/jsx ... ``` blocks
 * 3. Raw HTML (starts with <!DOCTYPE or <html)
 * 4. Mixed text with embedded code blocks
 */
export function extractCode(raw: string): string {
  if (!raw || !raw.trim()) return '';

  const trimmed = raw.trim();

  // Strategy 1: Find the LARGEST ```html or ```htm block
  const htmlBlocks = findCodeBlocks(trimmed, ['html', 'htm']);
  if (htmlBlocks.length > 0) {
    return scrubFences(htmlBlocks.sort((a, b) => b.length - a.length)[0]);
  }

  // Strategy 2: Find ```tsx, ```jsx, ```css blocks
  const otherBlocks = findCodeBlocks(trimmed, ['tsx', 'jsx', 'css', 'svg']);
  if (otherBlocks.length > 0) {
    return scrubFences(otherBlocks.sort((a, b) => b.length - a.length)[0]);
  }

  // Strategy 3: Find any ``` block
  const anyBlock = findCodeBlocks(trimmed, []);
  if (anyBlock.length > 0) {
    return scrubFences(anyBlock.sort((a, b) => b.length - a.length)[0]);
  }

  // Strategy 4: If it looks like raw HTML (starts with doctype or html tag)
  if (/^\s*<!DOCTYPE/i.test(trimmed) || /^\s*<html/i.test(trimmed)) {
    return scrubFences(trimmed);
  }

  // Strategy 5: Find HTML content anywhere in the text
  const htmlMatch = trimmed.match(/(<!DOCTYPE html[\s\S]*<\/html>)/i);
  if (htmlMatch) return scrubFences(htmlMatch[1]);

  // Strategy 6: Find any content starting with <html
  const htmlStart = trimmed.match(/(<html[\s\S]*<\/html>)/i);
  if (htmlStart) return scrubFences(htmlStart[1]);

  // Strategy 7 (TRUNCATED RESPONSES): grab from <!DOCTYPE or <html to the end,
  // even if there's no closing tag. The self-healer will close it.
  const partial = trimmed.match(/(<!DOCTYPE html[\s\S]*$|<html[\s\S]*$)/i);
  if (partial) return scrubFences(partial[1]);

  // Strategy 8: grab from first <div ...> onward
  const divStart = trimmed.match(/(<div[\s\S]*$)/i);
  if (divStart) return scrubFences(divStart[1]);

  // Last resort: aggressively strip any markdown fences and return
  return scrubFences(trimmed);
}

/**
 * Strip stray markdown fences, language hints, and orphan tag fragments
 * that LLMs sometimes leak even when told not to.
 * We never want literal "```html" or stray "l" characters rendering inside an iframe.
 */
function scrubFences(s: string): string {
  let result = s
    // Remove markdown fences
    .replace(/```html?\s*/gi, '')
    .replace(/```\s*$/gm, '')
    .replace(/^```[\w]*\s*$/gm, '')
    // Strip orphan opening tag fragments at the very start (common truncation artifact)
    // e.g. "li>" or "iv>" or "l>" left over from a truncated </div> or </li>
    .replace(/^[a-z\/]{1,4}>\s*/i, '')
    // Strip orphan closing tag fragment at end (e.g. "</di" or "<li" without closing)
    .replace(/<[a-z]{1,4}$/i, '')
    // Strip stray single characters at the very start that aren't HTML (e.g. lone "l")
    .replace(/^([a-zA-Z])\s*(?=<)/g, '')
    // Strip any text before the first actual HTML tag (explanatory preamble)
    .replace(/^[^<]*?(?=<!DOCTYPE|<html|<head|<div|<section|<style)/i, '')
    .trim();

  // Post-scrub: if the result starts with a random short text followed by HTML, strip the text
  const preHtmlMatch = result.match(/^([^<]{1,20})\s*(<(?:!DOCTYPE|html|head|div|section|style))/i);
  if (preHtmlMatch) {
    // Only strip if the pre-text doesn't look like valid HTML content
    const preText = preHtmlMatch[1].trim();
    if (!/^</.test(preText) && preText.length < 10) {
      result = result.slice(preHtmlMatch[1].length).trim();
    }
  }

  return result;
}

/**
 * Find all code blocks with optional language filter.
 */
function findCodeBlocks(text: string, languages: string[]): string[] {
  const blocks: string[] = [];

  if (languages.length === 0) {
    // Match any ``` block
    const regex = /```\w*\s*\n?([\s\S]*?)```/g;
    let match;
    while ((match = regex.exec(text)) !== null) {
      const code = match[1].trim();
      if (code.length > 50) blocks.push(code); // Ignore tiny snippets
    }
  } else {
    // Match blocks with specific language tags
    for (const lang of languages) {
      const regex = new RegExp('```' + lang + '\\s*\\n?([\\s\\S]*?)```', 'gi');
      let match;
      while ((match = regex.exec(text)) !== null) {
        const code = match[1].trim();
        if (code.length > 50) blocks.push(code);
      }
    }
  }

  return blocks;
}

/**
 * Validate that the extracted code is a proper HTML document.
 * Returns issues found (empty array = valid).
 */
export function validateHTML(html: string): string[] {
  const issues: string[] = [];

  if (!html || html.length < 50) {
    issues.push('Code is too short to be a valid design');
    return issues;
  }

  if (!/</i.test(html) && !/<body/i.test(html) && !/<div/i.test(html)) {
    issues.push('No HTML structure found');
  }

  if (!/<style/i.test(html) && !/style="/i.test(html)) {
    issues.push('No CSS styling found — design will look unstyled');
  }

  if (/^(Sure|Here|I'll|Let me|Of course|Certainly)/i.test(html.trim())) {
    issues.push('Response starts with conversational text, not code');
  }

  if (/\blorem ipsum\b/i.test(html)
    || /\bfeature (one|two|three)\b/i.test(html)
    || /\bTODO\b/.test(html)
    || />\s*placeholder(?: text)?\s*</i.test(html)) {
    issues.push('Design contains filler copy or TODO placeholders');
  }

  const unresolvedPlaceholders = (html.match(/\{\{[^}]+\}\}/g) || [])
    .filter((token) => !/^\{\{BRAND_(LOGO|IMAGE_\d+)\}\}$/.test(token));
  if (unresolvedPlaceholders.length > 0) {
    issues.push('Design contains unresolved template placeholders');
  }

  const bodyTag = html.match(/<body\b[^>]*>/i)?.[0] || '';
  const hasBodyResetInCss = /body\s*\{[\s\S]*?margin\s*:\s*0[\s\S]*?overflow\s*:\s*hidden/i.test(html)
    || /body\s*\{[\s\S]*?overflow\s*:\s*hidden[\s\S]*?margin\s*:\s*0/i.test(html);
  if (bodyTag && !hasBodyResetInCss && (!/overflow\s*:\s*hidden/i.test(bodyTag) || !/margin\s*:\s*0/i.test(bodyTag))) {
    issues.push('Body is missing fixed artifact canvas reset styles');
  }

  // TRUNCATION CHECK: if response was cut off mid-tag or has unbalanced tags
  if (/```/.test(html)) {
    issues.push('Response contains literal markdown fences (```) — truncated or malformed');
  }
  const openHtml = (html.match(/<html\b/gi) || []).length;
  const closeHtml = (html.match(/<\/html>/gi) || []).length;
  if (openHtml > 0 && closeHtml < openHtml) {
    issues.push('HTML element opened but never closed — response was truncated');
  }
  const openBody = (html.match(/<body\b/gi) || []).length;
  const closeBody = (html.match(/<\/body>/gi) || []).length;
  if (openBody > 0 && closeBody < openBody) {
    issues.push('Body element opened but never closed — response was truncated');
  }

  return issues;
}
