#!/usr/bin/env node
// ╔══════════════════════════════════════════════════════════════════════════╗
// ║  fydesign MCP server  —  zero-dependency, stdio (newline-delimited JSON-RPC) ║
// ║                                                                            ║
// ║  Exposes fydesign's on-brand design powers as MCP tools so any MCP client    ║
// ║  (MCP-compatible assistants and the Edecán capability router)                ║
// ║  configured local/API model can generate on-brand creative artifacts.       ║
// ║                                                                            ║
// ║  NO server: each call spawns a ONE-SHOT `scripts/fydesign-gen.ts` that       ║
// ║  loads the brand, generates N slides through the configured model router,    ║
// ║  renders them to PNG, saves to disk, and EXITS. Nothing stays on :3000.      ║
// ║                                                                            ║
// ║  Env:                                                                       ║
// ║    FYDESIGN_DIR   path to the fydesign repo (default: this file's ../)       ║
// ║    GEN_TIMEOUT    ms per generation (default 1200000 = 20 min)               ║
// ╚══════════════════════════════════════════════════════════════════════════╝

import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const FYDESIGN_DIR = process.env.FYDESIGN_DIR || join(HERE, '..');
// 20 min default: multi-shot video ads (image→video per shot + assembly) can run
// well past 10 min, especially on premium models (Gemini Omni / Veo / Kling v3).
const GEN_TIMEOUT = Number(process.env.GEN_TIMEOUT || 1_200_000);
const SERVER_INFO = { name: 'fydesign', version: '0.2.0' };
const CHILD_ENV_ALLOWLIST = new Set([
  'PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR',
  'ANTHROPIC_API_KEY', 'CLAUDE_USE_MAX', 'CLAUDE_CLI_PATH', 'CLAUDE_CLI_MODEL',
  'CLAUDE_MODEL', 'CLAUDE_VISION_MODEL', 'FAL_KEY', 'FAL_IMAGE_MODEL',
  'GEMINI_API_KEY', 'GOOGLE_GENAI_API_KEY', 'GOOGLE_CREDENTIALS_JSON',
  'GOOGLE_PREMIUM_IMAGE_MODEL', 'GITHUB_TOKEN', 'OPENAI_API_KEY',
  'VERTEX_AI_PROJECT_ID', 'VERTEX_AI_LOCATION', 'VERTEX_IMAGEN_LOCATION',
  'VERTEX_GOOGLE_IMAGE_LOCATION', 'GCS_ASSETS_BUCKET', 'MUAPI_API_KEY',
  'MUAPI_API_KEY2', 'MUAPI_SANDBOX', 'FYDESIGN_STORE_PATH', 'FY_IMAGE_ENGINE',
  'CHROMIUM_PATH', 'PUPPETEER_EXECUTABLE_PATH', 'FFMPEG_PATH', 'FFPROBE_PATH',
  'PLAYWRIGHT_BROWSERS_PATH',
  'YTDLP_PATH', 'ANIMATE_MODEL', 'RECAST_MODEL', 'REFERENCE_VIDEO_MODEL',
  'FYDESIGN_LLM_BRIDGE_URL', 'FYDESIGN_LLM_BRIDGE_TOKEN',
  'START_END_FRAME_MODEL', 'MUAPI_ANGLES_MODEL', 'MUAPI_BG_REMOVER_MODEL',
  'MUAPI_EXPAND_IMAGE_MODEL', 'MUAPI_FACE_SWAP_MODEL', 'MUAPI_HEADSHOT_MODEL',
  'MUAPI_IMAGE_MODEL', 'MUAPI_IMAGE_UPSCALE', 'MUAPI_INPAINT_MODEL',
  'MUAPI_LIPSYNC_MODEL', 'MUAPI_LORA_TRAINER_MODEL', 'MUAPI_LORA_ZIP_URL',
  'MUAPI_MUSIC_MODEL', 'MUAPI_OBJECT_ERASE_MODEL', 'MUAPI_OUTFIT_SWAP_MODEL',
  'MUAPI_PLACE_OBJECT_MODEL', 'MUAPI_PRODUCT_PHOTO_MODEL', 'MUAPI_RELIGHT_MODEL',
  'MUAPI_SKIN_ENHANCE_MODEL', 'MUAPI_STYLE_EDIT_MODEL', 'MUAPI_STYLE_TRANSFER_MODEL',
  'MUAPI_TTS_MODEL', 'MUAPI_TTS_VOICE', 'MUAPI_TTS_VOICE_ES', 'MUAPI_VIDEO_MODEL',
  'MUAPI_VIDEO_UPSCALE', 'FYDESIGN_OUTPUT_ROOT', 'FYDESIGN_STATE_ROOT',
]);

function safeChildEnv() {
  return Object.fromEntries(
    Object.entries(process.env).filter(([key, value]) => CHILD_ENV_ALLOWLIST.has(key) && value),
  );
}

// ─── Run the one-shot generator and return its parsed JSON result ────────────

function runGen(input) {
  return new Promise((resolve, reject) => {
    const outputRoot = (process.env.FYDESIGN_OUTPUT_ROOT || '').trim();
    if (!input.list && !outputRoot) {
      return reject(new Error(
        'FYDESIGN_OUTPUT_ROOT no está configurado; Edecán debe asignar un directorio privado.',
      ));
    }
    const controlledInput = input.list ? { list: true } : { ...input, outDir: outputRoot };
    const localTsx = join(FYDESIGN_DIR, 'node_modules', 'tsx', 'dist', 'cli.mjs');
    if (!existsSync(localTsx)) {
      return reject(new Error('FyDesign no está instalado: ejecuta npm ci en packages/fydesign-engine.'));
    }
    // Usa el mismo Node que lanzó este MCP. En la app empaquetada se llama
    // `fydesign-node`, por lo que un shebang `/usr/bin/env node` no sería
    // resoluble en un equipo limpio.
    const cmd = process.execPath;
    // Edecán supplies credentials explicitly from its vault. Never read a
    // repository .env file or inherit a developer's private configuration.
    const scriptArgs = [localTsx, 'scripts/fydesign-gen.ts'];
    const args = scriptArgs;

    let child;
    try {
      child = spawn(cmd, args, {
        cwd: FYDESIGN_DIR,
        env: safeChildEnv(),
        detached: process.platform !== 'win32',
      });
    } catch (e) {
      return reject(new Error(`No pude lanzar el generador: ${e.message}`));
    }

    let out = '';
    let err = '';
    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (err += d));
    child.stdin.end(JSON.stringify(controlledInput));
    const timer = setTimeout(() => {
      try {
        if (process.platform !== 'win32' && child.pid) process.kill(-child.pid, 'SIGKILL');
        else child.kill('SIGKILL');
      } catch { /* noop */ }
      reject(new Error(`La generación superó ${Math.round(GEN_TIMEOUT / 1000)}s y se canceló.`));
    }, GEN_TIMEOUT);

    child.on('error', (e) => { clearTimeout(timer); reject(new Error(`spawn error: ${e.message}`)); });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        const tail = (err || out).trim().split('\n').slice(-4).join(' ');
        return reject(new Error(`El generador terminó con código ${code}: ${tail.slice(0, 500)}`));
      }
      // The script prints ONLY the result JSON to stdout (logs/banners → stderr).
      // Parse defensively in case a loader banner still leaks in.
      const trimmed = out.trim();
      try {
        resolve(JSON.parse(trimmed));
      } catch {
        const i = trimmed.indexOf('{');
        const j = trimmed.lastIndexOf('}');
        try {
          resolve(JSON.parse(trimmed.slice(i, j + 1)));
        } catch (e) {
          reject(new Error(`No pude parsear la salida del generador: ${e.message} | out: ${trimmed.slice(0, 150)}`));
        }
      }
    });
  });
}

