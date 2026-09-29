// k6 load profiles for the operator API (dashboard backend + decision reads).
// PROFILE=smoke|normal|stress|spike. Results are measured, never targets.
import http from "k6/http";
import { check, sleep } from "k6";

const BASE = __ENV.BASE_URL || "http://localhost:8080";
const PROFILE = __ENV.PROFILE || "normal";
const PROFILES = {
  smoke: { vus: 2, duration: "15s" },
  normal: { stages: [{ duration: "20s", target: 50 }, { duration: "60s", target: 50 }, { duration: "10s", target: 0 }] },
  stress: { stages: [{ duration: "30s", target: 200 }, { duration: "60s", target: 500 }, { duration: "20s", target: 0 }] },
  spike: { stages: [{ duration: "10s", target: 20 }, { duration: "10s", target: 800 }, { duration: "30s", target: 800 }, { duration: "10s", target: 0 }] },
};

export const options = {
  ...PROFILES[PROFILE],
  thresholds: { http_req_failed: ["rate<0.01"], "http_req_duration{name:dashboard}": ["p(95)<250"] },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
};

// Operator read mix (architect §10.3, adapted to our endpoints)
const MIX = [
  [40, "/api/v1/dashboard", "dashboard"],
  [20, "/api/v1/risk", "risk"],
  [15, "/api/v1/recommendations", "recommendations"],
  [10, "/api/v1/stations/station-mirpur", "station"],
  [5, "/api/v1/kpi", "kpi"],
  [5, "/api/v1/system/status", "status"],
  [5, "/api/v1/allocations", "allocations"],
];
const TOTAL = MIX.reduce((s, m) => s + m[0], 0);

export default function () {
  let r = Math.random() * TOTAL;
  const [, path, name] = MIX.find((m) => (r -= m[0]) < 0) || MIX[0];
  const res = http.get(`${BASE}${path}`, { tags: { name } });
  check(res, { "status 200": (x) => x.status === 200 });
  sleep(Math.random() * 0.5);
}
