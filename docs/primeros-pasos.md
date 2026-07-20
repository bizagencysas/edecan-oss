# Primeros pasos

Guía del primer arranque de Edecán desde la perspectiva de quien lo usa (no de quien lo despliega — para eso ver [`self-hosting.md`](./self-hosting.md) y [`configuracion.md`](./configuracion.md), que cubren variables de entorno a nivel de instancia). Cubre exactamente lo que ves en pantalla: registro → wizard de bienvenida → Configuración → chat.

Principio que gobierna todo este recorrido: **conectar tu inteligencia (LLM) es lo único obligatorio para chatear.** Todo lo demás — voz, teléfono, correo/calendario, redes sociales, bots de mensajería — es opcional, se configura cuando quieras, y nunca bloquea el primer uso.

## 1. Crea tu cuenta

En `/register` creas tu espacio (tenant): nombre de tu empresa o espacio, correo y contraseña (mínimo 8 caracteres). Al enviar el formulario quedas con sesión iniciada de inmediato — no hay paso de verificación de correo intermedio.

Si es la primera vez que este navegador termina un registro (no existe todavía `edecan_wizard_done` en su almacenamiento local), caes en el wizard de bienvenida (`/app/bienvenida`) en vez de directo al chat. Si ya lo completaste o lo saltaste antes en este mismo navegador, vas directo a `/app`.

## 2. El wizard de bienvenida (máximo 3 pasos)

Pantalla corta, con barra de progreso arriba. La barra lateral con el resto de la app (Chat, Persona, Conectores…) sigue visible en todo momento — nunca quedas encerrado en el wizard, puedes irte a cualquier otra sección con un clic cuando quieras.

### Paso 1 — Conecta tu inteligencia

La misma pantalla que verás luego en la tarjeta "Inteligencia (LLM)" de Configuración (es literalmente el mismo componente). Dos grupos de opciones, del más simple al más manual:

**Un clic — usa lo que ya tienes instalado.** Solo aparece si Edecán corre en tu propia máquina (modo local/escritorio) y detectó alguna de estas herramientas ya instaladas y autenticadas:

- **Claude CLI** — usa tu sesión/suscripción de Claude Code ya paga, sin pedir ninguna API key aparte.
- **Codex CLI** — mismo concepto con el CLI de OpenAI.
- **Ollama** — si tienes modelos locales descargados, eliges cuál usar de un desplegable; cero costo, cero llamadas externas.

Si no detecta nada de esto (o corres contra un servidor hospedado, donde no aplica), pasas directo al segundo grupo.

**Pegar y validar — con una API key.** Un campo, un botón "Conectar" que prueba la key al toque y muestra ✅ conectado o el error exacto del proveedor. Nunca hace falta editar ningún archivo a mano.

| Proveedor | Dónde sacar tu key |
|---|---|
| Anthropic | https://console.anthropic.com/settings/keys |
| Compatible con OpenAI (OpenAI, Groq, Together.ai, un LLM propio…) | https://platform.openai.com/api-keys |
| Vertex AI / Gemini (camino simple: solo una key) | https://aistudio.google.com/apikey |

Vertex AI también tiene un modo avanzado (cuenta de servicio de Google Cloud: JSON + Project ID + región) para quien ya tiene un proyecto GCP propio — queda colapsado tras "Opciones avanzadas" a propósito, no es el camino que ve nadie por defecto.

¿No quieres decidir todavía? El enlace "Seguir sin conectar" te deja avanzar sin conectar nada — puedes volver a esto cuando quieras desde Configuración. El chat simplemente no va a poder generar respuestas hasta que conectes algún LLM.

### Paso 2 — Voz (opcional)

Transcripción de voz a texto (STT) y síntesis de texto a voz (TTS). Si no conectas nada aquí, la voz sigue funcionando con un stub (sin llamadas reales) — no afecta al chat de texto para nada.

