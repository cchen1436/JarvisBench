# Runtime migration and reproducibility

## Status and identity

The intended canonical public runtime is the OCI image reproducibly built from
`runtime/docker/Dockerfile`. The historical OpenClaw SQSH is not a release
artifact and is not a parent layer of that image. It is a private bootstrap
oracle used once to inventory capabilities and compare behavior during the
migration.

The 2026-08-10 migration audit recorded the following immutable checkpoints.
Hashes were recomputed from the downloaded files or recorded OCI descriptors;
private archive paths and the archives themselves remain outside this repository.

| Artifact | SHA-256 or OCI digest | Status |
|---|---|---|
| Original private SQSH | `96f81b679c93e1ee29679059dafb8364adea680dfe379315fb01717c28111c05` | read-only source identity |
| Sanitized bootstrap rootfs archive | `20dc3db3014b243a3ca2d36b6496ad363b77f61b1d7a35096407911b6fcb75c0` | private, 4,069,442,709-byte `linux/amd64` migration artifact |
| Bootstrap capability manifest | `19d3c8cfa1b5744b009dbbce39e3bcc726d6aaa727543d5d740a6a82f3dc791f` | private migration evidence |
| Formal-staging capability manifest | `50f07ec0c7b15ef95c26c9fe7c1b9216af580ace83d38b2268dfbfa4c30cb2e7` | private migration evidence |
| Audited staging OCI manifest list | `sha256:5378c1a797b008fd3557a11e4e9e197cb246b59a019656cc7841f499a4ebbfbf` | local `linux/amd64` staging build, not the final release |
| Post-remediation OCI index | `sha256:54ee0f3c2c0a60003c344e206e164c9f5521128d8771f06db2fda23b7d394249` | current local `jarvisbench:release-candidate`, not published |
| Post-remediation `linux/amd64` manifest | `sha256:848d15e6a34f8497d66668f4800ce79ef648357965277f9d07f4537ae746f236` | platform manifest for the current local candidate |
| Post-remediation image config | `sha256:1a534c9a7c1faa455859ffa99b0fe81975382b3e031104340e81fb8863e2e5f7` | image configuration for the current local candidate |
| Final public-release OCI manifest | not issued | rebuild and record only after source freeze and blocker closure |

The bootstrap export contains 239,988 members and was created in node-local
temporary storage with root OpenClaw state and credential homes excluded. Its
detached server checksum matches the downloaded archive checksum. The locally
imported bootstrap image remains private and has no reconstructible build
history; it is not publishable.

Two initial parallel `unsquashfs` attempts stalled and were cancelled. The
successful export used compute-node local temporary storage and a single-thread
`unsquashfs -no-xattrs` extraction, then created the compressed numeric-owner
rootfs archive; it completed in approximately 4 minutes 32 seconds. The
unprivileged migration could not restore one GStreamer
`security.capability` extended attribute. This is acceptable only because the
bootstrap is a private capability oracle: the formal image is rebuilt from the
Dockerfile and no parity claim depends on that file capability. Any future test
that needs the affected GStreamer privilege must add an explicit Dockerfile
capability step and validation rather than inheriting it from the bootstrap.

`scripts/compare_capabilities.py` found no differences across its required exact
capability keys: architecture, OS, libc, Python, Node, OpenClaw, Gateway help,
Git, SQLite, Pandoc, and wkhtmltopdf. Formal-only lock and task-contract fields
are intentionally populated only by the Dockerfile image. The audited staging
digest predates final source freeze, so it must not be relabeled as the final
release digest.

The current post-remediation candidate was built from the pinned public inputs
with:

```sh
JARVISBENCH_IMAGE=jarvisbench:release-candidate \
JARVISBENCH_PLATFORM=linux/amd64 \
  ./scripts/build-image.sh
```

Its complete offline container smoke passed, including all four topology/track
dry runs, six TypeScript supervisor contracts, task/checksum/privacy validation,
and the repeatable no-network SecretRef Gateway scan. It remains an unsigned,
local release candidate rather than a public release artifact.

## Migration sequence

1. Inspect the SQSH read-only on a server compute node and record its file
   checksum and capability manifest. Never run a large extraction on a login
   node.
2. Export a numeric-owner Linux rootfs archive without modifying the SQSH.
   Store the archive and detached checksum outside the public repository.
3. Verify the downloaded archive checksum on the Mac before import. Stream the
   archive directly into Docker as `linux/amd64`; do not expand it onto APFS.
4. Run the capability collector in the imported bootstrap image. The imported
   image has no reconstructible build history and is never published.
5. Build the canonical image from the pinned Dockerfile and lockfile with
   `./scripts/build-image.sh`.
6. Compare bootstrap and canonical capability manifests with
   `scripts/compare_capabilities.py`, then run the single authorized API canary.

Steps 1--6 were exercised. After the rejected pre-remediation attempt described
below, authorized representative single and MAS episodes passed the functional
post-remediation acceptance contract. A final signed/public digest remains
outstanding.

## Model-backed canary audit

One network-capable `linux/amd64` representative MAS run was attempted against
a local release-candidate image. The runtime command returned zero and reported
the episode completed, but the independent post-run privacy gate found the exact
provider credential once in temporary OpenClaw session state (in addition to the
expected ephemeral runtime configuration). The gate failed closed before sealed
grading or admission of the output, cleanup completed, and no model-backed
result is accepted as release evidence.

