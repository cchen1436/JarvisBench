# Instructions for coding agents

This repository includes an operational runbook for agents setting up
JarvisBench on a user's computer.

Before running commands, read [`docs/AGENT_GUIDE.md`](docs/AGENT_GUIDE.md) in
full and follow it. The default task is local, no-model installation and
validation. Stop before any API call, model-backed episode, full benchmark
sweep, publication, or destructive operation unless the user explicitly asks
for it.

Always preserve these boundaries:

- Do not edit frozen files below `tasks/` to make a run pass.
- Do not expose credentials, requester context, evaluator-only material, or raw
  private traces.
- Keep Track 2 replay independent from Track 1 execution.
- In MAS, keep Parent and Jarvis logically separate and use the native Gateway.
- Use a fresh episode directory or volume for every model-backed run.
- Report runtime completion separately from official benchmark scoring.

