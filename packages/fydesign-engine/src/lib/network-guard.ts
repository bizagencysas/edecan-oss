/**
 * Process-wide outbound network boundary for the local FyDesign runtime.
 *
 * The Python tool layer validates user input before spawning this process. This
 * second boundary validates the destination that Node actually connects to, so
 * redirects and DNS changes cannot pivot Studio into localhost, a private LAN,
 * link-local services, or cloud metadata endpoints.
 */
import dns from 'node:dns';
import { BlockList, isIP, type LookupFunction } from 'node:net';

import { Agent, setGlobalDispatcher, type Dispatcher } from 'undici';

const blocked = new BlockList();

for (const [network, prefix] of [
  ['0.0.0.0', 8],
  ['10.0.0.0', 8],
  ['100.64.0.0', 10],
  ['127.0.0.0', 8],
  ['169.254.0.0', 16],
  ['172.16.0.0', 12],
  ['192.0.0.0', 24],
  ['192.0.2.0', 24],
  ['192.88.99.0', 24],
  ['192.168.0.0', 16],
  ['198.18.0.0', 15],
  ['198.51.100.0', 24],
  ['203.0.113.0', 24],
  ['224.0.0.0', 4],
  ['240.0.0.0', 4],
] as const) {
  blocked.addSubnet(network, prefix, 'ipv4');
}

for (const [network, prefix] of [
  ['::', 128],
  ['::1', 128],
  ['::ffff:0:0', 96],
  ['100::', 64],
  ['2001:db8::', 32],
  ['fc00::', 7],
  ['fe80::', 10],
  ['ff00::', 8],
] as const) {
  blocked.addSubnet(network, prefix, 'ipv6');
}

const bridgeOrigin = (() => {
  const raw = process.env.FYDESIGN_LLM_BRIDGE_URL;
  if (!raw) return null;
  try {
    const parsed = new URL(raw);
    if (parsed.protocol !== 'http:' || parsed.hostname !== '127.0.0.1') return null;
    return parsed.origin;
  } catch {
    return null;
  }
})();

function isBlockedAddress(address: string): boolean {
  const family = isIP(address);
  if (family === 4) return blocked.check(address, 'ipv4');
  if (family === 6) return blocked.check(address.split('%', 1)[0], 'ipv6');
  return true;
}

export function assertSafeOutboundUrl(value: string | URL): URL {
  const target = value instanceof URL ? value : new URL(value);
  if (!['http:', 'https:'].includes(target.protocol)) {
    throw new Error('Studio bloqueó un destino con protocolo no permitido.');
  }
  if (target.username || target.password) {
    throw new Error('Studio bloqueó una URL con credenciales incrustadas.');
  }
  if (bridgeOrigin && target.origin === bridgeOrigin) return target;
  const rawHostname = target.hostname.replace(/^\[|\]$/g, '');
  const family = isIP(rawHostname);
  if (family && isBlockedAddress(rawHostname)) {
    throw new Error('Studio bloqueó un destino local, privado o reservado.');
  }
  const hostname = rawHostname.toLowerCase().replace(/\.$/, '');
  if (hostname === 'localhost' || hostname.endsWith('.local')) {
    throw new Error('Studio bloqueó un nombre de red local.');
  }
  return target;
}

const guardedLookup: LookupFunction = (hostname, options, callback) => {
  dns.lookup(
    hostname,
    {
      family: options.family,
      hints: options.hints,
      all: true,
      verbatim: true,
    },
    (error, addresses) => {
      if (error) {
        callback(error, '', 0);
        return;
      }
      if (!addresses.length || addresses.some((entry) => isBlockedAddress(entry.address))) {
        const blockedError = Object.assign(
          new Error('Studio bloqueó una resolución DNS local, privada o reservada.'),
          { code: 'EACCES' },
        );
        callback(blockedError, '', 0);
        return;
      }
      const selected = addresses[0];
      callback(null, selected.address, selected.family);
    },
  );
};

const agent = new Agent({ connect: { lookup: guardedLookup } });
const guardRedirects: Dispatcher.DispatcherComposeInterceptor = (dispatch) =>
  (options, handler) => {
    const origin = options.origin;
    if (!origin) throw new Error('Studio bloqueó una solicitud sin origen verificable.');
    assertSafeOutboundUrl(new URL(options.path, origin));
    return dispatch(options, handler);
  };

// Every redirect is dispatched through the composed dispatcher again. Literal
// IPs are checked by the interceptor and DNS names are pinned to an address that
// passed guardedLookup at the moment the socket is opened.
setGlobalDispatcher(agent.compose(guardRedirects));
