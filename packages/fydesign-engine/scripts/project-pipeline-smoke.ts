import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { launchBrowser } from '../src/lib/browser';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const temporary = await mkdtemp(path.join(os.tmpdir(), 'edecan-project-pipeline-'));
const state = path.join(temporary, 'state');
const output = path.join(temporary, 'output');
const token = 'pipeline-smoke-capability';
let calls = 0;
let visionCalls = 0;

function responseFor(payload: Record<string, unknown>): string {
  const prompt = String(payload.prompt || '');
  if (prompt.includes('You are OPUS')) {
    return JSON.stringify({
      artisticSummary: 'Calm editorial clarity',
      compositionStrategy: ['Asymmetric focal point'],
      typographyGuidance: ['Strong type hierarchy'],
      spacingPhilosophy: ['Use an eight point rhythm'],
      colorStrategy: ['One restrained accent'],
      restraintRules: ['Remove decorative noise'],
      antiPatterns: ['Generic feature-card grid'],
      emotionalDirection: 'Confident and humane',
      dominantVisualMove: 'Oversized left-aligned headline',
      forbiddenMoves: ['External assets'],
      tasteReferences: ['Editorial systems'],
    });
  }
  if (prompt.includes('senior visual design critic')) {
    return JSON.stringify({ score: 9, worth_fixing: false, issues: [], improvements: [] });
  }
  if (prompt.includes('SINGLE static HTML snippet')) {
    return '<div style="width:390px;height:844px;overflow:hidden;background:#f8fafc;color:#111827;font-family:system-ui;position:relative"><div style="padding:64px 24px 24px"><div style="font-size:30px;font-weight:800">Mis pendientes</div><div style="margin-top:24px;padding:18px;border:1px solid #e5e7eb;border-radius:16px">Pagar mañana</div><div style="margin-top:12px;padding:18px;border:1px solid #e5e7eb;border-radius:16px">Responder correo</div></div></div>';
  }
  return '<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;overflow:hidden;width:100%;height:100%;background:#f5f1e8;color:#17211b;font-family:system-ui}.canvas{width:100%;height:100%;display:grid;place-items:center}.card{max-width:70%;padding:48px;border:1px solid #17211b;border-radius:24px}h1{font-size:64px;line-height:1;margin:0 0 24px}p{font-size:22px}</style></head><body><main class="canvas"><section class="card"><h1>Diseño humano</h1><p>Un artefacto real, editable y local.</p></section></main></body></html>';
}

const server = http.createServer((request, response) => {
  if (request.method !== 'POST' || request.url !== '/complete') {
    response.writeHead(404).end();
    return;
  }
  if (request.headers.authorization !== `Bearer ${token}`) {
    response.writeHead(401).end();
    return;
  }
  const chunks: Buffer[] = [];
  request.on('data', (chunk) => chunks.push(Buffer.from(chunk)));
  request.on('end', () => {
    calls++;
    const payload = JSON.parse(Buffer.concat(chunks).toString('utf8')) as Record<string, unknown>;
    if (Array.isArray(payload.images) && payload.images.length) visionCalls++;
    const body = JSON.stringify({ text: responseFor(payload) });
    response.writeHead(200, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) });
    response.end(body);
  });
});

await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
const address = server.address();
if (!address || typeof address === 'string') throw new Error('Fake bridge did not bind');
const bridgePort = address.port;

async function execute(payload: Record<string, unknown>): Promise<Record<string, any>> {
  const child = spawn(
    process.execPath,
    [path.join(root, 'node_modules', 'tsx', 'dist', 'cli.mjs'), 'scripts/fydesign-project.ts'],
    {
      cwd: root,
      env: {
        PATH: process.env.PATH || '',
        HOME: process.env.HOME || '',
        LANG: process.env.LANG || 'C.UTF-8',
        FYDESIGN_LLM_BRIDGE_URL: `http://127.0.0.1:${bridgePort}/complete`,
        FYDESIGN_LLM_BRIDGE_TOKEN: token,
        FYDESIGN_STATE_ROOT: state,
        FYDESIGN_OUTPUT_ROOT: output,
        FYDESIGN_STORE_PATH: path.join(state, 'fydesign-store.json'),
      },
      stdio: ['pipe', 'pipe', 'pipe'],
    },
  );
  let stdout = '';
  let stderr = '';
  child.stdout.on('data', (chunk) => { stdout += chunk.toString('utf8'); });
  child.stderr.on('data', (chunk) => { stderr += chunk.toString('utf8'); });
  child.stdin.end(JSON.stringify(payload));
  const exitCode = await new Promise<number | null>((resolve) => child.once('exit', resolve));
  if (exitCode !== 0) throw new Error(`Project process failed (${exitCode}): ${stderr.slice(-2_000)}`);
  return JSON.parse(stdout.trim()) as Record<string, any>;
}

