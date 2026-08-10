# Postmortem 2025 Q3 Config drift (incident 2025-08-22)

## Summary
Environment variables diverged between staging and prod during a hotfix release. The prod pod was missing FEATURE_FLAG_URL, which collapsed to a default that disabled checkout.

## Root causes
1. Hotfix branch did not include the env var addition that landed on main a week earlier.
2. No release gate that diffed env between staging and prod.
3. Oncall dashboard did not surface checkout conversion rate for the first 14 min.

## Gaps
- Release process lacks an env-diff step.
- Conversion metric missing from primary dashboard.

## Follow-ups
- Add env-diff job to release pipeline.
- Add checkout rate tile to oncall dashboard.
