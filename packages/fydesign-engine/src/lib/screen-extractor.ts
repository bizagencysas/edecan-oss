// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  fydesign — Screen Extractor                                               ║
// ║  Parses TSX/JSX files to extract texts, components, icons, layout type     ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { ScreenInfo, LayoutType, AppAnalysis } from './types';

let screenCounter = 0;

/**
 * Extract screen information from a downloaded file
 */
export function extractScreenInfo(
  filePath: string,
  code: string,
  framework: AppAnalysis['framework'],
): ScreenInfo | null {
  // Skip layout files, test files
  if (filePath.includes('_layout') || filePath.includes('.test.')) return null;

  const fileName = filePath.split('/').pop() || '';
  const screenName = inferScreenName(fileName, filePath);
  const route = inferRoute(filePath, framework);

  const texts = extractTexts(code);
  const components = extractComponents(code);
  const icons = extractIcons(code);
  const estimatedLayout = inferLayout(code, components, fileName);
  const complexity = inferComplexity(code);

  screenCounter++;

  return {
    id: `screen-${screenCounter}-${fileName.replace(/\.\w+$/, '')}`,
    fileName,
    filePath,
    screenName,
    route,
    texts,
    components,
    icons,
    estimatedLayout,
    complexity,
    rawCode: code,
    structuralSnippet: extractStructuralSnippet(code),
  };
}

/* ── Structure Snippet Extraction ─────────────────────────────────────────── */

function extractStructuralSnippet(code: string): string {
  // Try to find the main return statement of the component
  const returnMatch = code.match(/return\s*\(\s*(<[\s\S]*?)\);/);
  if (returnMatch && returnMatch[1]) {
    // Strip out heavy text content and deep nested noise, keeping just the structure
    let snippet = returnMatch[1]
      .replace(/>[^<]{20,}</g, '>...<') // Replace long text nodes
      .trim();
    return snippet.slice(0, 500) + (snippet.length > 500 ? '...' : '');
  }

  // Fallback: just grab the first chunk of JSX/HTML-like syntax
  const htmlMatch = code.match(/<[A-Z][a-zA-Z0-9]*[\s\S]*?(?:>|\/>)/);
  if (htmlMatch && htmlMatch[0]) {
    return htmlMatch[0].slice(0, 500) + (htmlMatch[0].length > 500 ? '...' : '');
  }

  return '';
}

/* ── Screen Name Inference ────────────────────────────────────────────────── */

