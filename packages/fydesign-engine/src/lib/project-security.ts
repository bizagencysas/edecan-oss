const MAX_HTML_BYTES = 5 * 1024 * 1024;

/** Make a generated HTML artifact safe to download and open outside Edecán. */
export function artifactSafeHtml(raw: string): string {
  let html = raw
    .replace(/<script\b[\s\S]*?<\/script>/gi, '')
    .replace(/<(?:iframe|object|embed)\b[\s\S]*?<\/(?:iframe|object|embed)>/gi, '')
    .replace(/<(?:iframe|object|embed)\b[^>]*\/?>/gi, '')
    .replace(/\s+on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, '')
    .replace(/javascript\s*:/gi, 'about:blank#blocked-')
    .replace(/<link\b[^>]*>/gi, '')
    .replace(/<base\b[^>]*>/gi, '')
    .replace(/<meta\b[^>]*http-equiv\s*=\s*["']?refresh["']?[^>]*>/gi, '')
    .replace(/@import\s+(?:url\()?\s*["']?https?:[^;]+;?/gi, '')
    .replace(
      /\s+(src|href|poster|action|formaction|srcset)\s*=\s*(["'])\s*https?:[\s\S]*?\2/gi,
      ' $1="about:blank#blocked-external-resource"',
    )
    .replace(/url\(\s*(["']?)https?:[\s\S]*?\1\s*\)/gi, 'none')
    .replace(/<form\b[^>]*>/gi, '<div data-fydesign-form>')
    .replace(/<\/form>/gi, '</div>');
  const csp = '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src \'none\'; frame-src \'none\'; object-src \'none\'; base-uri \'none\'; form-action \'none\'">';
  html = /<head\b[^>]*>/i.test(html)
    ? html.replace(/<head\b([^>]*)>/i, `<head$1>${csp}`)
    : `${csp}${html}`;
  if (Buffer.byteLength(html) > MAX_HTML_BYTES) {
    throw new Error('El artefacto HTML supera el límite seguro de 5 MB.');
  }
  return html;
}

export { MAX_HTML_BYTES };
