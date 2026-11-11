# Independent Phase 5 review — 2026-11-10

What this change does: It measures CPU inference settings, quantization, cache
reuse, and concurrency using the pinned Llama model. It also measures the local
HTTPS application stack with an isolated test database and records raw evidence.

Looks good. Ship the local Phase 5 milestone.

The review found and the implementation team corrected three stack reporting
problems: report-writing time was included in throughput, unrelated HTTP 503
errors could be counted as queue rejections, and unexpected request failures did
not change the completion status. The first native sweep also reused 30 shared
header tokens during the long-prompt cache warm-up. Its original record remains
intact; a separate corrected comparison records zero cached tokens during both
warm-ups and measured cache hits of 50 and 309 tokens. Startup traces separately
record actual KV allocation because the timed sweep's logs did not expose it.

I checked the experiment configuration, request and wave collection, measured
denominators, queue admission timing, test-database isolation, process cleanup,
report calculations, and the saved native, cache, allocation, and HTTPS results.
For the intended one-worker application, the 42 measured stack attempts include
27 successes and 15 expected rejections, with no other request errors. The test
schema was removed, and owned process exits were recorded. The documentation
accurately distinguishes approximate process CPU attribution from throughput,
and the latency residual from precise network time. I checked the reported
control drift, six-thread outlier, and the small quality scores against raw data.

Independent verification reported 389 ordinary tests passed with 51 intentional
skips, and 440 tests passed with database and real inference enabled. The sample
sizes and fixed run order limit performance conclusions; the findings document
states these limits. Public deployment and installed-service resource limits
were not tested or authorized.
