// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  fydesign — Core Type Definitions                                          ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

/* ── App Analysis (output of GitHub scan) ─────────────────────────────────── */

export interface AppAnalysis {
  repoName: string;
  repoFullName: string;
  appName: string;
  description: string;
  framework: 'expo' | 'react-native-cli' | 'nextjs' | 'unknown';
  screens: ScreenInfo[];
  theme: ThemeInfo;
  packageJson?: Record<string, unknown>;
  brandIntelligence?: {
    brandVoice?: string;
    targetAudience?: string;
    keyFeatures?: string[];
    marketingAngles?: string[];
    designStyle?: string;
  };
}

export interface ScreenInfo {
  id: string;
  fileName: string;
  filePath: string;
  screenName: string;
  route: string;
  texts: string[];
  components: string[];
  icons: string[];
  estimatedLayout: LayoutType;
  complexity: 'simple' | 'medium' | 'complex';
  rawCode?: string;
  structuralSnippet?: string;
}

export type LayoutType =
  | 'dashboard'
  | 'list'
  | 'detail'
  | 'form'
  | 'auth'
  | 'settings'
  | 'chart'
  | 'wallet'
  | 'profile'
  | 'onboarding'
  | 'marketplace'
  | 'generic';

export interface ThemeInfo {
  primaryColor: string;
  secondaryColor: string;
  backgroundColor: string;
  darkBackgroundColor: string;
  textColor: string;
  darkTextColor: string;
  accentColors: string[];
  successColor: string;
  dangerColor: string;
  warningColor: string;
  brandName: string;
  hasDarkMode: boolean;
  borderRadius: number;
}

/* ── Mockup Configuration ─────────────────────────────────────────────────── */

export interface MockupProject {
  id: string;
  name: string;
  repoUrl: string;
  analysis: AppAnalysis;
  mockups: MockupData[];
  createdAt: string;
  updatedAt: string;
}

export interface MockupConfig {
  selectedScreens: string[]; // screen IDs
  languages: Language[];
  platform: Platform;
  device: DeviceType;
  template: TemplateType;
  darkMode: boolean;
  customBackground?: string;
}

export type Language = 'en' | 'es' | 'pt' | 'fr';
export type Platform = 'ios' | 'android' | 'both';

export type DeviceType =
  | 'iphone-16-pro'
  | 'iphone-16-pro-max'
  | 'iphone-15-pro'
  | 'iphone-se'
  | 'pixel-9'
  | 'galaxy-s24';

export type TemplateType =
  | 'gradient-hero'
  | 'split-screen'
  | 'feature-highlight'
  | 'full-bleed'
  | 'minimal-clean'
  | 'bold-statement'
  | 'dark-luxury'
  | 'glass-premium';

/* ── Generated Mockup Data ────────────────────────────────────────────────── */

export interface MockupData {
  id: string;
  screen: ScreenInfo;
  language: Language;
  headline: string;
  subtitle: string;
  template: TemplateType;
  device: DeviceType;
  platform: Platform;
  backgroundColor: string;
  gradientColors: [string, string];
  textColor: string;
  order: number;
  darkMode: boolean;
}

export interface GeneratedCopy {
  screenId: string;
  language: Language;
  headline: string;
  subtitle: string;
  featureBullets?: string[];
}

/* ── Device Frame Specs ───────────────────────────────────────────────────── */

export interface DeviceSpec {
  id: DeviceType;
  name: string;
  platform: Platform;
  screenWidth: number;
  screenHeight: number;
  exportWidth: number;
  exportHeight: number;
  bezelRadius: number;
  frameColor: string;
  notchType: 'dynamic-island' | 'notch' | 'punch-hole' | 'none';
}

