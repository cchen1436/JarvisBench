# Caption release baseline

The service can target `stage_display` and `headset_beta` independently through
cohort flags. Disabling a flag must restore strict visible-text immutability
without changing stored transcripts. A release is operationally ready only if
the following can be measured per cohort:

- p95 end-to-visible latency, with a 500 ms ceiling;
- duplicate-prefix and exact-replay suppression;
- active-token correction rate;
- prior-word rewrite rejection rate;
- frozen-token and caption-jump user reports.

The current deployment controller supports a ten-minute canary, a fifty-minute
observation window, and immediate flag rollback. Operations must not decide
which cohort ships first; that is a product-owner decision after field behavior
and release safety are known.
