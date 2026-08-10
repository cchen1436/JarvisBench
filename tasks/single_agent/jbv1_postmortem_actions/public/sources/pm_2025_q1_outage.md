# Postmortem 2025 Q1 Outage (incident 2025-02-08)

## Summary
A misconfigured AWS Auto Scaling group drained to 0 nodes during peak traffic.

## Timeline (UTC)
- 18:40 alert triggered
- 18:42 oncall acknowledged
- 18:55 scaled policy manually
- 19:05 recovered

## Root causes
1. Auto scaling min_size changed to 0 by a Terraform apply two weeks earlier.
2. No smoke test gates apply runs that zero out min_size.
3. Dashboard for min_size wasn't on the oncall wall.

## What worked
- Alert fired in <2 min.

## Gaps
- Config drift reviews did not cover terraform-managed capacity.
- No runbook entry for ASG zeroing.

## Follow-ups
- Require min_size >= 3 via OPA policy.
- Add a min_size panel to the oncall dashboard.
