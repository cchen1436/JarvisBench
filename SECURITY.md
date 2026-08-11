# Security policy

JarvisBench `0.1.0-rc.1` is a research preview, not a production service or a
security boundary.

## Known limitations

- Runtime parity currently pins OpenClaw `2026.3.11`. Its locked dependency
  graph has 10 known npm advisories (7 high and 3 critical). The image is kept
  as a private release candidate while a behavior-preserving upgrade is
  validated; it is not published as `latest`.
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

See `docs/PRIVACY.md` and `docs/RUNTIME_MIGRATION.md` for the complete boundary
and dependency audit.
