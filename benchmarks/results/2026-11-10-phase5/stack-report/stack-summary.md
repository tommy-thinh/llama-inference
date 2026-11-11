# Loopback HTTPS stack measurements

Three measured waves per cell; warm-ups excluded. Nearest-rank p95 is descriptive.

| Strategy | Clients | Success / attempts | Rejections | Errors | Successful requests/s | Completion tokens/s | Median attempt s | p95 attempt s | Median successful TTFT s | Median queue wait s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| fifo | 1 | 3/3 | 0 | 0 | 0.577 | 18.461 | 1.700 | 1.817 | 0.428 | 0.000 |
| fifo | 2 | 6/6 | 0 | 0 | 0.573 | 18.329 | 2.667 | 3.545 | 1.290 | 0.814 |
| fifo | 4 | 9/12 | 3 | 0 | 0.568 | 18.191 | 2.625 | 5.474 | 2.171 | 1.702 |
| reject | 1 | 3/3 | 0 | 0 | 0.581 | 18.593 | 1.718 | 1.728 | 0.438 | 0.000 |
| reject | 2 | 3/6 | 3 | 0 | 0.556 | 17.804 | 0.920 | 1.837 | 0.437 | 0.000 |
| reject | 4 | 3/12 | 9 | 0 | 0.537 | 17.190 | 0.120 | 2.067 | 0.473 | 0.000 |