// ─── Tools ─────────────────────────────────────────────────────────────────

const TOOLS = [
  {
    name: 'fydesign_generate',
    description:
      'Genera diseños on-brand para redes mediante el modelo configurado. Sirve para un anuncio o un carrusel, carga la identidad guardada, renderiza PNG reales y devuelve los artefactos con caption y hashtags.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string', description: 'Nombre de la marca guardada (ej. "Acme"). Usa fydesign_brands para verlas.' },
        prompt: { type: 'string', description: 'Qué quieres: tema/brief del carrusel o anuncio.' },
        slides: { type: 'number', description: 'Número de slides (1 = anuncio único; >1 = carrusel). Si se omite, se infiere del prompt.' },
        platform: {
          type: 'string',
          enum: ['instagram-feed', 'instagram-story', 'tiktok', 'facebook'],
          description: 'Formato/tamaño (default instagram-feed 1080x1080).',
        },
        repo: { type: 'string', description: 'Opcional: URL de repo de GitHub si la marca no está guardada (se analiza al vuelo).' },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'fydesign_brands',
    description: 'Lista las marcas configuradas en fydesign (nombre, repo, si tiene logo). Úsala para saber qué marcas puedes pasar a fydesign_generate / fydesign_image / fydesign_video.',
    inputSchema: { type: 'object', properties: {} },
  },
  {
    name: 'fydesign_register_brand',
    description:
      '➕ REGISTRA (o actualiza) una MARCA en la base de datos para que TODAS las herramientas la usen on-brand (colores, logo, kit de logos, datos reales anti-invención). Un solo comando — no necesitas el Setup web. Pasa nombre + paleta + logo + datos reales del producto. Para actualizar una marca existente, pasa su `id` (o el mismo nombre). Tras esto, úsala en fydesign_video_ad, fydesign_post, etc.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string', description: 'Nombre de la marca (ej. "Acme", "Producto Demo").' },
        colors: { type: 'array', items: { type: 'string' }, description: 'Paleta en hex, el color PRIMARIO primero (ej. ["#1A56FF","#0B1B3A","#F4F7FF"]).' },
        logo: { type: 'string', description: 'Logo principal: ruta de archivo, URL o dataURL. Se guarda embebido (sirve sin GCS).' },
        assets: { type: 'array', description: 'Kit de logos / pantallas reales. Nómbralos para que el modelo configurado elija (ej. "logomark", "logo-white", "app-icon", "screen-home").', items: { type: 'object', properties: { name: { type: 'string' }, url: { type: 'string' } }, required: ['name', 'url'] } },
        fonts: { type: 'string', description: 'Fuentes de marca (ej. "Playfair Display + Inter + JetBrains Mono").' },
        facts: { type: 'string', description: 'Datos REALES del producto/marca (qué es, para quién, props reales). el modelo configurado los limpia SIN inventar cifras. Esto evita anuncios genéricos o inventados.' },
        blurb: { type: 'string', description: 'Una línea que describe la marca.' },
        repo: { type: 'string', description: 'Opcional: URL del repo de GitHub de la marca.' },
        id: { type: 'string', description: 'Opcional: id de marca existente para ACTUALIZARLA en vez de crear una nueva.' },
      },
      required: ['brand'],
    },
  },
  {
    name: 'fydesign_image',
    description:
      'Genera una imagen real on-brand (personas, productos o escenas; no HTML) con el proveedor de imagen configurado. El asistente compone el prompt con los colores y estilo de la marca. Devuelve la ruta, el caption y, cuando el proveedor lo informa, el costo.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string', description: 'Marca guardada (ej. "Acme").' },
        prompt: { type: 'string', description: 'Qué imagen quieres; el asistente lo convierte en un prompt on-brand.' },
        provider: { type: 'string', enum: ['vertex', 'muapi', 'openai', 'fal'], description: 'vertex (default, Imagen 4, usa tu logo/app reales); muapi (Flux/Seedream/Ideogram/Nano-Banana/GPT-Image…); openai (GPT-Image directo, requiere OPENAI_API_KEY); fal (fal.ai directo — Flux/ideogram/nano-banana/recraft, requiere FAL_KEY, ya configurada).' },
        model: { type: 'string', description: 'Modelo Muapi si provider=muapi (ej. flux-2-dev, bytedance-seedream-v4, gpt4o).' },
        count: { type: 'number', description: 'Cuántas variaciones generar (1-4, default 1). Útil para escoger la mejor.' },
        quality: { type: 'string', enum: ['ultra', 'standard', 'fast', 'brand'], description: 'Perfil de calidad: ultra para alta fidelidad/texto complejo, standard para social, fast para prototipos o brand para componer referencias reales. El precio depende del proveedor.' },
        style: { type: 'string', description: 'Estilo visual: fotográfico, 3D, ilustración, minimal, cinemático…' },
        styleKey: { type: 'string', description: 'Clave de preset del catálogo (ej. y2k-studio, editorial-street-style, quiet-luxury, golden-hour).' },
        colorLock: { type: 'boolean', description: 'Soul HEX: fuerza la generación a la paleta EXACTA de la marca.' },
        platform: { type: 'string', enum: ['instagram-feed', 'instagram-story', 'tiktok', 'facebook'], description: 'Define el aspect ratio (default 1:1).' },
        aspectRatio: { type: 'string', description: 'Override del aspect ratio (ej. "16:9", "9:16").' },
        sizes: { type: 'array', items: { type: 'string' }, description: 'Varios formatos a la vez (ej. ["instagram-feed","instagram-story"] o ["all"]). Si OMITES platform/sizes, el modelo configurado infiere el formato y tamaño exacto del prompt (ej. "una historia" → 1080×1920).' },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'fydesign_post',
    description:
      'Genera un post terminado: imagen on-brand, logo, titular y CTA en el formato de la red. El proveedor de texto configurado prepara dirección y copy; el motor devuelve el PNG final.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string', description: 'Marca guardada (ej. "Acme").' },
        prompt: { type: 'string', description: 'De qué es el post (brief).' },
        quality: { type: 'string', enum: ['ultra', 'standard', 'fast', 'brand'], description: 'Perfil de calidad: ultra, standard, fast o brand para componer referencias reales. El precio depende del proveedor.' },
        sizes: { type: 'array', items: { type: 'string' }, description: 'Varios formatos (["instagram-feed","instagram-story"] o ["all"]). Si omites platform/sizes, el modelo configurado infiere el formato del prompt.' },
        style: { type: 'string', description: 'Estilo visual opcional.' },
        count: { type: 'number', description: 'Cuántas variaciones (1-4).' },
        platform: { type: 'string', enum: ['instagram-feed', 'instagram-story', 'tiktok', 'facebook'], description: 'Formato/tamaño (default instagram-feed).' },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'fydesign_edit',
    description:
      'Edita / remix una imagen EXISTENTE (image-to-image con Nano Banana): cambiar fondo, poner tu producto, variar estilo, limpiar, etc. Pasa la ruta/URL/dataURL de la imagen + qué cambiar. Devuelve la imagen editada on-brand.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string', description: 'Marca guardada (para mantener la paleta).' },
        inputImage: { type: 'string', description: 'Ruta de archivo, URL o data URL de la imagen a editar.' },
        prompt: { type: 'string', description: 'Qué cambiar (ej. "cambia el fondo a una oficina moderna y pon la tarjeta más grande").' },
        style: { type: 'string', description: 'Estilo visual opcional.' },
        count: { type: 'number', description: 'Cuántas variaciones (1-4).' },
        platform: { type: 'string', enum: ['instagram-feed', 'instagram-story', 'tiktok', 'facebook'], description: 'Formato/aspect ratio.' },
      },
      required: ['inputImage', 'prompt'],
    },
  },
  {
    name: 'fydesign_campaign',
    description:
      'Genera un set de campaña coherente: varias piezas terminadas con una narrativa, estilo y marca comunes. Se guardan en una subcarpeta de campaña.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string', description: 'Marca guardada (ej. "Acme").' },
        prompt: { type: 'string', description: 'Tema/brief de la campaña.' },
        count: { type: 'number', description: 'Cuántas piezas (2-6, default 4).' },
        quality: { type: 'string', enum: ['ultra', 'standard', 'fast', 'brand'], description: 'Motor de imagen (ver fydesign_image).' },
        style: { type: 'string', description: 'Estilo visual opcional que se suma a la dirección creativa del asistente.' },
        platform: { type: 'string', enum: ['instagram-feed', 'instagram-story', 'tiktok', 'facebook'], description: 'Formato.' },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'fydesign_strategy',
    description:
      'GOD MODE: el modelo configurado investiga tu marca, competencia y tendencias actuales EN INTERNET, define la estrategia de campaña, y genera un set de posts terminados on-brand. Más potente que fydesign_campaign (que solo usa conocimiento interno). Tarda más (investiga en vivo).',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string', description: 'Marca guardada (ej. "Acme").' },
        brief: { type: 'string', description: 'Tema/objetivo de la campaña.' },
        pieces: { type: 'number', description: 'Cuántas piezas (2-6, default 4).' },
        quality: { type: 'string', enum: ['ultra', 'standard', 'fast', 'brand'], description: 'Motor de imagen.' },
        platform: { type: 'string', enum: ['instagram-feed', 'instagram-story', 'tiktok', 'facebook'], description: 'Formato.' },
      },
      required: ['brief'],
    },
  },
  {
    name: 'fydesign_svg',
    description:
      'Genera un vector SVG on-brand (logo, ícono, badge o infografía simple) mediante el modelo de texto configurado, sin modelo de imagen. Devuelve un archivo .svg escalable.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string', description: 'Marca guardada (para la paleta y tipografía).' },
        prompt: { type: 'string', description: 'Qué vector quieres (ej. "un ícono minimalista de tarjeta de crédito").' },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'fydesign_video',
    description:
      'Genera un clip de video on-brand (texto a video) con el proveedor configurado. Para un anuncio completo con varias tomas, voz, música y marca usa fydesign_video_ad. Puede consumir un proveedor de pago y tardar varios minutos; Edecán muestra la confirmación antes de ejecutarlo.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string', description: 'Marca guardada (ej. "Acme").' },
        prompt: { type: 'string', description: 'Qué video quieres; el asistente lo convierte en un prompt on-brand.' },
        model: { type: 'string', description: 'Modelo Muapi de video actual (default kling-v2.5-turbo-pro-t2v; ej. veo3.1-text-to-video, gemini-omni-text-to-video, seedance-v1.5-pro-t2v-fast).' },
        duration: { type: 'number', description: 'Duración en segundos (default 5; se ajusta a lo que permita el modelo).' },
        platform: { type: 'string', enum: ['instagram-feed', 'instagram-story', 'tiktok', 'facebook'], description: 'Define el aspect ratio.' },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'fydesign_video_ad',
    description:
      'Produce un anuncio de video on-brand: planifica tomas, genera keyframes, anima, compone overlays, voz/música opcionales y entrega un MP4. Puede consumir proveedores de pago; el fallback local de movimiento no usa generación de video. Requiere ffmpeg.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string', description: 'Marca guardada (ej. "Acme", "Acme", "Marca Demo", "Producto Demo").' },
        prompt: { type: 'string', description: 'Brief del anuncio (qué vender, qué emoción, qué CTA).' },
        shots: { type: 'number', description: 'Número de tomas (3-6, default 4).' },
        withVoiceover: { type: 'boolean', description: 'el modelo configurado escribe y narra un guion de voz (TTS). Default true.' },
        withMusic: { type: 'boolean', description: 'Agregar cama musical (requiere MUAPI_MUSIC_MODEL).' },
        withCaptions: { type: 'boolean', description: 'Quemar subtítulos de la voz en el video.' },
        tier: { type: 'string', enum: ['fast', 'pro', 'max', 'ultra'], description: 'Perfil de calidad para el motor keyframe: fast, pro, max o ultra. El costo depende del proveedor, del modelo y del número de tomas.' },
        engine: { type: 'string', enum: ['seedance', 'kling', 'omni', 'direct', 'avatar', 'keyframe', 'auto'], description: 'Ruta de video: un modelo directo, avatar con lipsync, composición keyframe de varias tomas o selección automática. Algunas rutas restringen personas realistas; Edecán valida la respuesta del proveedor.' },
        refImages: { type: 'array', items: { type: 'string' }, description: 'Logo / producto / referencia (rutas/URLs/dataURLs, 1-7). El modelo lo integra ÉL MISMO en el video (no se pega a mano). Sin esto = text-to-video. (Para "persona real lip-sync" con engine:"avatar", sube la foto de la persona.)' },
        preset: { type: 'string', enum: ['ugc', 'tv-spot', 'hyper-motion', 'unboxing', 'product-review', 'demo', 'tutorial', 'cinematic', 'wild-card'], description: 'FORMATO de Marketing Studio ("elige formato y listo", estilo Higgsfield). Envuelve tu brief en ese estilo y fija el mejor modelo/aspect/duración, luego va DIRECTO al modelo: ugc (social realista), tv-spot ($1M cinematográfico), hyper-motion (alta energía/producto), unboxing, product-review, demo, tutorial, cinematic, wild-card (creativo audaz). Opcional.' },
        duration: { type: 'number', description: 'Duración del clip Omni en segundos (4, 6, 8 o 10; default 8).' },
        model: { type: 'string', description: 'Override del endpoint Muapi (ej. gemini-omni-image-to-video, veo-4-image-to-video, openai-sora-2-pro-image-to-video, kling-v3.0-pro-image-to-video). Pisa el tier/motor.' },
        cinemaBody: { type: 'string', description: 'Cinema Studio: cuerpo de cámara forzado en todas las tomas (arri-alexa-35, red-v-raptor, sony-venice, imax-film-camera, arriflex-16sr, panavision-dxl2).' },
        genre: { type: 'string', description: 'Cinema Studio: género (commercial, epic, noir, drama, action, intimate, documentary, music-video, suspense, horror, comedy, western, sci-fi, spectacle).' },
        colorGrade: { type: 'string', description: 'Cinema Studio: grade (teal-and-orange, golden-hour, warm, muted-film, high-contrast, film-noir, blockbuster, overcast-indie, documentary).' },
        speedRamp: { type: 'string', description: 'Cinema Studio: rampa de velocidad (slow-motion, speed-up, impact, ramp-up, flash-in, flash-out, linear).' },
        style: { type: 'string', description: 'Estilo visual opcional (cinemático, 3D, etc.).' },
        platform: { type: 'string', enum: ['instagram-feed', 'instagram-story', 'tiktok', 'facebook'], description: 'Define el aspect ratio (default vertical 9:16).' },
        aspectRatio: { type: 'string', description: 'Override del aspect ratio ("9:16","16:9","1:1").' },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'fydesign_analyze_video',
    description:
      '🔎 VIDEO ANALYZER ("analiza este video, quiero algo así"): el modelo con visión configurado examina fotogramas reales y deconstruye concepto, cámara, luz, sujeto, estructura y ritmo. Devuelve un prompt de recreación. Puede consumir el proveedor de visión conectado.',
    inputSchema: {
      type: 'object',
      properties: {
        url: { type: 'string', description: 'URL del video a analizar (YouTube/TikTok/Vimeo/Instagram via yt-dlp, o un http directo a un .mp4).' },
        file: { type: 'string', description: 'Ruta local o URL directa de un archivo de video (alternativa a url).' },
        frames: { type: 'number', description: 'Cuántos fotogramas ve el modelo configurado (4-10, default 7).' },
      },
    },
  },
  {
    name: 'fydesign_clipper',
    description:
      '✂️ PERSONAL CLIPPER: convierte un video LARGO (YouTube/TikTok/Vimeo via yt-dlp, o un archivo) en N clips verticales 9:16 listos para TikTok/Reels. Si el video tiene subtítulos automáticos (YouTube), el modelo configurado lee el transcript, elige los momentos MÁS virales y quema los subtítulos en cada clip. Sin transcript → cortes uniformes sin subs. Devuelve los .mp4 + por qué cada clip engancha.',
    inputSchema: {
      type: 'object',
      properties: {
        url: { type: 'string', description: 'URL del video largo (YouTube/TikTok/Vimeo/Instagram).' },
        file: { type: 'string', description: 'Ruta local o URL directa de un archivo de video (alternativa a url).' },
        count: { type: 'number', description: 'Cuántos clips cortar (1-10, default 3).' },
        clipLength: { type: 'number', description: 'Duración objetivo de cada clip en segundos (8-90, default 25).' },
      },
    },
  },
  {
    name: 'fydesign_ad_engine',
    description:
      '🧠 AD ENGINE: de un brief deriva nichos de audiencia, ángulos, hooks, prompts de video, copy y un reporte estratégico. Por defecto devuelve solo el plan; con generate:true crea una variante de video por nicho y puede consumir el proveedor conectado. No fabrica cifras, ratings ni testimonios.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string', description: 'Marca guardada (ej. "Acme", "Marca Demo").' },
        prompt: { type: 'string', description: 'Brief opcional (objetivo, producto, ángulo). Sin él, deriva de la marca.' },
        niches: { type: 'number', description: 'Cuántos nichos derivar (1-6, default 3).' },
        generate: { type: 'boolean', description: 'Generar también un video por nicho. El costo depende del proveedor y modelo conectados.' },
        model: { type: 'string', enum: ['seedance', 'kling', 'omni'], description: 'Modelo directo para las variantes (default seedance).' },
        platform: { type: 'string', description: 'Aspect ("instagram-story"=9:16, "facebook"=16:9).' },
      },
    },
  },
  {
    name: 'fydesign_product_ad',
    description:
      '🛍️ MARKETING STUDIO: anuncio de video centrado en tu PRODUCTO. Igual que fydesign_video_ad pero investiga el producto (pásale productUrl para sacar datos reales sin inventar) y/o usa una foto real del producto (productImage). Devuelve un .mp4 de anuncio de producto listo para publicar.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string', description: 'Marca guardada.' },
        prompt: { type: 'string', description: 'Brief del anuncio de producto.' },
        productUrl: { type: 'string', description: 'URL de la página del producto (el modelo configurado la investiga para datos REALES).' },
        productImage: { type: 'string', description: 'Ruta/URL/dataURL de una foto real del producto.' },
        shots: { type: 'number', description: 'Número de tomas (3-6).' },
        withVoiceover: { type: 'boolean', description: 'Voz en off (default true).' },
        withMusic: { type: 'boolean', description: 'Cama musical.' },
        withCaptions: { type: 'boolean', description: 'Subtítulos quemados.' },
        tier: { type: 'string', enum: ['fast', 'pro', 'max', 'ultra'], description: 'Calidad image→video: fast/pro/max/ultra (ultra=Gemini Omni, identidad+audio). Default pro.' },
        model: { type: 'string', description: 'Override del endpoint Muapi de video (pisa el tier).' },
        platform: { type: 'string', enum: ['instagram-feed', 'instagram-story', 'tiktok', 'facebook'], description: 'Formato (default 9:16).' },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'fydesign_influencer',
    description:
      '👤 AI INFLUENCER STUDIO (persona reutilizable, equivalente a Soul ID por imágenes de referencia): crea una persona/embajador de marca consistente a partir de un "photo dump" (varias fotos), o genera nuevas imágenes con ESA MISMA cara/identidad. action="create" (con personaName + refImages), action="use" (genera imágenes de la persona con un prompt), action="list" (ver personas de la marca). La persona se guarda por marca y se reutiliza.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string', description: 'Marca guardada.' },
        action: { type: 'string', enum: ['create', 'use', 'list'], description: 'create = registrar persona; use = generar imágenes de la persona; list = listar.' },
        personaName: { type: 'string', description: 'Nombre de la persona (ej. "Sofia embajadora").' },
        prompt: { type: 'string', description: 'create: descripción del personaje (opcional). use: qué imagen quieres de la persona.' },
        refImages: { type: 'array', items: { type: 'string' }, description: 'create: rutas/URLs/dataURLs de las fotos de referencia (el "photo dump", 3-10 fotos de la misma persona).' },
        count: { type: 'number', description: 'use: cuántas imágenes generar (1-4).' },
        platform: { type: 'string', enum: ['instagram-feed', 'instagram-story', 'tiktok', 'facebook'], description: 'Formato/aspect ratio.' },
      },
      required: [],
    },
  },
  {
    name: 'fydesign_talking_head',
    description:
      '🗣️ TALKING-HEAD / UGC: la persona (AI influencer) HABLA — retrato + voz en off → video con lipsync. Pásale la persona y el guion. Requiere un modelo de lipsync en Muapi (MUAPI_LIPSYNC_MODEL). Devuelve un .mp4.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string', description: 'Marca guardada.' },
        personaName: { type: 'string', description: 'Persona existente (créala antes con fydesign_influencer action=create).' },
        prompt: { type: 'string', description: 'El guion que dirá la persona (o usa script).' },
        script: { type: 'string', description: 'Guion explícito a narrar (alias de prompt).' },
        lipsyncModel: { type: 'string', description: 'Override del modelo de lipsync Muapi.' },
      },
      required: ['personaName'],
    },
  },
  {
    name: 'fydesign_photo_dump',
    description:
      '🖼️ PHOTO DUMP (consistencia tipo Soul v2): pásale varias fotos de referencia (un producto, una persona, un estilo) + un prompt, y genera imágenes NUEVAS manteniendo esa misma identidad/apariencia, on-brand. Como fydesign_influencer pero sin guardar persona — para una sola tanda.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string', description: 'Marca guardada.' },
        prompt: { type: 'string', description: 'Qué imagen quieres (manteniendo la identidad de las refs).' },
        refImages: { type: 'array', items: { type: 'string' }, description: 'Fotos de referencia (rutas/URLs/dataURLs, 2-4).' },
        count: { type: 'number', description: 'Cuántas imágenes (1-4, default 2).' },
        platform: { type: 'string', enum: ['instagram-feed', 'instagram-story', 'tiktok', 'facebook'], description: 'Formato/aspect ratio.' },
      },
      required: ['prompt', 'refImages'],
    },
  },
  {
    name: 'fydesign_batch',
    description:
      '⚡ SUPERCOMPUTER: de UN brief saca MUCHAS variaciones on-brand en paralelo (el modelo configurado expande el brief en N conceptos distintos y los genera concurrentemente). Ideal para explorar ángulos/escenas rápido y escoger los mejores. Devuelve todas las imágenes.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string', description: 'Marca guardada.' },
        prompt: { type: 'string', description: 'Brief a explotar en variaciones.' },
        count: { type: 'number', description: 'Cuántas variaciones (2-24, default 8).' },
        quality: { type: 'string', enum: ['ultra', 'standard', 'fast', 'brand'], description: 'Motor de imagen (default fast para velocidad/costo).' },
        platform: { type: 'string', enum: ['instagram-feed', 'instagram-story', 'tiktok', 'facebook'], description: 'Formato/aspect ratio.' },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'fydesign_studio',
    description:
      '🛠️ EDIT STUDIO (las "apps" de Higgsfield, vía Muapi): edita una imagen existente con una operación. op: inpaint (cambia una zona descrita), place (mete un producto/objeto, opcional editRef), expand (outpaint a otro aspecto), relight, bg-remove (quita fondo), outfit (cambia ropa, opcional editRef), face-swap (requiere editRef = cara), headshot (retrato pro), skin (mejora piel), erase (borra objeto), style (transfiere estilo: editRef o styleKey del catálogo), product (foto de producto pro). Devuelve la imagen editada.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string' },
        inputImage: { type: 'string', description: 'Imagen a editar (ruta/URL/dataURL).' },
        op: { type: 'string', enum: ['inpaint', 'place', 'expand', 'relight', 'bg-remove', 'outfit', 'face-swap', 'headshot', 'skin', 'erase', 'style', 'product'], description: 'Operación de edición.' },
        prompt: { type: 'string', description: 'Instrucción (qué cambiar). Para bg-remove/skin/headshot puede ir vacío.' },
        editRef: { type: 'string', description: 'Imagen de referencia (producto/prenda/cara/estilo) según la op.' },
        styleKey: { type: 'string', description: 'Para op=style: clave de estilo del catálogo (ej. y2k-studio, editorial-street-style).' },
        aspectRatio: { type: 'string', description: 'Para op=expand: aspect destino.' },
      },
      required: ['inputImage', 'op'],
    },
  },
  {
    name: 'fydesign_photodump',
    description: '📸 PHOTODUMP (Higgsfield Photodump Studio): de una persona/FyID genera un set de hasta 26 fotos consistentes en distintos escenarios/outfits/moods, listas para carrusel. Crea la persona antes con fydesign_influencer.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string' },
        personaName: { type: 'string', description: 'Persona existente.' },
        count: { type: 'number', description: 'Cuántas fotos (default 18, máx 26).' },
      },
      required: ['personaName'],
    },
  },
  {
    name: 'fydesign_instadump',
    description: '🌀 INSTADUMP (Higgsfield Instadump): de UN retrato + packs de tendencia genera una serie de imágenes on-trend manteniendo la misma identidad. Sin prompt manual.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string' },
        inputImage: { type: 'string', description: 'Retrato de referencia (ruta/URL/dataURL).' },
        trendKeys: { type: 'array', items: { type: 'string' }, description: 'Packs de tendencia (ej. paparazzi, red-carpet, mukbang). Si se omite, usa los primeros count.' },
        count: { type: 'number', description: 'Cuántas variaciones (default 12).' },
      },
      required: ['inputImage'],
    },
  },
  {
    name: 'fydesign_ambassador',
    description: '🤝 BRAND AMBASSADOR (AI Influencer): usa una persona/FyID como vocero de marca — el modelo configurado planea una mini-serie de contenido sobre el brief y genera las piezas con esa identidad.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string' },
        personaName: { type: 'string', description: 'Persona existente.' },
        prompt: { type: 'string', description: 'Brief / tema de la serie.' },
        count: { type: 'number', description: 'Cuántas piezas (default 3).' },
      },
      required: ['personaName', 'prompt'],
    },
  },
  {
    name: 'fydesign_train_face',
    description: '🧬 ENTRENAR FyID: entrena una identidad LoRA desde varias fotos para consistencia de personaje/cara. Es una operación externa potencialmente costosa y requiere confirmación deliberada.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string' },
        personaName: { type: 'string', description: 'Nombre de la FyID a entrenar.' },
        refImages: { type: 'array', items: { type: 'string' }, description: '15-30 fotos de la misma persona (rutas/URLs/dataURLs).' },
      },
      required: ['refImages'],
    },
  },
  {
    name: 'fydesign_storyboard',
    description: '🍿 STORYBOARD (Higgsfield Popcorn): de UN brief, el modelo configurado arma una secuencia cinemática de N frames on-brand (consistentes), renderizados como imágenes. Ideal para previsualizar un anuncio antes de animarlo.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string' },
        prompt: { type: 'string', description: 'Brief de la secuencia.' },
        frames: { type: 'number', description: 'Cuántos frames (default 6, máx 12).' },
        platform: { type: 'string', enum: ['instagram-feed', 'instagram-story', 'tiktok', 'facebook'], description: 'Aspect ratio.' },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'fydesign_upscale',
    description: '⬆️ UPSCALE (Topaz vía Muapi): sube de resolución una imagen (2x/4x/8x) o un video. Para video, pasa una URL pública del archivo.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string' },
        inputImage: { type: 'string', description: 'Imagen o video a escalar (ruta/URL/dataURL; video necesita URL).' },
        target: { type: 'string', enum: ['image', 'video'], description: 'Qué escalar (default image).' },
        scale: { type: 'number', description: 'Factor (2/4/8) para imagen.' },
      },
      required: ['inputImage'],
    },
  },
  {
    name: 'fydesign_animate',
    description: '🎞️ ANIMATE: anima un still, aplica movimiento de referencia, recastea un personaje o interpola entre inicio y final. Devuelve MP4 y puede consumir créditos del proveedor conectado.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string' },
        op: { type: 'string', enum: ['animate', 'recast', 'reference', 'start-end'], description: 'Modo.' },
        inputImage: { type: 'string', description: 'animate: still; recast: video; reference: imagen de referencia.' },
        prompt: { type: 'string', description: 'Descripción del movimiento/escena.' },
        styleKey: { type: 'string', description: 'animate: clave de movimiento de cámara del catálogo.' },
        drivingVideo: { type: 'string', description: 'animate: URL de video que aporta el movimiento.' },
        characterRef: { type: 'string', description: 'recast: imagen del personaje a insertar.' },
        startImage: { type: 'string', description: 'start-end: primer frame.' },
        endImage: { type: 'string', description: 'start-end: último frame.' },
        duration: { type: 'number', description: 'Duración (s).' },
        model: { type: 'string', description: 'Override del endpoint Muapi.' },
      },
      required: ['op'],
    },
  },
  {
    name: 'fydesign_refine',
    description: '🧠 BRIEF REFINER (director creativo el modelo configurado): de un brief crudo, el modelo configurado decide DINÁMICAMENTE cuántas preguntas aclaratorias hacen falta (1 a 15, según el caso) — cada una con opciones — y reescribe un brief MEJOR que el tuyo, on-brand y sin inventar datos. Pasa `answers` (respuestas previas) para re-pensar con menos/cero preguntas. Devuelve { questions, refinedBrief, assumptions, rationale }.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string', description: 'Marca guardada.' },
        prompt: { type: 'string', description: 'El brief crudo a refinar.' },
        kind: { type: 'string', description: 'Tipo de pieza (video-ad, image, post, carousel…).' },
        answers: { type: 'array', description: 'Respuestas a preguntas previas para re-pensar.', items: { type: 'object', properties: { q: { type: 'string' }, a: { type: 'string' } }, required: ['q', 'a'] } },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'fydesign_moodboard',
    description: '🎨 MOODBOARD (Higgsfield Moodboards): el modelo configurado destila un set de imágenes de referencia en un descriptor de estilo estructurado (paleta, encuadre, luz, mood, texturas, era) reutilizable como look de marca. Devuelve el descriptor + lo guarda.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string' },
        refImages: { type: 'array', items: { type: 'string' }, description: 'Imágenes de referencia del moodboard (rutas/URLs/dataURLs).' },
        prompt: { type: 'string', description: 'Nombre/etiqueta del moodboard (opcional).' },
      },
      required: ['refImages'],
    },
  },
  {
    name: 'fydesign_autoroute',
    description: '🧭 AUTO-ROUTER (Higgsfield multi-model hub): el modelo configurado clasifica tu brief y elige el MEJOR modelo de Muapi/Vertex (imagen/video/edición/audio…) para el trabajo, con justificación. No genera — recomienda el modelo.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string' },
        prompt: { type: 'string', description: 'Qué quieres lograr.' },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'fydesign_virality',
    description: '📈 VIRALITY SCORE (Higgsfield virality predictor): el modelo configurado puntúa un concepto/idea 0-100 (hook, claridad, shareability) con razones y arreglos accionables. Úsalo para elegir el mejor concepto antes de gastar en render.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string' },
        prompt: { type: 'string', description: 'El concepto/idea/caption a evaluar.' },
        platform: { type: 'string', description: 'Plataforma destino (tiktok, instagram, youtube…).' },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'fydesign_angles',
    description: '🔄 ANGLES (Higgsfield Angles 2.0): de UNA imagen genera vistas del MISMO sujeto desde otros ángulos de cámara (3/4, perfil, picado, contrapicado…), misma identidad y luz.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string' },
        inputImage: { type: 'string', description: 'Imagen base (ruta/URL/dataURL).' },
        count: { type: 'number', description: 'Cuántos ángulos (default 4).' },
      },
      required: ['inputImage'],
    },
  },
  {
    name: 'fydesign_product_shots',
    description: '🛍️ PRODUCT SHOTS (Marketing Studio / Product Placement): de la foto REAL de tu producto genera varias tomas hero on-brand componiendo el producto en escenas distintas, preservando su logo/etiqueta/forma. Para un anuncio en video usa fydesign_product_ad con productImage.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string' },
        productImage: { type: 'string', description: 'Foto real del producto (ruta/URL/dataURL).' },
        count: { type: 'number', description: 'Cuántas tomas (default 3).' },
        platform: { type: 'string', enum: ['instagram-feed', 'instagram-story', 'tiktok', 'facebook'], description: 'Aspect ratio.' },
      },
      required: ['productImage'],
    },
  },
  {
    name: 'fydesign_product_photoshoot',
    description: '📸 PRODUCT PHOTOSHOOT (Higgsfield-style): de la foto REAL de tu producto, elige un MODO con nombre y obtén fotos de producto curadas (el producto se mantiene idéntico). Modos: product_shot (catálogo en blanco), lifestyle_scene, closeup_with_person, moodboard_pin, hero_banner, social_carousel, ad_creative_pack, virtual_model_tryout, conceptual_product, restyle.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string' },
        productImage: { type: 'string', description: 'Foto real del producto (ruta/URL/dataURL).' },
        shootMode: { type: 'string', enum: ['product_shot', 'lifestyle_scene', 'closeup_with_person', 'moodboard_pin', 'hero_banner', 'social_carousel', 'ad_creative_pack', 'virtual_model_tryout', 'conceptual_product', 'restyle'], description: 'Modo de la sesión (default product_shot).' },
        prompt: { type: 'string', description: 'Detalle/escena opcional para afinar el modo.' },
        count: { type: 'number', description: 'Cuántas imágenes (1-6, default 1).' },
        aspectRatio: { type: 'string', enum: ['1:1', '16:9', '9:16', '4:3', '3:4'], description: 'Override del aspect (default según el modo).' },
      },
      required: ['productImage'],
    },
  },
  {
    name: 'fydesign_marketplace_card',
    description: '🏷️ MARKETPLACE CARD (Higgsfield-style): coloca tu producto REAL en una ficha de listing fiel a la plataforma (Amazon, Etsy, Shopify, eBay, MercadoLibre, App Store, thumbnail de YouTube, tarjeta testimonial). El producto se compone foto-real con Nano Banana Pro y el texto (título/precio/CTA) se renderiza nítido en HTML/CSS — NUNCA inventa precio/título: solo imprime lo que pasas. Sin ratings/badges falsos.',
    inputSchema: {
      type: 'object',
      properties: {
        brand: { type: 'string' },
        productImage: { type: 'string', description: 'Foto real del producto (ruta/URL/dataURL).' },
        cardTemplate: { type: 'string', enum: ['amazon', 'etsy', 'shopify', 'ebay', 'mercadolibre', 'app_store', 'thumbnail', 'review_badge'], description: 'Plantilla de ficha (default amazon).' },
        cardTitle: { type: 'string', description: 'Título/headline REAL a imprimir (si falta, usa el nombre de la marca).' },
        cardPrice: { type: 'string', description: 'Precio REAL a imprimir tal cual, ej. "$49.99". Si falta, no se muestra precio (anti-invención).' },
        prompt: { type: 'string', description: 'Detalle/escena opcional para la composición del producto.' },
        count: { type: 'number', description: 'Cuántas variantes (1-4, default 1).' },
      },
      required: ['productImage'],
    },
  },
  {
    name: 'fydesign_instant',
    description: 'Crea una suite desde un sitio o referencias sin registrar marca: analiza el producto, decide concepto, piezas, narrativa y copy, y compone titular, CTA y logo con autocrítica. Usa dryRun:true para revisar el plan antes de autorizar proveedores de generación.',
    inputSchema: {
      type: 'object',
      properties: {
        siteUrl: { type: 'string', description: 'URL del sitio web de la marca (ej. https://tumarca.com). Opcional si pasas refImages.' },
        refImages: { type: 'array', items: { type: 'string' }, description: 'Fotos REALES del producto a promocionar (rutas/URLs/dataURLs). el modelo configurado las VE y compone el marketing con ellas. Opcional si pasas siteUrl.' },
        prompt: { type: 'string', description: 'Brief opcional (qué enfatizar). Si falta, el modelo configurado decide el pack más efectivo él mismo.' },
        dryRun: { type: 'boolean', description: 'true = devuelve el plan, copy y briefs sin generar medios. El razonamiento puede usar el modelo principal conectado.' },
        suite: { type: 'array', items: { type: 'string', enum: ['posts', 'carousel', 'story', 'ad', 'video'] }, description: 'Pistas de qué generar. Default ["posts","story","video"]. el modelo configurado tiene libertad sobre el formato real de cada pieza.' },
        count: { type: 'number', description: 'Pista de nº de piezas (el modelo configurado decide el total final).' },
        brandName: { type: 'string', description: 'Nombre de marca (útil cuando no pasas siteUrl).' },
        saveAsBrand: { type: 'boolean', description: 'Guardar la identidad derivada del sitio como marca reutilizable en Neon (default false = efímera).' },
        quality: { type: 'string', enum: ['brand', 'ultra', 'standard', 'fast'], description: 'Calidad de imagen (default brand = Nano Banana, compone tu producto real vía referencias).' },
        duration: { type: 'number', description: 'Duración del video corto en segundos (default 5).' },
      },
    },
  },
];

