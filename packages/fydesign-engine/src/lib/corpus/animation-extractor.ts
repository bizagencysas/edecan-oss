// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Animation Extractor — Framer Motion, GSAP, CSS animations                    ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';

export interface AnimationPattern {
  type: 'css-transition' | 'css-keyframe' | 'framer-motion' | 'gsap' | 'three-js';
  description: string;
  properties: string[];
  file: string;
  snippet?: string;
}

export type AnimationLibrary = 'framer-motion' | 'gsap' | 'motion' | 'css' | 'three' | 'react-spring';

export interface CSSAnimation {
  name: string;
  duration: string;
  easing: string;
  keyframes: string;
}

export interface FramerPattern {
  component: string;
  animation: string;
  props: Record<string, string>;
}

export function detectAnimationLibrary(profile: {
  hasFramerMotion: boolean;
  hasGsap: boolean;
  hasThreeJS: boolean;
}): AnimationLibrary[] {
  const libs: AnimationLibrary[] = [];
  if (profile.hasFramerMotion) libs.push('framer-motion', 'motion');
  if (profile.hasGsap) libs.push('gsap');
  if (profile.hasThreeJS) libs.push('three');
  // CSS animations always possible
  if (!libs.length) libs.push('css');
  return libs;
}

export async function extractAnimationPatterns(
  cloneDir: string,
  profile: { hasFramerMotion: boolean; hasGsap: boolean; hasThreeJS: boolean },
): Promise<AnimationPattern[]> {
  const patterns: AnimationPattern[] = [];

  const tsxFiles = await findFiles(cloneDir, /\.(tsx|jsx)$/, 50);
  const cssFiles = await findFiles(cloneDir, /\.(css|scss)$/, 30);

  // CSS animations from CSS files
  for (const file of cssFiles) {
    try {
      const content = await readFile(file, 'utf-8');
      const relPath = path.relative(cloneDir, file);

      const keyframePatterns = extractCSSAnimations([{ path: file, content }]);
      for (const anim of keyframePatterns) {
        patterns.push({
          type: 'css-keyframe',
          description: `@keyframes ${anim.name} — ${anim.duration} ${anim.easing}`,
          properties: ['animation', 'keyframes'],
          file: relPath,
          snippet: anim.keyframes.slice(0, 200),
        });
      }

      // CSS transitions
      const transitionMatch = content.match(/transition(-[a-z]+)?\s*:\s*([^;{]+)/g);
      if (transitionMatch) {
        for (const tm of transitionMatch) {
          patterns.push({
            type: 'css-transition',
            description: tm.trim(),
            properties: tm.split(':')[1]?.trim().split(/\s+/) || [],
            file: relPath,
            snippet: tm.trim(),
          });
        }
      }
    } catch { /* skip */ }
  }

  // Framer Motion patterns from TSX files
  if (profile.hasFramerMotion) {
    for (const file of tsxFiles) {
      try {
        const content = await readFile(file, 'utf-8');
        const relPath = path.relative(cloneDir, file);

        if (/from\s+['"]framer-motion['"]/.test(content) || /from\s+['"]motion['"]/.test(content)) {
          const fmPatterns = extractFramerMotionPatterns([{ path: file, content }]);
          for (const fp of fmPatterns) {
            patterns.push({
              type: 'framer-motion',
              description: `${fp.component}: ${fp.animation}`,
              properties: Object.keys(fp.props),
              file: relPath,
              snippet: `motion.${fp.component} ${fp.animation}(${JSON.stringify(fp.props).slice(0, 120)})`,
            });
          }
        }
      } catch { /* skip */ }
    }
  }

  // GSAP patterns
  if (profile.hasGsap) {
    for (const file of tsxFiles) {
      try {
        const content = await readFile(file, 'utf-8');
        const relPath = path.relative(cloneDir, file);

        const gsapMatch = content.match(/gsap\.\w+\([^)]+\)/g);
        if (gsapMatch) {
          for (const gm of gsapMatch) {
            patterns.push({
              type: 'gsap',
              description: gm,
              properties: [],
              file: relPath,
              snippet: gm.slice(0, 200),
            });
          }
        }
      } catch { /* skip */ }
    }
  }

  return patterns.slice(0, 30);
}

export function extractCSSAnimations(
  cssFiles: { path: string; content: string }[],
): CSSAnimation[] {
  const animations: CSSAnimation[] = [];

  for (const file of cssFiles) {
    const content = file.content;

    // Find @keyframes blocks
    const keyframeRegex = /@keyframes\s+(\w+)\s*\{([^}]+)\}/g;
    let m;
    while ((m = keyframeRegex.exec(content)) !== null) {
      animations.push({
        name: m[1],
        duration: '0.3s',
        easing: 'ease',
        keyframes: m[2].trim().slice(0, 300),
      });
    }

    // Find animation declarations
    const animRegex = /animation\s*:\s*([^;{]+)/g;
    let am;
    while ((am = animRegex.exec(content)) !== null) {
      const parts = am[1].trim().split(/\s+/);
      const name = parts[0];
      const duration = parts.find((p) => /s|ms/.test(p)) || '0.3s';
      const easing = parts.find((p) => /ease|linear|bounce|elastic|cubic-bezier/.test(p)) || 'ease';

      const existing = animations.find((a) => a.name === name);
      if (existing) {
        existing.duration = duration;
        existing.easing = easing;
      }
    }
  }

  return animations;
}

export function extractFramerMotionPatterns(
  tsxFiles: { path: string; content: string }[],
): FramerPattern[] {
  const patterns: FramerPattern[] = [];

  for (const file of tsxFiles) {
    const content = file.content;

    // motion.div / motion.button etc with animate/initial/whileHover props
    const motionRegex = /motion\.(\w+)[^>]*?(animate|initial|whileHover|whileTap|exit|variants)\s*=\s*(\{[^}]+\})/g;
    let m;
    while ((m = motionRegex.exec(content)) !== null) {
      try {
        patterns.push({
          component: m[1],
          animation: m[2],
          props: JSON.parse(m[3].replace(/(\w+):/g, '"$1":').replace(/'/g, '"')),
        });
      } catch {
        patterns.push({
          component: m[1],
          animation: m[2],
          props: { raw: m[3].slice(0, 100) },
        });
      }
    }

    // useAnimation / useSpring / useTransform hooks
    const hookRegex = /(?:const|let)\s+\w+\s*=\s*use(?:Animation|Spring|Transform|MotionValue)\(/g;
    if (hookRegex.test(content)) {
      patterns.push({
        component: 'hook',
        animation: 'react-hook',
        props: { detected: 'true' },
      });
    }
  }

  return patterns;
}

async function findFiles(dir: string, pattern: RegExp, max: number): Promise<string[]> {
  const results: string[] = [];

  async function walk(d: string) {
    if (results.length >= max) return;
    try {
      const entries = await readdir(d, { withFileTypes: true });
      for (const entry of entries) {
        if (results.length >= max) return;
        if (entry.name.startsWith('.') || entry.name === 'node_modules') continue;
        const full = path.join(d, entry.name);
        if (entry.isDirectory()) {
          await walk(full);
        } else if (entry.isFile() && pattern.test(entry.name)) {
          results.push(full);
        }
      }
    } catch { /* skip */ }
  }

  await walk(dir);
  return results;
}
