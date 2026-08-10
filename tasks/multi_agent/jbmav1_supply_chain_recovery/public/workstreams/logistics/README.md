# Recovery option model

Complete `optimize_recovery.py` without reading other workstreams. It must emit
three owner-facing options:

1. `no_expedite`: use only ground freight;
2. `partial_air_600`: air-ship exactly 600 units from `LOT-771A` after its
   certificate, at an incremental cost of 10,800;
3. `full_air_900_rejected`: preserve the tempting one-day quote in the audit
   trail but mark it invalid because it depends on held `LOT-771B` units.

The model may describe customer allocation consequences, but it cannot decide
which customer the owner values most. Keep units, dates, costs, and quality
assumptions machine-readable.