try {
  const created = await execute({
    action: 'create',
    prompt: 'Crea una landing humana y clara',
    projectName: 'Pipeline real',
    brandTokens: `Identidad extensa ${'x'.repeat(70_000)}`,
    mode: 'landing',
    quality: 'balanced',
    count: 2,
    width: 900,
    height: 600,
  });
  if (!created.ok || created.revisions.length !== 2 || created.files.length !== 6) {
    throw new Error('Create did not persist two variants plus the board');
  }
  if (!created.pipeline.corpusEnriched || created.pipeline.designMemoryInsights < 2) {
    throw new Error('Corpus/design memory were not active in the project route');
  }
  if (!created.pipeline.watchdog || !created.pipeline.deterministicValidation) {
    throw new Error('Watchdog/validation were not active in the project route');
  }
  if (created.pipeline.variantCount !== 2 || created.pipeline.criticsAttempted !== 2) {
    throw new Error('Visual critic was not executed');
  }
  for (const file of created.files) await stat(file);
  const boardPath = created.files.find((file: string) => file.endsWith('-board.html'));
  const board = await readFile(boardPath, 'utf8');
  if (!board.includes('Content-Security-Policy') || !board.includes('srcdoc=')) {
    throw new Error('Static variant board is missing its hardened preview frames');
  }
  if (board.includes('<script') || board.includes('allow-scripts') || board.includes('allow-same-origin')) {
    throw new Error('Variant iframe has excessive sandbox privileges');
  }
  const boardBrowser = await launchBrowser();
  try {
    const page = await boardBrowser.newPage();
    await page.setContent(board, { waitUntil: 'load' });
    const frames = page.frames();
    if (frames.length < 3) throw new Error('Static board did not load both variant frames');
    const headings = await Promise.all(frames.slice(1).map((frame) =>
      frame.locator('h1').textContent().catch(() => null)));
    if (headings.some((heading) => heading !== 'Diseño humano')) {
      throw new Error(`Static board frames were not visibly rendered: ${JSON.stringify(headings)}`);
    }
  } finally {
    await boardBrowser.close();
  }
  const boardPng = created.files.find((file: string) => file.endsWith('-board.png'));
  if ((await stat(boardPng)).size < 5_000) {
    throw new Error('Static board did not produce a visible Chromium render');
  }

  const mockup = await execute({
    action: 'create',
    prompt: 'Crea los mockups de esta aplicación de pendientes',
    projectName: 'Mockup ejecutable',
    brandName: 'Agenda Clara',
    mode: 'mockup',
    quality: 'fast',
    width: 900,
    height: 700,
    languages: ['es'],
    theme: { primaryColor: '#4F46E5', backgroundColor: '#F8FAFC' },
    screenBriefs: [{
      name: 'Pendientes',
      route: '/pendientes',
      layout: 'list',
      texts: ['Todo bajo control', 'Organiza tu día sin esfuerzo'],
      components: ['Lista de pendientes', 'Agregar pendiente'],
      icons: ['check'],
    }],
  });
  if (!mockup.pipeline.screenRecreation || mockup.pipeline.mockupScreens !== 1) {
    throw new Error('Screen recreation/mockup modules were not active in the project route');
  }
  const mockupHtml = await readFile(
    mockup.files.find((file: string) => file.includes('-rev_') && file.endsWith('.html')),
    'utf8',
  );
  if (!mockupHtml.includes('Todo bajo control') || mockupHtml.includes('<script')) {
    throw new Error('Mockup output was not rendered or hardened');
  }

  const edited = await execute({
    action: 'edit',
    projectId: created.project.id,
    instruction: 'Haz el título más cálido sin cambiar lo demás',
    quality: 'balanced',
  });
  if (!edited.ok || edited.previousRevision !== created.revisions.at(-1)) {
    throw new Error('Edit did not preserve immutable revision lineage');
  }

  const history = await execute({ action: 'history', projectId: created.project.id });
  if (history.revisions.length !== 3 || history.revisions.at(-1).id !== edited.revision) {
    throw new Error('History did not return the immutable revision lineage');
  }
  const health = await execute({ action: 'brand-health', projectId: created.project.id });
  if (!health.report || health.report.analyzedRevisions !== 3) {
    throw new Error('Brand health did not analyze the project revisions');
  }
  const template = await execute({
    action: 'template-save',
    projectId: created.project.id,
    templateName: 'Landing humana',
    templateCategory: 'landing',
  });
  const fromTemplate = await execute({
    action: 'template-create',
    templateId: template.template.id,
    projectName: 'Desde plantilla',
  });
  if (!fromTemplate.ok || fromTemplate.files.length !== 4) {
    throw new Error('Template did not create a complete reversible project');
  }
  const duplicate = await execute({
    action: 'duplicate',
    projectId: created.project.id,
    projectName: 'Copia completa',
  });
  if (!duplicate.ok || duplicate.project.id === created.project.id) {
    throw new Error('Duplicate did not create an independent project');
  }
  const designSystem = await execute({
    action: 'design-system-generate',
    projectId: created.project.id,
  });
  if (!designSystem.designSystem?.tokens || designSystem.files.length !== 1) {
    throw new Error('Design system generation did not persist an export');
  }
  const exported = await execute({
    action: 'export',
    projectId: created.project.id,
    exportFormat: 'pdf',
  });
  if ((await stat(exported.files[0])).size < 1_000) {
    throw new Error('PDF export is not a real document');
  }
  const shared = await execute({ action: 'share-package', projectId: created.project.id });
  if (shared.privacy !== 'local-private' || shared.files.length !== 3) {
    throw new Error('Share package did not preserve its private local boundary');
  }

  const assetDir = path.join(state, 'staging', 'smoke');
  await mkdir(assetDir, { recursive: true });
  const assetPath = path.join(assetDir, 'reference.png');
  await writeFile(assetPath, Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZKx8AAAAASUVORK5CYII=',
    'base64',
  ));
  await execute({
    action: 'create',
    prompt: 'Crea un post usando la referencia visual privada',
    projectName: 'Referencia privada',
    mode: 'post',
    quality: 'fast',
    assetPaths: [assetPath],
  });
  if (!visionCalls) throw new Error('Private visual references did not reach the Edecán bridge');
  if (calls < 9) throw new Error(`Expected full AI pipeline calls, received ${calls}`);

  const store = JSON.parse(await readFile(path.join(state, 'fydesign-store.json'), 'utf8'));
  if (!Array.isArray(store.designMemory) || !store.designMemory.length) {
    throw new Error('Persistent local design memory was not seeded');
  }
} finally {
  await new Promise<void>((resolve) => server.close(() => resolve()));
  await rm(temporary, { recursive: true, force: true });
}