This is not a source-tree leak: the release-tree privacy scan still reports zero
findings, and no credential, private canary state, requester profile, or session
artifact is present in this repository or image build context. It is a runtime
credential-containment failure.

The worker adapter was subsequently changed to store only a file-backed
OpenClaw `SecretRef` in `openclaw.json`; OpenClaw resolves the mounted value into
its in-memory snapshot and writes `secretref-managed` markers to generated model
catalogs. A `--network none` preflight exercised configuration, the native
Gateway, and the dynamic supervisor. It reported a healthy Gateway, a ready
supervisor, zero agent messages, and zero exact dummy-secret matches across all
54 files in the episode tree. Unit tests separately cover file and environment
SecretRef serialization and prove that the resolved values do not enter
`openclaw.json`.

This remediation was subsequently exercised by one representative single-agent
and one dynamic-MAS network-capable episode. Both completed the runtime,
artifact, exact-receipt, exact-secret scan, and sealed-grader handoff; neither is
a full-suite score-parity claim. SecretRef prevents accidental
persistence but does not prevent arbitrary worker code running under the same
container uid from deliberately reading the mounted file. Treat a credential
broker or a separate uid/process boundary as a distinct privacy-hardening
decision before public deployment.

## macOS archive and xattr caveat

Expanding a Linux rootfs on APFS can change numeric ownership, modes, symlinks,
extended attributes, file capabilities, device nodes, and atomic-rename
behavior. The bootstrap archive must therefore be checksum-verified and piped
directly to `docker import --platform linux/amd64`; mutable benchmark state
belongs in a Docker named volume, not a macOS bind mount.

Downloaded macOS CLI binaries may also receive `com.apple.quarantine` when a
browser is used. Do not recursively strip xattrs as a generic workaround.
Verify the official digest and code signature first, inspect the exact xattr,
and remove only a confirmed quarantine attribute from the exact verified
binary if Gatekeeper blocks it. The release image itself contains Linux
binaries and is unaffected by Mach-O signing.

## Pinned runtime and compatibility warning

The Dockerfile pins its frontend, Ubuntu base, Node 22 image, Ubuntu snapshot,
OpenClaw package version, and npm dependency graph. The minimal Ubuntu base has
no CA bundle. For the first signed InRelease fetch only, the build temporarily
disables HTTPS peer/host verification, while Ubuntu archive signatures remain
mandatory. It installs `ca-certificates`, deletes the exception, and performs
all subsequent downloads with HTTPS verification. This bootstrap is an
explicit residual supply-chain risk; replacing it requires a separately pinned
and verified CA source, not an unreviewed convenience image. `Check-Valid-Until`
is disabled only because an immutable historical snapshot naturally expires.

Node 22.22.1 and OpenClaw 2026.3.11 are a pinned, representative-canary-tested
pair for this staging image, not a promise that arbitrary Node/OpenClaw upgrades
are compatible. Native
Gateway children depend on the exact delegation -> child completion -> same
Parent integration protocol. A new OpenClaw version can change session files,
Gateway flags, plugin loading, model configuration, or completion notices even
when `openclaw --version` succeeds. Upgrade only by changing the lock, rebuilding
the image, running no-model contract tests, and repeating the one representative
Gateway canary.

The current locked graph also emits an engine warning: `osc-progress@0.3.2`
declares Node 24 or newer while this image intentionally uses Node 22. The
package installs because npm treats the declaration as a warning, and the
no-model version/Gateway-help checks and representative Gateway canaries pass,
but that is not proof that every optional OpenClaw path is compatible. This
mismatch must remain visible until the pinned pair is replaced deliberately.

The participant image embeds the expected task checksum/provenance contracts,
but not the public task bundle. Validation and episodes mount `tasks/` read-only
at `/opt/jarvisbench/tasks`; a full runtime validation fails if that mount is
missing or differs by one byte. Mutable OpenClaw paths default to
`OPENCLAW_HOME=/var/lib/jarvisbench`,
`OPENCLAW_STATE_DIR=/var/lib/jarvisbench/openclaw`, and
`OPENCLAW_CONFIG_PATH=/var/lib/jarvisbench/openclaw/openclaw.json`. Real MAS
episodes override them with episode-local Linux-volume paths.

## Dependency and supply-chain risk

Digest pins and lockfiles prevent silent version drift; they do not prove that
dependencies are vulnerability-free. OpenClaw brings a large transitive npm
graph and the image contains document converters that process untrusted task
inputs. The build uses `npm ci --omit=dev --ignore-scripts`, runs as UID 10001,
and the no-model smoke drops Linux capabilities, disables networking, enables
`no-new-privileges`, and uses a read-only root filesystem. Production runners
should preserve those restrictions wherever Gateway networking allows them.

Before a public release, archive machine-readable npm and OS vulnerability
scan reports, triage reachable high/critical findings, and record accepted
risks. Never "fix" an audit by unpinning dependencies or changing frozen task
content. Evaluator-only and requester-private material must remain outside both
the build context and the public image.

The 2026-08-10 build reported 10 npm advisories (7 high, 3 critical). It also
identified locked `@whiskeysockets/baileys@7.0.0-rc.9` as affected by the public
message-spoofing advisory `GHSA-qvv5-jq5g-4cgg`. JarvisBench does not need the
WhatsApp channel for benchmark execution, but the vulnerable package is still
present in the generic OpenClaw distribution. This is a public-release blocker
until reachability is documented and the dependency is removed, isolated, or
updated through a separately validated OpenClaw lock revision.
