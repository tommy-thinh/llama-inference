# Native optimization measurements

Warm-ups excluded. p95 uses nearest rank; small samples are descriptive only.

| Profile | Prompt | Clients | Success/attempt | TTFT s | Median latency s | p95 s | Decode tok/s | Throughput tok/s | CPU % | Peak RSS MiB | Cached tokens |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cache-control | short | 1 | 3/3 | 0.398 | 1.771 | 1.787 | 22.682 | 18.140 | 399.270 | 1355.449 | 0.000 |
| cache-control | long | 1 | 3/3 | 2.273 | 3.752 | 3.832 | 20.884 | 8.477 | 395.861 | 1366.480 | 0.000 |
| prefix-confirmed | short | 1 | 3/3 | 0.053 | 1.437 | 1.466 | 22.328 | 22.215 | 398.951 | 1355.449 | 50.000 |
| prefix-confirmed | long | 1 | 3/3 | 0.052 | 1.588 | 1.685 | 20.210 | 20.114 | 399.359 | 1366.480 | 309.000 |
