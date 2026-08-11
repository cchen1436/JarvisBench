# Security policy

JarvisBench `0.1.0-rc.2` is a research preview, not a production service or a
security boundary.

## Known limitations

- Runtime parity currently pins OpenClaw `2026.6.34` on Node `22.22.3`. The
  release lock audit reports zero critical and zero high advisories; six
  moderate and one low advisory remain disclosed. CI rejects any future lock
  with a high or critical finding. The image is not published as `latest`.
- A worker running under the same Unix identity can deliberately read a model
  credential present in its process environment or an accessible mounted
  secret file. File references prevent accidental persistence, not a malicious
  worker. Use a credential broker or stronger process boundary when that threat
  is in scope.
- The OpenClaw Gateway is bound to loopback and each episode uses isolated
  mutable state. Do not expose the Gateway port publicly.

Never include API keys, requester-private profiles, evaluator-only material, or
raw model traces in a security report. Use GitHub private vulnerability
reporting once it is enabled for the repository.

See [privacy](../docs/PRIVACY.md) and
[runtime migration](../docs/RUNTIME_MIGRATION.md) for the complete boundary and
dependency audit.