export const DEVICE_SPECS: Record<DeviceType, DeviceSpec> = {
  'iphone-16-pro': {
    id: 'iphone-16-pro',
    name: 'iPhone 16 Pro',
    platform: 'ios',
    screenWidth: 393,
    screenHeight: 852,
    exportWidth: 1290,
    exportHeight: 2796,
    bezelRadius: 55,
    frameColor: '#1a1a1a',
    notchType: 'dynamic-island',
  },
  'iphone-16-pro-max': {
    id: 'iphone-16-pro-max',
    name: 'iPhone 16 Pro Max',
    platform: 'ios',
    screenWidth: 430,
    screenHeight: 932,
    exportWidth: 1290,
    exportHeight: 2796,
    bezelRadius: 55,
    frameColor: '#1a1a1a',
    notchType: 'dynamic-island',
  },
  'iphone-15-pro': {
    id: 'iphone-15-pro',
    name: 'iPhone 15 Pro',
    platform: 'ios',
    screenWidth: 393,
    screenHeight: 852,
    exportWidth: 1290,
    exportHeight: 2796,
    bezelRadius: 55,
    frameColor: '#2a2a2e',
    notchType: 'dynamic-island',
  },
  'iphone-se': {
    id: 'iphone-se',
    name: 'iPhone SE',
    platform: 'ios',
    screenWidth: 375,
    screenHeight: 667,
    exportWidth: 1242,
    exportHeight: 2208,
    bezelRadius: 0,
    frameColor: '#e0e0e0',
    notchType: 'none',
  },
  'pixel-9': {
    id: 'pixel-9',
    name: 'Pixel 9',
    platform: 'android',
    screenWidth: 412,
    screenHeight: 915,
    exportWidth: 1080,
    exportHeight: 2400,
    bezelRadius: 48,
    frameColor: '#202124',
    notchType: 'punch-hole',
  },
  'galaxy-s24': {
    id: 'galaxy-s24',
    name: 'Galaxy S24',
    platform: 'android',
    screenWidth: 360,
    screenHeight: 780,
    exportWidth: 1080,
    exportHeight: 2340,
    bezelRadius: 42,
    frameColor: '#1a1a1a',
    notchType: 'punch-hole',
  },
};

/* ── Template Definitions ─────────────────────────────────────────────────── */

export interface TemplateSpec {
  id: TemplateType;
  name: string;
  description: string;
  textPosition: 'top' | 'bottom' | 'left' | 'right' | 'overlay';
  devicePosition: 'center' | 'bottom' | 'right' | 'left' | 'full';
  deviceScale: number;
}

export const TEMPLATE_SPECS: Record<TemplateType, TemplateSpec> = {
  'gradient-hero': {
    id: 'gradient-hero',
    name: 'Gradient Hero',
    description: 'Bold gradient background with headline on top',
    textPosition: 'top',
    devicePosition: 'bottom',
    deviceScale: 0.72,
  },
  'split-screen': {
    id: 'split-screen',
    name: 'Split Screen',
    description: 'Device and text side by side',
    textPosition: 'left',
    devicePosition: 'right',
    deviceScale: 0.82,
  },
  'feature-highlight': {
    id: 'feature-highlight',
    name: 'Feature Highlight',
    description: 'Feature bullets alongside the device',
    textPosition: 'left',
    devicePosition: 'right',
    deviceScale: 0.75,
  },
  'full-bleed': {
    id: 'full-bleed',
    name: 'Full Bleed',
    description: 'Device fills the frame, text overlaid',
    textPosition: 'overlay',
    devicePosition: 'full',
    deviceScale: 0.95,
  },
  'minimal-clean': {
    id: 'minimal-clean',
    name: 'Minimal Clean',
    description: 'Clean background, centered device, elegant text',
    textPosition: 'top',
    devicePosition: 'center',
    deviceScale: 0.65,
  },
  'bold-statement': {
    id: 'bold-statement',
    name: 'Bold Statement',
    description: 'Giant headline dominates, device secondary',
    textPosition: 'top',
    devicePosition: 'bottom',
    deviceScale: 0.6,
  },
  'dark-luxury': {
    id: 'dark-luxury',
    name: 'Dark Luxury',
    description: 'Premium dark aesthetic with glow effects',
    textPosition: 'top',
    devicePosition: 'center',
    deviceScale: 0.68,
  },
  'glass-premium': {
    id: 'glass-premium',
    name: 'Glass Premium',
    description: 'Glassmorphism background with floating device',
    textPosition: 'top',
    devicePosition: 'center',
    deviceScale: 0.7,
  },
};
