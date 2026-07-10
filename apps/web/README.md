# apps/web — frontend (Next.js 14 + TypeScript + Tailwind)

Interfaz web: chat con streaming (SSE), configuración de la persona del asistente ("nivel Dios": nombre, tono, formalidad, instrucciones, memoria), panel de conectores (Google, Microsoft, sociales), voz web (push-to-talk), documentos, recordatorios/contactos/finanzas, uso y facturación.

Consume la API vía `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`, ver `ARCHITECTURE.md` §10.14).

Proyecto Next.js/TypeScript independiente del workspace Python raíz (tiene su propio `package.json`).

## Correr localmente

```bash
cd apps/web
cp .env.local.example .env.local   # ajusta NEXT_PUBLIC_API_URL si tu API no corre en :8000
npm install
npm run dev                        # http://localhost:3000
```

También disponible como `make web` desde la raíz del repo (mismo comando, ver `ARCHITECTURE.md` §8).

Otros scripts: `npm run build` (build de producción), `npm run start` (sirve el build), `npm run lint`.