async function runTool(name, args) {
  args = args || {};
  if (name === 'fydesign_brands') return runGen({ list: true });
  if (name === 'fydesign_register_brand') {
    if (!args.brand) throw new Error('brand (nombre) es obligatorio');
    return runGen({
      media: 'register',
      brand: args.brand,
      regColors: args.colors,
      regLogo: args.logo,
      regAssets: args.assets,
      regFonts: args.fonts,
      regFacts: args.facts,
      regBlurb: args.blurb,
      regRepo: args.repo,
      regId: args.id,
    });
  }
  if (name === 'fydesign_generate') {
    if (!args.prompt) throw new Error('prompt es obligatorio');
    return runGen({
      brand: args.brand,
      repo: args.repo,
      prompt: args.prompt,
      slides: args.slides,
      platform: args.platform,
    });
  }
  if (name === 'fydesign_image') {
    if (!args.prompt) throw new Error('prompt es obligatorio');
    return runGen({
      media: 'image',
      brand: args.brand,
      repo: args.repo,
      prompt: args.prompt,
      provider: args.provider,
      model: args.model,
      count: args.count,
      quality: args.quality,
      style: args.style,
      styleKey: args.styleKey,
      colorLock: args.colorLock,
      platform: args.platform,
      aspectRatio: args.aspectRatio,
      sizes: args.sizes,
    });
  }
  if (name === 'fydesign_post') {
    if (!args.prompt) throw new Error('prompt es obligatorio');
    return runGen({
      media: 'post',
      brand: args.brand,
      repo: args.repo,
      prompt: args.prompt,
      quality: args.quality,
      style: args.style,
      count: args.count,
      platform: args.platform,
      sizes: args.sizes,
    });
  }
  if (name === 'fydesign_edit') {
    if (!args.inputImage || !args.prompt) throw new Error('inputImage y prompt son obligatorios');
    return runGen({
      media: 'edit',
      brand: args.brand,
      inputImage: args.inputImage,
      prompt: args.prompt,
      style: args.style,
      count: args.count,
      platform: args.platform,
    });
  }
  if (name === 'fydesign_campaign') {
    if (!args.prompt) throw new Error('prompt es obligatorio');
    return runGen({
      media: 'campaign',
      brand: args.brand,
      prompt: args.prompt,
      count: args.count,
      quality: args.quality,
      style: args.style,
      platform: args.platform,
    });
  }
  if (name === 'fydesign_strategy') {
    if (!args.brief) throw new Error('brief es obligatorio');
    return runGen({
      media: 'campaign',
      godMode: true,
      brand: args.brand,
      prompt: args.brief,
      count: args.pieces,
      quality: args.quality,
      platform: args.platform,
    });
  }
  if (name === 'fydesign_svg') {
    if (!args.prompt) throw new Error('prompt es obligatorio');
    return runGen({ media: 'svg', brand: args.brand, prompt: args.prompt });
  }
  if (name === 'fydesign_video') {
    if (!args.prompt) throw new Error('prompt es obligatorio');
    return runGen({
      media: 'video',
      brand: args.brand,
      repo: args.repo,
      prompt: args.prompt,
      model: args.model,
      duration: args.duration,
      platform: args.platform,
    });
  }
  if (name === 'fydesign_video_ad') {
    if (!args.prompt) throw new Error('prompt es obligatorio');
    return runGen({
      media: 'video-ad',
      brand: args.brand,
      prompt: args.prompt,
      shots: args.shots,
      withVoiceover: args.withVoiceover,
      withMusic: args.withMusic,
      withCaptions: args.withCaptions,
      tier: args.tier,
      model: args.model,
      engine: args.engine,
      preset: args.preset,
      duration: args.duration,
      refImages: args.refImages,
      cinemaBody: args.cinemaBody,
      genre: args.genre,
      colorGrade: args.colorGrade,
      speedRamp: args.speedRamp,
      style: args.style,
      platform: args.platform,
      aspectRatio: args.aspectRatio,
    });
  }
  if (name === 'fydesign_analyze_video') {
    if (!args.url && !args.file) throw new Error('pasa url (YouTube/TikTok/…) o file (ruta/URL del video)');
    return runGen({ media: 'analyze', videoUrl: args.url, videoFile: args.file, analyzeFrames: args.frames });
  }
  if (name === 'fydesign_clipper') {
    if (!args.url && !args.file) throw new Error('pasa url (video largo) o file (ruta/URL del video)');
    return runGen({ media: 'clip', videoUrl: args.url, videoFile: args.file, clipCount: args.count, clipLength: args.clipLength });
  }
  if (name === 'fydesign_ad_engine') {
    return runGen({ media: 'ad-engine', brand: args.brand, prompt: args.prompt, adNiches: args.niches, adGenerate: args.generate, model: args.model, platform: args.platform });
  }
  if (name === 'fydesign_product_ad') {
    if (!args.prompt) throw new Error('prompt es obligatorio');
    return runGen({
      media: 'product-ad',
      brand: args.brand,
      prompt: args.prompt,
      productUrl: args.productUrl,
      productImage: args.productImage,
      shots: args.shots,
      withVoiceover: args.withVoiceover,
      withMusic: args.withMusic,
      withCaptions: args.withCaptions,
      tier: args.tier,
      model: args.model,
      cinemaBody: args.cinemaBody,
      genre: args.genre,
      colorGrade: args.colorGrade,
      speedRamp: args.speedRamp,
      platform: args.platform,
      aspectRatio: args.aspectRatio,
    });
  }
  if (name === 'fydesign_influencer') {
    return runGen({
      media: 'persona',
      brand: args.brand,
      personaAction: args.action,
      personaName: args.personaName,
      prompt: args.prompt,
      refImages: args.refImages,
      count: args.count,
      platform: args.platform,
    });
  }
  if (name === 'fydesign_talking_head') {
    if (!args.personaName) throw new Error('personaName es obligatorio');
    return runGen({
      media: 'talking-head',
      brand: args.brand,
      personaName: args.personaName,
      prompt: args.prompt,
      voiceText: args.script || args.prompt,
      lipsyncModel: args.lipsyncModel,
    });
  }
  if (name === 'fydesign_photo_dump') {
    if (!args.prompt || !args.refImages) throw new Error('prompt y refImages son obligatorios');
    return runGen({
      media: 'photo-dump',
      brand: args.brand,
      prompt: args.prompt,
      refImages: args.refImages,
      count: args.count,
      platform: args.platform,
    });
  }
  if (name === 'fydesign_batch') {
    if (!args.prompt) throw new Error('prompt es obligatorio');
    return runGen({
      media: 'batch',
      brand: args.brand,
      prompt: args.prompt,
      batchCount: args.count,
      quality: args.quality,
      platform: args.platform,
    });
  }
  if (name === 'fydesign_studio') {
    if (!args.inputImage || !args.op) throw new Error('inputImage y op son obligatorios');
    return runGen({ media: 'edit-pro', brand: args.brand, inputImage: args.inputImage, editOp: args.op, prompt: args.prompt, editRef: args.editRef, styleKey: args.styleKey, aspectRatio: args.aspectRatio });
  }
  if (name === 'fydesign_photodump') {
    if (!args.personaName) throw new Error('personaName es obligatorio');
    return runGen({ media: 'photodump', brand: args.brand, personaName: args.personaName, count: args.count });
  }
  if (name === 'fydesign_instadump') {
    if (!args.inputImage) throw new Error('inputImage es obligatorio');
    return runGen({ media: 'instadump', brand: args.brand, inputImage: args.inputImage, trendKeys: args.trendKeys, count: args.count });
  }
  if (name === 'fydesign_ambassador') {
    if (!args.personaName || !args.prompt) throw new Error('personaName y prompt son obligatorios');
    return runGen({ media: 'ambassador', brand: args.brand, personaName: args.personaName, prompt: args.prompt, count: args.count });
  }
  if (name === 'fydesign_train_face') {
    if (!args.refImages) throw new Error('refImages es obligatorio');
    return runGen({ media: 'train-face', brand: args.brand, personaName: args.personaName, refImages: args.refImages });
  }
  if (name === 'fydesign_storyboard') {
    if (!args.prompt) throw new Error('prompt es obligatorio');
    return runGen({ media: 'storyboard', brand: args.brand, prompt: args.prompt, frames: args.frames, platform: args.platform });
  }
  if (name === 'fydesign_upscale') {
    if (!args.inputImage) throw new Error('inputImage es obligatorio');
    return runGen({ media: 'upscale', brand: args.brand, inputImage: args.inputImage, upscaleTarget: args.target, upscaleScale: args.scale });
  }
  if (name === 'fydesign_animate') {
    if (!args.op) throw new Error('op es obligatorio');
    return runGen({ media: 'animate', brand: args.brand, animateOp: args.op, inputImage: args.inputImage, prompt: args.prompt, styleKey: args.styleKey, drivingVideo: args.drivingVideo, characterRef: args.characterRef, startImage: args.startImage, endImage: args.endImage, duration: args.duration, model: args.model });
  }
  if (name === 'fydesign_refine') {
    if (!args.prompt) throw new Error('prompt es obligatorio');
    return runGen({ media: 'refine', brand: args.brand, prompt: args.prompt, kind: args.kind, refineAnswers: args.answers });
  }
  if (name === 'fydesign_moodboard') {
    if (!args.refImages) throw new Error('refImages es obligatorio');
    return runGen({ media: 'moodboard', brand: args.brand, refImages: args.refImages, prompt: args.prompt });
  }
  if (name === 'fydesign_autoroute') {
    if (!args.prompt) throw new Error('prompt es obligatorio');
    return runGen({ media: 'autoroute', brand: args.brand, prompt: args.prompt });
  }
  if (name === 'fydesign_virality') {
    if (!args.prompt) throw new Error('prompt es obligatorio');
    return runGen({ media: 'virality', brand: args.brand, prompt: args.prompt, platform: args.platform });
  }
  if (name === 'fydesign_angles') {
    if (!args.inputImage) throw new Error('inputImage es obligatorio');
    return runGen({ media: 'angles', brand: args.brand, inputImage: args.inputImage, count: args.count });
  }
  if (name === 'fydesign_product_shots') {
    if (!args.productImage) throw new Error('productImage es obligatorio');
    return runGen({ media: 'product-shots', brand: args.brand, productImage: args.productImage, count: args.count, platform: args.platform });
  }
  if (name === 'fydesign_product_photoshoot') {
    if (!args.productImage) throw new Error('productImage es obligatorio');
    return runGen({ media: 'product-photoshoot', brand: args.brand, productImage: args.productImage, shootMode: args.shootMode, prompt: args.prompt, count: args.count, aspectRatio: args.aspectRatio });
  }
  if (name === 'fydesign_marketplace_card') {
    if (!args.productImage) throw new Error('productImage es obligatorio');
    return runGen({ media: 'marketplace-card', brand: args.brand, productImage: args.productImage, cardTemplate: args.cardTemplate, cardTitle: args.cardTitle, cardPrice: args.cardPrice, prompt: args.prompt, count: args.count });
  }
  if (name === 'fydesign_instant') {
    if (!args.siteUrl && !(Array.isArray(args.refImages) && args.refImages.length)) {
      throw new Error('pasa siteUrl (URL de la marca) y/o refImages (fotos del producto)');
    }
    return runGen({ media: 'instant', siteUrl: args.siteUrl, refImages: args.refImages, prompt: args.prompt, dryRun: args.dryRun, suite: args.suite, count: args.count, brandName: args.brandName, saveAsBrand: args.saveAsBrand, quality: args.quality, duration: args.duration });
  }
  throw new Error(`Herramienta desconocida: ${name}`);
}