| Función | Proveedor | Dónde sacar tu key |
|---|---|---|
| Voz a texto (STT) | Deepgram | https://console.deepgram.com |
| Texto a voz (TTS) | ElevenLabs | https://elevenlabs.io |
| Texto a voz (TTS), alternativa | Amazon Polly | No pide key propia — usa las credenciales de AWS ya configuradas en tu instancia; aquí solo eliges qué voz usar (p. ej. `Lupe` para español). |

Botón "Saltar" siempre disponible — no es necesario configurar voz para seguir.

### Paso 3 — ¡Listo!

Pantalla de cierre con un botón grande "Empezar a chatear" que te lleva a `/app`. A partir de aquí, este navegador no vuelve a mostrarte el wizard automáticamente (queda guardado `edecan_wizard_done=1` en `localStorage` — nunca viaja al servidor, es puramente una preferencia local del navegador).

## 3. Después del wizard: la pantalla de Configuración

`/app/configuracion` es el mismo centro de credenciales al que vuelves cuando quieras cambiar algo — configuración progresiva en tarjetas independientes, nunca un formulario único gigante:

- **Inteligencia (LLM)** — la única obligatoria para chatear. Si no está conectada, un aviso arriba de toda la pantalla te lo recuerda ("Conecta tu LLM para empezar a chatear — todo lo demás es opcional"), pero no te bloquea nada más.
- **Voz** — Deepgram (STT) y ElevenLabs/Polly (TTS), igual que el paso 2 del wizard.
- **Telefonía (Twilio)** — llamadas y SMS con tu propia cuenta de Twilio; se conecta desde Conectores.
- **Conectores** (Google, Microsoft, Meta, X, YouTube, Slack) — correo, calendario y redes sociales vía OAuth, cada uno con tu propia cuenta.
- **Mensajería** (Telegram, Discord) — el bot token de tu propio bot.

Cada tarjeta muestra su estado actual (proveedor, modelo cuando aplica, y la key enmascarada — nunca la key completa) o "Sin conectar". El botón "Configurar"/"Cambiar" abre el mismo panel de conexión; "Quitar" borra la credencial guardada. Ninguna key pasa nunca por tu navegador más que en el instante de enviarla al backend — no se guarda en `localStorage` ni en ningún sitio del lado del cliente.

## 4. Notas para quien construye el build de escritorio

El frontend (`apps/web`, Next.js 14) soporta un modo de export estático pensado para empaquetarse dentro de la app de escritorio Tauri:

```bash
NEXT_OUTPUT=export NEXT_PUBLIC_API_URL='' npm run build
```

Esto genera HTML/CSS/JS estático (carpeta `out/`) sin necesitar un servidor Next corriendo — el backend local empaquetado en la app de escritorio sirve tanto la API como estos archivos estáticos desde el mismo origen.

**Por qué `NEXT_PUBLIC_API_URL=''` (vacío) y no simplemente omitirla:** con la variable vacía, el fetch de la API queda relativo (`'' + '/v1/credentials'` → same-origin), en vez de apuntar a una URL absoluta fija. `lib/api-configuracion.ts` (esta pantalla) maneja esto con `process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ?? "http://localhost:8000"` — el operador `??` respeta un vacío explícito y solo cae al default cuando la variable ni siquiera está definida. **Actualización (verificado 2026-07-09, fase v7): `lib/api.ts` (compartido, usado por el resto de la app) YA usa ese mismo `??`** — este párrafo antes advertía que todavía resolvía la variable con `||` (lo que sí habría convertido un vacío explícito de vuelta a `http://localhost:8000`), pero eso ya no es cierto contra el código real (`lib/api.ts` línea 39; confirmado además que cada `lib/api-*.ts` que importa `API_BASE_URL` desde ahí hereda el mismo `??`, sin ninguna ocurrencia de `||` restante para este patrón en todo `apps/web/src/lib/`). Same-origin real en modo escritorio para todas las pantallas, no solo para esta.