function inferScreenName(fileName: string, filePath: string): string {
  let name = fileName.replace(/\.(tsx|jsx|ts|js)$/, '');

  // Handle index files — use parent directory name
  if (name === 'index' || name === 'page') {
    const parts = filePath.split('/');
    const parentDir = parts[parts.length - 2] || 'Home';
    // Clean up directory names like (tabs), (auth)
    name = parentDir.replace(/[()]/g, '');
  }

  // Clean up common patterns
  const nameMap: Record<string, string> = {
    'index': 'Dashboard',
    'tabs': 'Dashboard',
    'home': 'Home',
    'mercado': 'Marketplace',
    'portafolio': 'Portfolio',
    'wallet': 'Wallet',
    'ajustes': 'Settings',
    'perfil': 'Profile',
    'verificacion': 'Verification',
    'notificaciones': 'Notifications',
    'login': 'Login',
    'register': 'Register',
    'registro': 'Register',
    'ahorros': 'Savings',
    'impuestos': 'Taxes',
    'actividad': 'Activity',
    'transacciones': 'Transactions',
    'pagos': 'Payments',
    'fy-advisor': 'AI Advisor',
    'auto-invest': 'Auto Invest',
    'invertir': 'Invest',
    'comparador': 'Compare',
    'challenges': 'Challenges',
    'comunidad': 'Community',
    'prediccion': 'Predictions',
    'salud-portafolio': 'Portfolio Health',
    'trust-score': 'Trust Score',
    'metodos-pago': 'Payment Methods',
    'crear-proyecto': 'Create Project',
    'mercado-secundario': 'Secondary Market',
    'exit-planner': 'Exit Planner',
    'digest-mensual': 'Monthly Digest',
    'capsula-del-tiempo': 'Time Capsule',
    'inversion-exitosa': 'Success',
  };

  const cleanName = name.toLowerCase().replace(/[_\s]+/g, '-');
  if (nameMap[cleanName]) return nameMap[cleanName];

  // Title case the name
  return name
    .replace(/[-_]/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function inferRoute(
  filePath: string,
  framework: AppAnalysis['framework'],
): string {
  if (framework === 'expo') {
    // Extract route from Expo file-based routing
    let route = filePath
      .replace(/^(apps\/mobile\/)?app/, '')
      .replace(/\.(tsx|jsx|ts|js)$/, '')
      .replace(/\/index$/, '/');

    if (!route.startsWith('/')) route = '/' + route;
    return route;
  }

  return '/' + filePath.split('/').pop()?.replace(/\.\w+$/, '') || '';
}

/* ── Text Extraction ──────────────────────────────────────────────────────── */

function extractTexts(code: string): string[] {
  const texts = new Set<string>();

  // 1. JSX text content: >Some text<
  const jsxTextRegex = />([^<>{}\n]+)</g;
  let match;
  while ((match = jsxTextRegex.exec(code)) !== null) {
    const text = match[1].trim();
    if (text.length > 1 && text.length < 100 && !isCodeToken(text)) {
      texts.add(text);
    }
  }

  // 2. String literals in JSX attributes: title="..." placeholder="..."
  const attrRegex =
    /(?:title|label|placeholder|message|text|description|heading|subtitle|header|buttonText|action)=["'`]([^"'`]+)["'`]/gi;
  while ((match = attrRegex.exec(code)) !== null) {
    const text = match[1].trim();
    if (text.length > 1 && text.length < 100) {
      texts.add(text);
    }
  }

  // 3. Alert.alert strings
  const alertRegex = /Alert\.alert\(\s*['"`]([^'"`]+)['"`]\s*,\s*['"`]([^'"`]+)['"`]/g;
  while ((match = alertRegex.exec(code)) !== null) {
    texts.add(match[1].trim());
    texts.add(match[2].trim());
  }

  // Filter out noise
  return Array.from(texts).filter(
    (t) =>
      !t.startsWith('{') &&
      !t.startsWith('//') &&
      !t.match(/^[a-z]+\.[a-z]+/i) && // method calls
      !t.match(/^\d+$/) && // pure numbers
      !t.match(/^[#$@]/), // color codes, special chars
  );
}

function isCodeToken(text: string): boolean {
  const codePatterns = [
    /^(const|let|var|function|return|import|export|if|else|switch|case)/,
    /^[{}()[\]]/,
    /^\s*$/,
    /^(true|false|null|undefined|NaN)$/,
    /^[a-z]+[A-Z]/, // camelCase
    /^\.\.\./,
  ];
  return codePatterns.some((p) => p.test(text));
}

/* ── Component Extraction ─────────────────────────────────────────────────── */

function extractComponents(code: string): string[] {
  const components = new Set<string>();

  // Import statements
  const importRegex =
    /import\s+\{([^}]+)\}\s+from\s+['"][^'"]+['"]/g;
  let match;
  while ((match = importRegex.exec(code)) !== null) {
    const imports = match[1].split(',').map((s) => s.trim());
    imports.forEach((imp) => {
      // Only React components start with uppercase
      const name = imp.split(' as ').pop()?.trim() || '';
      if (name && /^[A-Z]/.test(name)) {
        components.add(name);
      }
    });
  }

  // Default imports
  const defaultImportRegex =
    /import\s+([A-Z][a-zA-Z]+)\s+from\s+['"][^'"]+['"]/g;
  while ((match = defaultImportRegex.exec(code)) !== null) {
    components.add(match[1]);
  }

  return Array.from(components);
}

/* ── Icon Extraction ──────────────────────────────────────────────────────── */

function extractIcons(code: string): string[] {
  const icons = new Set<string>();

  // Ionicons: name="icon-name"
  const ionRegex = /name=["']([a-z][\w-]+)["']/g;
  let match;
  while ((match = ionRegex.exec(code)) !== null) {
    icons.add(match[1]);
  }

  // MaterialIcons, FontAwesome, etc.
  const iconNameRegex =
    /(?:icon|iconName|name)=["']([a-z][\w-]+)["']/g;
  while ((match = iconNameRegex.exec(code)) !== null) {
    icons.add(match[1]);
  }

  return Array.from(icons);
}

/* ── Layout Type Inference ────────────────────────────────────────────────── */

function inferLayout(
  code: string,
  components: string[],
  fileName: string,
): LayoutType {
  const codeLower = code.toLowerCase();
  const fileNameLower = fileName.toLowerCase();

  // Auth screens
  if (
    fileNameLower.match(/(login|signup|register|auth|registro|verificacion)/) ||
    (codeLower.includes('password') && codeLower.includes('textinput'))
  ) {
    return 'auth';
  }

  // Wallet
  if (fileNameLower.includes('wallet') || fileNameLower.includes('billetera')) {
    return 'wallet';
  }

  // Profile / Settings
  if (fileNameLower.match(/(perfil|profile|settings|ajustes)/)) {
    return fileNameLower.match(/(settings|ajustes)/) ? 'settings' : 'profile';
  }

  // Marketplace / List
  if (
    fileNameLower.match(/(mercado|market|explore|discover)/) ||
    components.some((c) => c.includes('FlatList'))
  ) {
    return 'marketplace';
  }

  // Dashboard (main screen with multiple sections)
  if (
    fileNameLower === 'index.tsx' ||
    (components.length > 5 && codeLower.includes('scrollview'))
  ) {
    return 'dashboard';
  }

  // Charts
  if (
    codeLower.includes('chart') ||
    codeLower.includes('graph') ||
    fileNameLower.match(/(prediccion|analytics|stats)/)
  ) {
    return 'chart';
  }

  // Forms
  if (
    codeLower.includes('textinput') &&
    (codeLower.match(/textinput/gi) || []).length > 2
  ) {
    return 'form';
  }

  // Detail
  if (codeLower.includes('scrollview') && codeLower.includes('image')) {
    return 'detail';
  }

  // Onboarding
  if (fileNameLower.match(/(onboard|welcome|intro|tour)/)) {
    return 'onboarding';
  }

  return 'generic';
}

/* ── Complexity ───────────────────────────────────────────────────────────── */

function inferComplexity(
  code: string,
): ScreenInfo['complexity'] {
  const lines = code.split('\n').length;
  if (lines > 400) return 'complex';
  if (lines > 150) return 'medium';
  return 'simple';
}
