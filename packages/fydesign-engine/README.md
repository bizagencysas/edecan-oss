# Edecán FyDesign Engine

Motor local del Studio de Edecán. Expone 36 herramientas creativas reales por
MCP y un `fydesign_health` sintético en el adaptador Python (37 capacidades en
total). Incluye generación y edición de imágenes, campañas, SVG, video,
storyboards, producto, personas, análisis, clips, upscale y variantes.

La [matriz de capacidades](./CAPABILITIES.md) distingue qué acciones son
seguras o premium, qué dependencias usan y cuándo Edecán pide confirmación. El
[manifiesto de portado](./PORTING_MANIFEST.json) fija la procedencia, cobertura
de las dos capas del motor y hashes de todos los archivos TypeScript/MJS.

## Frontera de seguridad

- No lee `.env` ni hereda secretos del backend.
- Edecán le pasa únicamente variables permitidas desde `TokenVault`.
- Las marcas se guardan en el JSON privado indicado por
  `FYDESIGN_STORE_PATH`; no requiere Neon ni incluye datos de terceros.
- No contiene la app Next, autenticación, SQL, despliegues, tests privados,
  `.git`, `.next`, `node_modules` ni archivos de propietarios.
- Una operación que requiere un proveedor ausente falla con un mensaje real;
  nunca devuelve un artefacto simulado como si se hubiese generado.

## Desarrollo reproducible

```bash
npm ci
npm run typecheck
npm test
```

El cliente principal es `edecan_design_studio.engine.StudioEngineClient`.

## Runtime de escritorio

El instalador nativo agrega Node, Chromium, ffmpeg, ffprobe y yt-dlp para que
las capacidades creativas no dependan de herramientas globales del equipo. Los
builds fijan versiones y verifican checksums antes de empaquetar. ffmpeg,
ffprobe y yt-dlp son ejecutables independientes con sus propias licencias; ver
[`NOTICE`](./NOTICE). Apache-2.0 cubre este motor, no relicencia esos binarios.
