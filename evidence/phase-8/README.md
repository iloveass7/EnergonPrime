# Load test results (measured, 2026-09-29)

Workload: `load/k6_api.js`, operator read mix (40% dashboard, 20% risk, 15% recommendations,
10% station detail, 5% kpi, 5% system status, 5% allocations), 0–0.5 s think time.
Target: the containerised API (`docker compose`, API_WORKERS=2), images built from the working tree
that was committed as `7f765d7` immediately after the run.
Machine: Apple M4, 16 GB RAM; Docker VM 4 CPUs / 5.8 GB. All data simulated.

| Profile | VUs | Requests | Throughput | avg | p50 | p95 | p99 | max | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| normal | 50 | 14,945 | 166 req/s | 1.93 ms | 1.31 ms | 4.12 ms | 6.75 ms | 201 ms | 0.00% |
| stress | 200 → 500 | 114,713 | 1,040 req/s | 2.76 ms | 1.25 ms | 4.96 ms | 27.4 ms | 365 ms | 0.00% |

Resources at peak stress (`stress-docker-stats.txt`): api 72% CPU / 197 MiB, worker 1% / 67 MiB,
simulator 3% / 78 MiB, redis 0.5% / 5 MiB.

Reading the numbers:
- Reads are served from Redis + a 500 ms in-process cache, never from the simulator, so user load
  does not reach the simulator (3% CPU under 1,040 req/s) and cannot trigger its pool exhaustion.
- The API is the first limit (72% CPU across 2 workers at 500 VUs); scale with API_WORKERS or replicas.
- Throughput is bounded by k6 think time, not by a server limit; the spike profile (800 VUs) was not run yet.

Raw k6 summaries: `k6-normal.json`, `k6-stress.json`.
