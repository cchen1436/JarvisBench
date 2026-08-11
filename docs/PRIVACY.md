# Privacy and evaluation boundary

The public package is produced from an empty directory by an explicit allowlist
exporter. It includes public prompts/assets and participant schemas only.

The following are never participant-visible or included in the public image:

- evaluator rubrics, graders, references, partial solutions, and evaluator prompts;
- requester profiles, user memory, or private Luna context;
- raw worker/Luna/Jarvis traces, credentials, provider configuration, and historical runs;
- server paths, scheduler configuration, SQSH images, and mutable OpenClaw state.

`task.public.json` is a schema projection, not a redacted copy of the internal
manifest. Runtime mounts public task assets read-only. Evaluator and requester
services are separate processes with narrower interfaces.

For worker execution, JarvisBench does not write a custom provider or credential
reference into `openclaw.json`. A configured 0400 credential file is read only
by the runner and exposed under the selected vendor environment variable to the
OpenClaw subprocess, which then uses its native provider implementation. This
prevents accidental copies in generated model catalogs and config backups. The
value remains visible to code in the same process/uid boundary; deployments
whose threat model includes a malicious worker need a credential-injecting
loopback broker or a stronger process boundary.

The bundled Jarvis and Luna client fixes its base URL to OpenAI's official API,
uses the official Python SDK and Responses API with `store=False`, and reads
only `OPENAI_API_KEY` or a bounded 0400 `OPENAI_API_KEY_FILE`.

The repository scanner is defense in depth; it is not a substitute for building
the release from a fail-closed allowlist.
