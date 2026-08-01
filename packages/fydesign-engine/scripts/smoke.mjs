import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const child = spawn(process.execPath, [path.join(root, 'mcp', 'fydesign-mcp.mjs')], {
  cwd: root,
  env: { PATH: process.env.PATH || '', HOME: process.env.HOME || '' },
  stdio: ['pipe', 'pipe', 'ignore'],
});
child.stdin.write(`${JSON.stringify({
  jsonrpc: '2.0',
  id: 1,
  method: 'initialize',
  params: { protocolVersion: '2025-06-18', capabilities: {} },
})}\n`);
child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'tools/list' })}\n`);

let buffer = '';
for await (const chunk of child.stdout) {
  buffer += chunk.toString('utf8');
  const lines = buffer.trim().split('\n');
  if (lines.length < 2) continue;
  const response = JSON.parse(lines.at(-1));
  const tools = response?.result?.tools;
  if (!Array.isArray(tools) || tools.length !== 36) {
    throw new Error(`Expected 36 MCP tools, received ${tools?.length ?? 'invalid output'}`);
  }
  if (tools.some((tool) => !String(tool.name).startsWith('fydesign_'))) {
    throw new Error('Unexpected tool outside the fydesign namespace');
  }
  child.stdin.end();
  break;
}
await once(child, 'exit');
