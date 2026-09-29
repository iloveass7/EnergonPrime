"""Prometheus metrics (architect §9.6 names). All values describe the simulated system."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# simulator integration
SIM_REQUEST_SECONDS = Histogram(
    "sim_request_duration_seconds", "Simulator call latency", ["endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)  # fmt: skip
SIM_ERRORS = Counter("sim_errors_total", "Simulator call failures", ["endpoint", "kind"])
SIM_CIRCUIT_STATE = Gauge("sim_circuit_state", "0 closed, 1 half-open, 2 open")
SIM_STALE_RESPONSES = Counter("sim_stale_responses_total", "Responses carrying X-Simulator-Stale")
SIM_STREAM_CONNECTED = Gauge("sim_stream_connected", "1 when the SSE stream is connected")
SSE_RECONNECTS = Counter("sse_reconnects_total", "SSE reconnect attempts")
SIM_TICK = Gauge("sim_tick", "Latest simulator tick seen")

# worker
SYNC_RUNS = Counter("sync_runs_total", "Snapshot refreshes", ["result"])
SYNC_SECONDS = Histogram("sync_duration_seconds", "Full refresh + score + plan duration")
STATE_AGE = Gauge("state_age_seconds", "Age of the published snapshot")
STATE_VERSION = Gauge("state_version", "Published state version")
EPOCH = Gauge("sim_epoch", "Simulator epoch (increments on reset)")

# intelligence
FORECAST_WAPE = Gauge("forecast_wape", "Rolling one-step WAPE of the demand forecast", ["fuel"])
FORECAST_FALLBACK = Counter("forecast_fallback_total", "Forecast fallbacks used", ["method"])
RISK_KEYS = Gauge("risk_keys", "Station-fuel keys per risk level", ["level"])
ALERTS_OPENED = Counter("alerts_opened_total", "Alerts opened", ["kind"])
RECOMMENDATIONS = Counter("recommendations_total", "Recommendations proposed", ["policy"])
PLANNER_SECONDS = Histogram("planner_duration_seconds", "Planner duration", ["policy"])
VALIDATOR_REJECTIONS = Counter("replay_rejections_total", "Validator rejections", ["reason"])
FALLBACK_ACTIVE = Gauge("fallback_active", "1 while a fallback is active", ["component"])

# decisions
INTENTS = Counter("intent_state_total", "Allocation intents by resulting state", ["state"])
OPERATOR_ACTIONS = Counter("operator_actions_total", "Operator actions", ["action"])
TEST_PLANE_ACTIONS = Counter("test_plane_actions_total", "Test-plane actions", ["action"])

# api
HTTP_REQUESTS = Counter("http_requests_total", "API requests", ["route", "method", "status"])
HTTP_SECONDS = Histogram(
    "http_request_duration_seconds", "API latency", ["route"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
)  # fmt: skip
HTTP_INFLIGHT = Gauge("http_inflight", "In-flight API requests")
