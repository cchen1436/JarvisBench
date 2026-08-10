# Caption rollout policy

The next release may target either the public stage display or the accessibility
headset beta. Product has not recorded which cohort goes first.

Shared requirements:

- suppress exact replay and cumulative-prefix duplication;
- recover from provider sequence resets;
- never rewrite words before the last completed word boundary;
- do not add more than 500 ms of stabilization latency;
- keep the release behind a reversible cohort flag;
- record the behavior of non-prefix provider corrections explicitly.

Public stage operators prioritize visual immutability. Headset-beta participants
have reported that freezing an incorrect active token can reduce comprehension.
The field traces establish provider behavior, but they do not authorize a first
cohort or decide the acceptable correction boundary.

