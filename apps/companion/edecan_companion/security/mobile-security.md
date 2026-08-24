# Desktop and Mobile Security Module

Load after master `security.md` for Tauri/Rust, iOS/SwiftUI and Android/Kotlin.

## Tauri/Rust

- Exact Tauri version and capability model.
- Deny-by-default capabilities per window/webview.
- Remote/untrusted content receives no privileged native bridge.
- Validate every IPC command argument and caller context.
- No shell strings, arbitrary paths/URLs/executables or broad filesystem scopes.
- CSP/navigation/deep-link controls.
- Secrets in OS credential store; no plaintext logs/config.
- Signed updater with protected signing keys and rollback policy.
- Minimize/review `unsafe`; audit Rust dependencies and parsers.

## iOS

- Keychain and appropriate Data Protection; no secrets in bundle/UserDefaults.
- ATS/TLS validation; pinning only with rotation/recovery design.
- Biometrics protect local access, never replace server authorization.
- Token rotation/revocation and secure OAuth/passkey flows.
- Universal/deep links with state and strict validation.
- WebViews without privileged bridge to untrusted content.
- Minimal entitlements, App Groups, associated domains and SDK data collection.
- App Attest/jailbreak signals are additive, not sole controls.
- Current MASVS/MASTG baseline and platform docs.

## Android

- Components not exported by default; validate intents and PendingIntents.
- Keystore and secure storage; no secrets in resources/BuildConfig/assets.
- Cleartext disabled and TLS validation intact.
- Deep/App Links and custom schemes validated.
- WebView JS bridge/content/navigation tightly controlled; never ignore SSL errors.
- Biometric/Play Integrity/root signals do not replace server auth.
- Minimal permissions and protected signing keys.
- Gradle repositories/plugins/dependencies and third-party SDKs reviewed.
- Current MASVS/MASTG baseline.

## Cross-platform tests

- Extract packaged strings/config for secrets.
- Logout/revocation and offline behavior.
- Backup/restore and screenshot/clipboard leakage.
- Deep-link hijack/state replay.
- WebView/native bridge abuse.
- Local role/premium/admin tampering.
- Network MITM in controlled lab without weakening release config.
- Update/signature/downgrade behavior.
- Server still rejects unauthorized operations regardless of client tampering.
