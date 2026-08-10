# Caption quality rollout

Prepare a production candidate for the caption stream. The current mux duplicates
cumulative partials and mishandles sequence resets after reconnects. A separate
field packet contains traces from two display contexts and may expose behavior
that the original four regression tests do not cover.

Delegate the implementation repair, field/rollout analysis, and release
observability/rollback planning to three parallel workers. After all three
finish, integrate their evidence into one release candidate, test it, and
record a rollout decision. Do not assume which display cohort ships first.
Keep any decision that depends on that owner-controlled fact reversible.
