# Inference baseline summary

- Model: `meta-llama/Llama-3.2-1B-Instruct`
- GGUF: `Llama-3.2-1B-Instruct-Q4_K_M.gguf` (807,694,464 bytes)
- Quantization: `Q4_K_M`
- Model SHA-256: `6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83`
- llama.cpp commit: `620d2225cc436ab0b001e3200e590e3b286ef9ab`
- CPU measurement: server process user+system time during each request, as percent of one logical CPU (100% equals one fully occupied CPU).

| Profile | Prompt | Runs | TTFT median (s) | Total median (s) | Decode mean (tok/s) | Server CPU mean (% of one logical CPU) | Peak RSS (MiB) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | short | 3 | 0.491 | 3.675 | 19.76 | 399.7 | 1366.7 |
| baseline | long | 3 | 2.412 | 5.928 | 17.83 | 397.4 | 1366.7 |
| threads-2 | short | 3 | 0.947 | 6.477 | 11.44 | 199.8 | 1364.7 |
| threads-2 | long | 3 | 4.572 | 10.751 | 10.23 | 199.1 | 1364.7 |
| threads-6 | short | 3 | 0.421 | 3.323 | 17.48 | 558.5 | 1368.6 |
| threads-6 | long | 3 | 1.775 | 5.323 | 16.36 | 560.0 | 1368.6 |
| context-1024 | short | 3 | 0.513 | 3.719 | 19.83 | 399.3 | 1334.7 |
| context-1024 | long | 3 | 2.396 | 5.873 | 18.10 | 397.3 | 1334.7 |
| context-4096 | short | 3 | 0.502 | 3.590 | 20.15 | 399.3 | 1430.7 |
| context-4096 | long | 3 | 2.349 | 5.818 | 18.19 | 397.6 | 1430.7 |
| output-32 | short | 3 | 0.526 | 2.086 | 19.87 | 398.2 | 1366.6 |
| output-32 | long | 3 | 2.455 | 4.173 | 17.52 | 394.7 | 1366.6 |
| output-128 | short | 3 | 0.533 | 7.208 | 18.45 | 395.0 | 1366.7 |
| output-128 | long | 3 | 2.544 | 9.769 | 17.60 | 397.9 | 1366.7 |
