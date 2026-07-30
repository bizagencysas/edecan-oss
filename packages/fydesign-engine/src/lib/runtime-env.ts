/** Minimal environment for a Claude CLI subprocess.
 *
 * The builder may hold provider keys for image, video and storage. The
 * language-model child never needs them and must not be able to inspect them.
 */
const CLAUDE_CLI_ENV_ALLOWLIST = [
  'PATH',
  'HOME',
  'LANG',
  'LC_ALL',
  'TMPDIR',
  'CLAUDE_CLI_MODEL',
] as const;

export function safeClaudeCliEnv(): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {};
  for (const key of CLAUDE_CLI_ENV_ALLOWLIST) {
    const value = process.env[key];
    if (value) env[key] = value;
  }
  return env;
}

/** Claude Code in text-only, non-agentic mode.
 *
 * OAuth/keychain remains available, while tools, skills, hooks, Chrome and
 * session persistence are disabled. The prompt can be supplied on stdin or as
 * the optional positional argument.
 */
export function claudeTextOnlyArgs(prompt?: string): string[] {
  const args = [
    '-p',
    ...(prompt === undefined ? [] : [prompt]),
    '--tools',
    '',
    '--safe-mode',
    '--disable-slash-commands',
    '--no-session-persistence',
    '--no-chrome',
  ];
  return args;
}

export function requiredRuntimePath(
  name: 'FYDESIGN_OUTPUT_ROOT' | 'FYDESIGN_STATE_ROOT',
): string {
  const value = (process.env[name] || '').trim();
  if (!value) {
    throw new Error(`${name} no está configurado; Edecán debe asignar un directorio privado.`);
  }
  return value;
}
