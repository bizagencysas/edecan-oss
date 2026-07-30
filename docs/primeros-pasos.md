# Primeros pasos

Esta guía describe el primer uso. La configuración de infraestructura está en
[`configuracion.md`](./configuracion.md).

## 1. Abre Edecán

La aplicación arranca con la inteligencia ya administrada por la instalación.
No hay selector de proveedor, modelo ni pantalla para pegar una API key de
LLM.

El operador configura una sola vez Workers AI:

```dotenv
CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_API_TOKEN=
```

Chat, voz, llamadas y herramientas ligeras usan automáticamente
`@cf/zai-org/glm-4.7-flash`.

## 2. Personaliza tu perfil

Completa tu nombre, forma de trato, idioma y preferencias. Edecán usa ese
perfil y la memoria confirmada en todas las conversaciones.

## 3. Conecta solo lo que necesites

Las capacidades opcionales viven en Ajustes:

- Deepgram y ElevenLabs para voz cloud;
- un proveedor de imágenes;
- Brave o Tavily para búsqueda;
- Twilio y un número propio para llamadas;
- Google, Microsoft, Meta, LinkedIn, X y otros mediante sus flujos oficiales.

Ninguna integración opcional bloquea el chat.

## 4. Conecta el teléfono

En la app de escritorio abre **Configuración → Conectar mi teléfono** y escanea
el QR desde iOS o Android. El código es de un solo uso y el dispositivo conserva
una identidad durable y revocable.

## 5. Habla naturalmente

No necesitas aprender comandos técnicos. Ejemplos:

- “Organiza mis pendientes para hoy.”
- “Revisa este documento y dime qué debo decidir.”
- “Llama a este contacto con mi agente de negocios.”
- “Crea una imagen para este post.”

Edecán elige la ruta adecuada y muestra herramientas, confirmaciones y progreso
en la conversación.

## Para quien construye el escritorio

El frontend soporta export estático para el shell nativo:

```bash
NEXT_OUTPUT=export NEXT_PUBLIC_API_URL='' npm run build
```

La URL vacía mantiene las llamadas same-origin cuando el backend empaquetado
sirve la UI.