// ─── JSON-RPC / MCP stdio plumbing ───────────────────────────────────────────

function send(msg) { process.stdout.write(JSON.stringify(msg) + '\n'); }
function reply(id, result) { send({ jsonrpc: '2.0', id, result }); }
function replyError(id, code, message) { send({ jsonrpc: '2.0', id, error: { code, message } }); }

async function handle(msg) {
  const { id, method, params } = msg;
  const isRequest = id !== undefined && id !== null;
  switch (method) {
    case 'initialize':
      reply(id, {
        protocolVersion: params?.protocolVersion || '2025-06-18',
        capabilities: { tools: { listChanged: false } },
        serverInfo: SERVER_INFO,
      });
      return;
    case 'notifications/initialized':
    case 'initialized':
      return;
    case 'ping':
      if (isRequest) reply(id, {});
      return;
    case 'tools/list':
      reply(id, { tools: TOOLS });
      return;
    case 'tools/call': {
      try {
        const result = await runTool(params?.name, params?.arguments);
        reply(id, { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }], isError: false });
      } catch (e) {
        reply(id, { content: [{ type: 'text', text: `Error: ${e.message}` }], isError: true });
      }
      return;
    }
    default:
      if (isRequest) replyError(id, -32601, `Method not found: ${method}`);
      return;
  }
}

// Read newline-delimited JSON-RPC; flush in-flight handlers before exiting.
let buffer = '';
let ended = false;
const pending = new Set();
function track(p) {
  pending.add(p);
  p.finally(() => { pending.delete(p); if (ended && pending.size === 0) process.exit(0); });
}
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  buffer += chunk;
  let nl;
  while ((nl = buffer.indexOf('\n')) !== -1) {
    const line = buffer.slice(0, nl).trim();
    buffer = buffer.slice(nl + 1);
    if (!line) continue;
    let msg;
    try { msg = JSON.parse(line); } catch { continue; }
    track(handle(msg).catch((e) => {
      if (msg && msg.id !== undefined && msg.id !== null) replyError(msg.id, -32603, String(e && e.message));
    }));
  }
});
process.stdin.on('end', () => {
  ended = true;
  // Wait for in-flight handlers to settle, THEN exit (via track()'s finally).
  // No fixed safety timer here: a generation can take ~90s, and runGen already
  // bounds each call with GEN_TIMEOUT — a short net would cut real work off.
  if (pending.size === 0) process.exit(0);
});

process.stderr.write(`[fydesign-mcp] ready → repo: ${FYDESIGN_DIR} (one-shot, provider-neutral)\n`);
