# Postmortem 2025 Q2 DB migration (incident 2025-05-14)

## Summary
Online schema migration ran a non-transactional ALTER and locked the primary for 7 min.

## Root causes
1. Migration tool path did not use `--algorithm=INPLACE --lock=NONE`.
2. CI did not catch this because the test DB was empty.
3. No observability alert on long-held MDL locks.

## Gaps
- CI dataset is empty — real migrations should run against a realistic copy.
- Runbook did not cover manual `pt-osc` fallback.

## Follow-ups
- Switch migrations to gh-ost with safety checks.
- Nightly CI against restored prod snapshot.
