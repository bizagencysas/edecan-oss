# Web Security Module

Load after master `security.md` for Next.js, React, TypeScript, Node.js and browser-facing systems.

## Discovery

- Exact framework/runtime/package versions.
- Public domains, preview URLs and alternate origins.
- Routes, middleware, Server Actions, route handlers and APIs.
- Client/server boundaries and serialized data.
- Authentication/session implementation.
- CSP, cookies, CORS, CSRF and cache behavior.
- Uploads, remote fetches, redirects and image/document rendering.

## Critical tests

### Authorization

- Directly call hidden/admin routes.
- Change object IDs and tenant/workspace selectors.
- Replay requests after role removal/logout.
- Test alternate methods/content types/route variants.
- Verify middleware bypass does not bypass handler/service policy.

### XSS

- Stored/reflected/DOM sinks.
- Markdown, rich text, SVG and filename/metadata rendering.
- Admin/log/export surfaces.
- URL schemes and React escape hatches.
- CSP effectiveness and actual response headers.

### CSRF/CORS

- Cookie-authenticated mutations.
- Origin/referrer/token controls as designed.
- CORS origin allowlist, credentials and preflight.
- Do not mark CORS as authorization.

### Cache

- User/tenant-specific responses not publicly cached.
- Cache key includes all security-relevant dimensions.
- Logout/role change invalidates sensitive cached state.
- Revalidation and CDN behavior tested on deployed staging.

### SSRF

- Remote images/previews/imports/webhooks/browser tools.
- Redirects, DNS rebinding, IPv6/private/link-local/metadata.
- Header/credential forwarding.
- Egress policy.

### Sessions

- Cookie flags and scope.
- Rotation and revocation.
- Reset/magic/invite token single-use and expiry.
- OAuth/OIDC state/nonce/PKCE/issuer/audience.

## Next.js-specific cautions

Research exact installed version and current advisories. Treat Server Actions as remote operations, verify auth inside final operation, review middleware matchers/rewrites, prevent private-data cache leaks, inspect `NEXT_PUBLIC_*`, source maps, preview/draft modes and remote image configuration.

## Release gate

Block on auth bypass, stored XSS in privileged context, cross-tenant cache/data access, exposed secrets, SSRF to internal/metadata targets, session revocation failure or missing tests for a security-critical changed flow.
