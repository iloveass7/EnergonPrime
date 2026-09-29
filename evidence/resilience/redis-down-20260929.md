# Redis outage (2026-09-29, compose stack, Neon Postgres)

Procedure: `docker compose stop redis`, wait 8 s, read `/api/v1/system/status` and `/api/v1/dashboard`,
then `docker compose start redis`, wait 12 s, read status again. All data simulated.

| Phase | overall | redis | worker | database | dashboard |
|---|---|---|---|---|---|
| before | healthy | healthy: hot state | healthy: heartbeat 0.6s ago | healthy: postgres | 200 |
| redis stopped | **degraded** | **down: serving l1-last-good** | down: no heartbeat | healthy: postgres | **200** (last good copy) |
| redis restarted | healthy | healthy: hot state | healthy: heartbeat 0.7s ago | healthy: postgres | 200 |

Result: health detects the outage within seconds, the API keeps serving the operator its last good
state instead of erroring, and the stack recovers on its own with no restart of api or worker.
