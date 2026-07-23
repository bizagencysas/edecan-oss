import dns from 'node:dns';
import http from 'node:http';

import { assertSafeOutboundUrl } from '../src/lib/network-guard';

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

for (const target of [
  'http://127.0.0.1/private',
  'http://169.254.169.254/latest/meta-data',
  'http://[::1]/private',
  'file:///etc/passwd',
  'http://user:secret@example.com/',
]) {
  let blocked = false;
  try {
    assertSafeOutboundUrl(target);
  } catch {
    blocked = true;
  }
  invariant(blocked, `network guard accepted ${target}`);
}

assertSafeOutboundUrl('https://example.com/public');

// A public-looking hostname that resolves to loopback must be rejected at the
// actual socket lookup, not only by the URL parser.
const originalLookup = dns.lookup;
Object.defineProperty(dns, 'lookup', {
  configurable: true,
  value: (_hostname: string, _options: unknown, callback: Function) =>
    callback(null, [{ address: '127.0.0.1', family: 4 }]),
});
const server = http.createServer((_request, response) => response.end('private'));
await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
let rebindingBlocked = false;
try {
  await fetch('http://public-looking.invalid/').catch(() => {
    rebindingBlocked = true;
  });
} finally {
  Object.defineProperty(dns, 'lookup', { configurable: true, value: originalLookup });
  await new Promise<void>((resolve, reject) =>
    server.close((error) => (error ? reject(error) : resolve())),
  );
}
invariant(rebindingBlocked, 'DNS rebinding destination was not blocked');

console.log('network guard smoke: ok');
