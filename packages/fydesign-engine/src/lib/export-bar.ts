// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Export Bar — embedded in every generated HTML so the user can download     ║
// ║  PNG / JPG / WebP / SVG directly from the design preview.                   ║
// ║  Uses server-side Playwright rendering to avoid canvas tainting.            ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

const EXPORT_BAR_CSS = `
<style id="fyd-export-bar-css">
  #fyd-export-bar {
    position: fixed;
    bottom: 12px;
    right: 12px;
    z-index: 99999;
    display: flex;
    gap: 6px;
    padding: 6px 10px;
    background: rgba(0,0,0,0.78);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border-radius: 20px;
    border: 1px solid rgba(255,255,255,0.12);
    font-family: system-ui, -apple-system, sans-serif;
    opacity: 0.4;
    transition: opacity 0.25s;
    user-select: none;
  }
  #fyd-export-bar:hover { opacity: 1; }
  #fyd-export-bar button {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 5px 12px;
    border-radius: 14px;
    border: 1px solid rgba(255,255,255,0.15);
    background: rgba(255,255,255,0.06);
    color: #fff;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
    letter-spacing: 0.02em;
    transition: background 0.15s, border-color 0.15s;
    white-space: nowrap;
  }
  #fyd-export-bar button:disabled { opacity: 0.5; cursor: not-allowed; }
  #fyd-export-bar button:hover:not(:disabled) {
    background: rgba(255,255,255,0.16);
    border-color: rgba(255,255,255,0.3);
  }
  #fyd-export-bar button.fyd-export-dl { background: #3b82f6; border-color: #3b82f6; }
  #fyd-export-bar button.fyd-export-dl:hover:not(:disabled) { background: #2563eb; }
  #fyd-export-tooltip {
    position: fixed;
    bottom: 56px;
    right: 12px;
    z-index: 99999;
    background: #1f2937;
    color: #e5e7eb;
    padding: 8px 14px;
    border-radius: 10px;
    font-size: 12px;
    font-family: system-ui, -apple-system, sans-serif;
    pointer-events: none;
    opacity: 0;
    transform: translateY(6px);
    transition: all 0.2s;
  }
  #fyd-export-tooltip.show { opacity: 1; transform: translateY(0); }
</style>`;

