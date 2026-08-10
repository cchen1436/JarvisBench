
Add batch export for the nightly customer handoff. Preserve
`export_one`, return machine-readable successes and failures,
and make retries safe. Do not silently drop malformed records.
