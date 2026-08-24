// ╔══════════════════════════════════════════════════════════════════════════════╗
// ║  Repo Sandbox — Safe install, dev server lifecycle, security checks          ║
// ╚══════════════════════════════════════════════════════════════════════════════╝

import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { spawn, type ChildProcess } from 'node:child_process';
import { createServer } from 'node:net';

export type PackageManager = 'pnpm' | 'npm' | 'yarn';

const DANGEROUS_SCRIPTS = [
  'rm -rf /', 'rm -rf ~', 'sudo ', 'chmod 777', '> /dev/sda',
  'eval ', 'curl | bash', 'wget -O - | sh', 'fork bomb', ':(){ :|:& };:',
];

export async function detectPackageManager(cloneDir: string): Promise<PackageManager | null> {
  if (existsSync(path.join(cloneDir, 'pnpm-lock.yaml'))) return 'pnpm';
  if (existsSync(path.join(cloneDir, 'yarn.lock'))) return 'yarn';
  if (existsSync(path.join(cloneDir, 'package-lock.json'))) return 'npm';
  if (existsSync(path.join(cloneDir, 'package.json'))) return 'npm';
  return null;
}

export async function safeInstall(cloneDir: string, pm: PackageManager): Promise<boolean> {
  return new Promise((resolve) => {
    const p = spawn(pm, ['install', '--ignore-scripts'], { cwd: cloneDir, stdio: 'pipe' });
    let stderr = '';
    p.stderr.on('data', d => { stderr += d.toString(); });
    p.on('close', code => {
      if (code !== 0) {
        console.warn(`[Sandbox] install failed:`, stderr || `exited ${code}`);
        resolve(false);
      } else {
        resolve(true);
      }
    });
    p.on('error', (err) => {
      console.warn(`[Sandbox] install failed:`, err.message);
      resolve(false);
    });
    setTimeout(() => { p.kill('SIGTERM'); resolve(false); }, 120_000);
  });
}

export async function detectDevCommand(cloneDir: string, pm: PackageManager): Promise<string | null> {
  try {
    const pkgPath = path.join(cloneDir, 'package.json');
    const pkg = JSON.parse(await readFile(pkgPath, 'utf-8'));
    const scripts = pkg.scripts || {};

    // Common dev script names
    const candidates = ['dev', 'start', 'develop', 'serve'];
    for (const name of candidates) {
      if (scripts[name]) return name;
    }

    // Fallback: use the first script that looks like a dev server
    for (const [name, script] of Object.entries(scripts)) {
      if (typeof script === 'string' && /\b(dev|start|serve|next|vite|astro|remix)\b/.test(script)) {
        return name;
      }
    }

    return null;
  } catch {
    return null;
  }
}

async function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.listen(0, () => {
      const addr = server.address();
      if (addr && typeof addr !== 'string') {
        const port = addr.port;
        server.close(() => resolve(port));
      } else {
        server.close(() => reject(new Error('Could not find free port')));
      }
    });
    server.on('error', reject);
  });
}

export async function startDevServer(
  cloneDir: string,
  scriptName: string,
  pm: PackageManager,
  port?: number,
): Promise<{ process: ChildProcess; port: number; url: string }> {
  const freePort = port || (await findFreePort());
  const env = { ...process.env, PORT: String(freePort) };

  const cmd = pm;
  const child = spawn(cmd, ['run', scriptName], {
    cwd: cloneDir,
    env: env as typeof process.env,
    stdio: 'pipe',
    detached: false,
  } as Parameters<typeof spawn>[2]);

  child.on('error', () => { /* handled by caller */ });

  // Wait for server to start
  const url = `http://localhost:${freePort}`;
  const started = await waitForServer(url, 60_000);
  if (!started) {
    child.kill('SIGTERM');
    throw new Error(`Dev server failed to start within timeout on port ${freePort}`);
  }

  return { process: child, port: freePort, url };
}

async function waitForServer(url: string, timeoutMs: number): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const resp = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (resp.ok || resp.status < 500) return true;
    } catch {
      // Not ready yet
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

export async function stopDevServer(proc: ChildProcess): Promise<void> {
  return new Promise((resolve) => {
    if (!proc.killed) {
      proc.kill('SIGTERM');
      const forceKill = setTimeout(() => {
        if (!proc.killed) proc.kill('SIGKILL');
      }, 5000);
      proc.on('exit', () => {
        clearTimeout(forceKill);
        resolve();
      });
      // Resolve after 6s max
      setTimeout(resolve, 6000);
    } else {
      resolve();
    }
  });
}

export async function isRepoSafeToRun(cloneDir: string): Promise<boolean> {
  try {
    const pkgPath = path.join(cloneDir, 'package.json');
    if (!existsSync(pkgPath)) return true;

    const pkg = JSON.parse(await readFile(pkgPath, 'utf-8'));
    const scripts = { ...(pkg.scripts || {}), ...(pkg.postinstall ? { postinstall: pkg.postinstall } : {}) };

    for (const [name, script] of Object.entries(scripts)) {
      if (typeof script !== 'string') continue;
      for (const dangerous of DANGEROUS_SCRIPTS) {
        if (script.includes(dangerous)) {
          console.warn(`[Sandbox] dangerous script detected: ${name} contains "${dangerous}"`);
          return false;
        }
      }
    }

    return true;
  } catch {
    return true; // If we can't read package.json, assume safe
  }
}