const EXPORT_BAR_JS = `
<script id="fyd-export-bar-js">
(function() {
  if (document.getElementById('fyd-export-bar')) return;

  function notify(msg) {
    var el = document.getElementById('fyd-export-tooltip');
    if (!el) { el = document.createElement('div'); el.id = 'fyd-export-tooltip'; document.body.appendChild(el); }
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(el._t); el._t = setTimeout(function() { el.classList.remove('show'); }, 2200);
  }

  function triggerDownload(url, filename) {
    var a = document.createElement('a');
    a.download = filename;
    a.href = url;
    document.body.appendChild(a);
    a.click();
    setTimeout(function() { document.body.removeChild(a); URL.revokeObjectURL(url); }, 200);
  }

  function dataUriToBlob(dataUri) {
    var parts = dataUri.split(',');
    var mime = parts[0].match(/:(.*?);/)[1];
    var raw = atob(parts[1]);
    var bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return new Blob([bytes], { type: mime });
  }

  /** Call client-side renderer — fixes 500 errors from missing server browsers */
  async function exportClientSide(format) {
    if (format === 'html') {
      var w = Math.max(document.documentElement.scrollWidth, document.documentElement.offsetWidth, 1280);
      var h = Math.max(document.documentElement.scrollHeight, document.documentElement.offsetHeight, 720);
      var html = '<!DOCTYPE html>\\n' + document.documentElement.outerHTML;
      var blob = new Blob([html], {type: 'text/html'});
      triggerDownload(URL.createObjectURL(blob), 'fydesign_' + w + 'x' + h + '.html');
      notify('Exported HTML');
      return;
    }

    if (!window.htmlToImage) {
      notify('Loading exporter...');
      try {
        await new Promise(function(resolve, reject) {
          var script = document.createElement('script');
          script.src = 'https://cdn.jsdelivr.net/npm/html-to-image@1.11.11/dist/html-to-image.js';
          script.onload = resolve;
          script.onerror = function() { reject(new Error('Failed to load library from CDN.')); };
          document.head.appendChild(script);
          setTimeout(function() { reject(new Error('Timeout loading library.')); }, 8000);
        });
      } catch(e) {
        notify(e.message);
        return;
      }
    }

    notify('Rendering ' + format.toUpperCase() + '...');

    try {
      // Force CORS on all images just in case
      var imgs = document.querySelectorAll('img');
      for (var i = 0; i < imgs.length; i++) {
        if (imgs[i].src && !imgs[i].src.startsWith('data:') && !imgs[i].crossOrigin) {
          imgs[i].crossOrigin = 'anonymous';
        }
      }

      // ── Pre-render cleanup ──
      // 1. Hide export bar & tooltip so they don't appear in the image
      var exportBar = document.getElementById('fyd-export-bar');
      var tooltip = document.getElementById('fyd-export-tooltip');
      if (exportBar) exportBar.style.display = 'none';
      if (tooltip) tooltip.style.display = 'none';

      // 2. Force overflow:hidden on body+html to clip floating elements cleanly
      var prevBodyOverflow = document.body.style.overflow;
      var prevHtmlOverflow = document.documentElement.style.overflow;
      document.body.style.overflow = 'hidden';
      document.documentElement.style.overflow = 'hidden';

      var method = format === 'jpg' ? 'toJpeg' : format === 'svg' ? 'toSvg' : 'toPng';

      // Use exact design canvas dimensions (from body width/height set by Opus)
      var canvasW = parseInt(getComputedStyle(document.body).width) || document.body.scrollWidth;
      var canvasH = parseInt(getComputedStyle(document.body).height) || document.body.scrollHeight;

      var opts = {
        pixelRatio: 2,
        backgroundColor: format === 'jpg' ? '#ffffff' : null,
        imagePlaceholder: 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7',
        width: canvasW,
        height: canvasH,
        style: {
          overflow: 'hidden',
        },
        filter: function(node) {
          // Exclude export bar elements from the render
          if (!node || !node.id) return true;
          return node.id !== 'fyd-export-bar' && node.id !== 'fyd-export-tooltip' && node.id !== 'fyd-export-bar-css';
        },
      };

      var dataUrl = await Promise.race([
        window.htmlToImage[method](document.body, opts),
        new Promise(function(_, reject) { setTimeout(function() { reject(new Error('Render timed out after 15s. The design might be too complex.')); }, 15000); })
      ]);

      // ── Post-render restore ──
      document.body.style.overflow = prevBodyOverflow;
      document.documentElement.style.overflow = prevHtmlOverflow;
      if (exportBar) exportBar.style.display = '';
      if (tooltip) tooltip.style.display = '';

      if (format === 'copy') {
        var blob = dataUriToBlob(dataUrl);
        await navigator.clipboard.write([new ClipboardItem({'image/png': blob})]);
        notify('Copied to clipboard');
        return;
      }

      var w = canvasW;
      var h = canvasH;
      var blob = dataUriToBlob(dataUrl);
      triggerDownload(URL.createObjectURL(blob), 'fydesign_' + w + 'x' + h + '.' + format);
      notify('Exported ' + format.toUpperCase());
    } catch (e) {
      // Restore visibility on error
      var exportBar2 = document.getElementById('fyd-export-bar');
      var tooltip2 = document.getElementById('fyd-export-tooltip');
      if (exportBar2) exportBar2.style.display = '';
      if (tooltip2) tooltip2.style.display = '';
      document.body.style.overflow = '';
      document.documentElement.style.overflow = '';
      notify('Render failed: ' + e.message);
      console.error(e);
    }
  }

  window.fydExport = async function(format) {
    var btns = document.querySelectorAll('#fyd-export-bar button');
    btns.forEach(function(b) { b.disabled = true; });
    try {
      await exportClientSide(format);
    } catch(e) {
      notify('Export error: ' + e.message);
    } finally {
      btns.forEach(function(b) { b.disabled = false; });
    }
  };

  window.addEventListener('message', function(e) {
    if (e.data && e.data.type === 'FYD_EXPORT' && e.data.format) {
      window.fydExport(e.data.format);
    }
  });
})();
</script>`;

const EXPORT_BAR_HTML = `
<div id="fyd-export-bar">
  <button onclick="fydExport('html')" title="Download HTML">HTML</button>
  <button onclick="fydExport('png')" title="Download PNG">PNG</button>
  <button onclick="fydExport('jpg')" title="Download JPG">JPG</button>
  <button onclick="fydExport('svg')" title="Download SVG">SVG</button>
  <button onclick="fydExport('copy')" title="Copy to clipboard">Copy</button>
</div>`;

/** Full export bar block — inject before </body> */
const EXPORT_BLOCK = EXPORT_BAR_CSS + EXPORT_BAR_JS + EXPORT_BAR_HTML;

/**
 * Inject the export bar into an HTML string.
 * Safe to call on HTML that already has it (idempotent check in JS).
 */
export function injectExportBar(html: string): string {
  if (html.includes('id="fyd-export-bar"')) return html;
  if (html.includes('</body>')) {
    return html.replace('</body>', EXPORT_BLOCK + '\n</body>');
  }
  return html.replace('</html>', EXPORT_BLOCK + '\n</html>');
}

export { EXPORT_BLOCK };
