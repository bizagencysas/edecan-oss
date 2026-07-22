// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  AST Extractor — Component names, props, JSX patterns, classification       ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

export interface PropPattern {
  name: string;
  type: string;
  required: boolean;
}

export interface JSXPattern {
  className: string;
  tailwindClasses: string[];
}

export interface ImportInfo {
  source: string;
  names: string[];
  isDefault: boolean;
}

export type ComponentClass =
  | 'hero' | 'nav' | 'sidebar' | 'dashboard' | 'card' | 'table'
  | 'chart' | 'form' | 'modal' | 'pricing' | 'testimonial'
  | 'carousel' | 'phone-mockup' | 'app-shell' | 'unknown';

const COMPONENT_CLASS_KEYWORDS: Record<ComponentClass, string[]> = {
  hero: ['hero', 'banner', 'header', 'heading', 'jumbotron'],
  nav: ['nav', 'navbar', 'navigation', 'menu', 'topbar', 'sidebar'],
  sidebar: ['sidebar', 'side-nav', 'drawer', 'aside'],
  dashboard: ['dashboard', 'analytics', 'overview', 'stats', 'metrics'],
  card: ['card', 'tile', 'panel', 'box', 'widget'],
  table: ['table', 'datatable', 'grid', 'rows', 'columns'],
  chart: ['chart', 'graph', 'plot', 'sparkline', 'bar', 'line', 'pie'],
  form: ['form', 'input', 'select', 'checkbox', 'radio', 'textarea', 'field'],
  modal: ['modal', 'dialog', 'popup', 'overlay', 'drawer', 'sheet'],
  pricing: ['pricing', 'price', 'plan', 'subscription', 'tier'],
  testimonial: ['testimonial', 'review', 'quote', 'customer', 'social-proof'],
  carousel: ['carousel', 'slider', 'swiper', 'slides', 'gallery'],
  'phone-mockup': ['mockup', 'device', 'phone', 'iphone', 'android', 'screen', 'frame'],
  'app-shell': ['layout', 'shell', 'app', 'root', 'wrapper'],
  unknown: [],
};

export function extractComponentNames(_filePath: string, content: string): string[] {
  const names: string[] = [];
  // export function ComponentName
  const funcRegex = /export\s+(?:default\s+)?function\s+([A-Z][A-Za-z0-9_]*)/g;
  let match: RegExpExecArray | null;
  while ((match = funcRegex.exec(content)) !== null) {
    names.push(match[1]);
  }
  // export const ComponentName
  const constRegex = /export\s+const\s+([A-Z][A-Za-z0-9_]*)\s*[:=]/g;
  while ((match = constRegex.exec(content)) !== null) {
    names.push(match[1]);
  }
  // export default function ComponentName (anonymous handled by file name)
  const defaultFuncRegex = /export\s+default\s+(?:function|class)\s+([A-Z][A-Za-z0-9_]*)/g;
  while ((match = defaultFuncRegex.exec(content)) !== null) {
    names.push(match[1]);
  }
  return [...new Set(names)];
}

export function extractPropsPatterns(_filePath: string, content: string): PropPattern[] {
  const patterns: PropPattern[] = [];

  // interface Props { name: type; ... }
  const ifaceRegex = /(?:interface|type)\s+(\w*Props\w*)\s*[={]\s*\{([^}]+)\}/g;
  let match: RegExpExecArray | null;
  while ((match = ifaceRegex.exec(content)) !== null) {
    const body = match[2];
    const propRegex = /(\w+)(\?)?\s*:\s*([^;]+)/g;
    let propMatch;
    while ((propMatch = propRegex.exec(body)) !== null) {
      patterns.push({
        name: propMatch[1],
        type: propMatch[3].trim(),
        required: !propMatch[2],
      });
    }
  }

  return patterns;
}

export function extractJSXPatterns(_filePath: string, content: string): JSXPattern[] {
  const patterns: JSXPattern[] = [];
  const classRegex = /className\s*=\s*(?:["'`]\s*\{?|\{["'`]?)([^"'`}]+)(?:["'`]\s*\}?|\}["'`]?)/g;
  let match;
  while ((match = classRegex.exec(content)) !== null) {
    const classStr = match[1];
    const classes = classStr.split(/\s+/).filter(Boolean);
    patterns.push({ className: classStr, tailwindClasses: classes });
  }
  return patterns;
}

export function classifyComponent(filePath: string, content: string): ComponentClass {
  const haystack = `${filePath} ${content.slice(0, 3000)}`.toLowerCase();
  for (const [classification, keywords] of Object.entries(COMPONENT_CLASS_KEYWORDS)) {
    if (classification === 'unknown') continue;
    const score = keywords.filter((kw) => haystack.includes(kw)).length;
    if (score >= 2) return classification as ComponentClass;
  }
  return 'unknown';
}

export function extractImports(_filePath: string, content: string): ImportInfo[] {
  const imports: ImportInfo[] = [];
  // import { X, Y } from 'source'
  const namedRegex = /import\s+\{([^}]+)\}\s+from\s+['"]([^'"]+)['"]/g;
  let match: RegExpExecArray | null;
  while ((match = namedRegex.exec(content)) !== null) {
    imports.push({
      source: match[2],
      names: match[1].split(',').map((n) => n.trim()).filter(Boolean),
      isDefault: false,
    });
  }
  // import X from 'source'
  const defaultRegex = /import\s+(\w+)\s+from\s+['"]([^'"]+)['"]/g;
  let defMatch: RegExpExecArray | null;
  while ((defMatch = defaultRegex.exec(content)) !== null) {
    const name = defMatch[1];
    const source = defMatch[2];
    if (name[0] === name[0].toUpperCase() || imports.every((i) => i.source !== source)) {
      imports.push({ source, names: [name], isDefault: true });
    }
  }
  return imports;
}
