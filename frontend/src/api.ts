// Thin client for /api/v1 (the browser never talks to the simulator).
export type Level = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export interface Meta {
  tick: number;
  sim_time: string;
  sim_status: string;
  epoch: number;
  state_version: number;
  fetched_at: number;
  age_s: number;
  stale: boolean;
  degraded: string[];
  execution_blocked: string | null;
  source: string;
}

export interface Risk {
  station_id: string;
  station_name: string;
  fuel_type: string;
  level: Level;
  inventory: number;
  capacity: number;
  fill_pct: number;
  expected_demand: number;
  inbound: number;
  expected_unmet: number;
  shortage_probability: number;
  hours_to_stockout: number | null;
  cover_hours: number;
  drivers: string[];
}

export interface Recommendation {
  id: string;
  status: string;
  station_id: string;
  station_name: string;
  fuel_type: string;
  depot_id: string;
  depot_name: string;
  route_id: string;
  quantity: number;
  transit_ticks: number;
  eta_tick: number;
  created_tick: number;
  valid_until_tick: number;
  level: Level;
  priority_unmet_l: number;
  impact: {
    unmet_before_l: number;
    unmet_after_l: number;
    risk_before: number;
    risk_after: number;
    stockout_before_h: number | null;
    stockout_after_h: number | null;
    inventory_before: number[];
    inventory_after: number[];
    capacity: number;
  };
  comparison: { plan: string; projected_unmet_l: number; risk: number | null; binding_constraint: string | null; why: string }[];
  reasons: string[];
  confidence: string;
  needs_review: boolean;
  policy_used: string;
  model_version: string;
  intents?: Intent[];
}

export interface Intent {
  idempotency_key: string;
  status: string;
  sim_allocation_id: number | null;
  upstream_code: string | null;
  detail: string | null;
}

export interface Alert {
  key: string;
  kind: string;
  severity: "info" | "warning" | "critical";
  title: string;
  detail: string;
  opened_tick: number;
  closed_tick?: number;
}

export interface Allocation {
  id: number;
  idempotency_key: string;
  source_depot_id: string;
  destination_station_id: string;
  route_id: string;
  fuel_type: string;
  quantity: number;
  created_tick: number;
  departure_tick: number | null;
  expected_arrival_tick: number | null;
  actual_arrival_tick: number | null;
  status: string;
  failure_reason: string | null;
  ours?: boolean;
  rec_id?: string | null;
}

export type FuelMap = Record<string, number>;

export interface Dashboard {
  kpi: {
    served_demand_liters: number;
    unmet_demand_liters: number;
    service_level: number;
    allocation_liters: number;
    allocation_failures: number;
    risk_counts: Record<Level, number>;
    open_alerts: number;
    active_recommendations: number;
  };
  network: {
    depots: {
      id: string; name: string; status: string; dispatch_capacity_per_tick: number; capacity: FuelMap; inventory: FuelMap;
      pending_dispatch: number; dispatch_utilisation: number;
      next_supply: { id: string; fuel_type: string; quantity: number; planned_tick: number; status: string }[];
    }[];
    stations: {
      id: string; name: string; region_id: string; status: string; demand_profile: string; demand_multiplier: number;
      capacity: FuelMap; inventory: FuelMap; risk: Record<string, Level>;
    }[];
    routes: { id: string; source_depot_id: string; destination_station_id: string; transit_ticks: number; max_shipment: number; status: string }[];
  };
  risks: Risk[];
  recommendations: Recommendation[];
  alerts: { open: Alert[]; recent_closed: Alert[] };
  allocations: Allocation[];
  events: { id: number; type: string; start_tick: number; end_tick: number; status: string; parameters: Record<string, unknown> }[];
  health: {
    simulator: { circuit: string; stream: string; last_success_age_s: number | null; last_error: string | null; stale: boolean };
    worker: { refresh_ms: number; plan_ms: number; epoch: number };
    database: { ok: boolean; kind: string | null };
  };
  intelligence: { model_version: string; method: string; wape_overall: number | null; wape_by_fuel: Record<string, number | null>; window_ticks: number; history_rows: number; planner: { policy: string; duration_ms: number } };
  execution_blocked: string | null;
}

export interface Envelope<T> {
  data: T;
  meta: Meta;
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, public detail: string, public upstream?: string | null) {
    super(`${code}: ${detail}`);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(res.status, body.code ?? `HTTP_${res.status}`, body.detail ?? res.statusText, body.upstream_code);
  }
  return body as T;
}

export const api = {
  dashboard: () => request<Envelope<Dashboard>>("/api/v1/dashboard"),
  status: () => request<{ data: SystemStatus }>("/api/v1/system/status"),
  allocations: () => request<Envelope<{ allocations: Allocation[]; intents_attention: { idempotency_key: string; status: string; detail: string }[] }>>("/api/v1/allocations"),
  decisions: () => request<{ data: { audit: AuditRow[]; recommendations: DecidedRec[] } }>("/api/v1/decisions?limit=100"),
  alertHistory: () => request<{ data: (Alert & { id: number })[] }>("/api/v1/alerts/history?limit=100"),
  recommendation: (id: string) => request<Envelope<Recommendation>>(`/api/v1/recommendations/${id}`),
  approve: (id: string, quantity?: number, note?: string) =>
    request<{ data: { recommendation: Recommendation; intents: Intent[] } }>(`/api/v1/recommendations/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ quantity, note }),
    }),
  reject: (id: string, reason: string) =>
    request(`/api/v1/recommendations/${id}/reject`, { method: "POST", body: JSON.stringify({ reason }) }),
  cancel: (id: number) => request(`/api/v1/allocations/${id}/cancel`, { method: "POST" }),
  capabilities: () => request<{ data: { test_plane: boolean; auth_mode: string; policy: string } }>("/api/v1/capabilities"),
  test: {
    action: (a: string) => request(`/api/v1/test/simulator/${a}`, { method: "POST" }),
    scenarios: () => request<{ data: Record<string, unknown> }>("/api/v1/test/simulator/scenarios"),
    scenario: (name: string) => request(`/api/v1/test/simulator/scenarios/${name}/run`, { method: "POST" }),
    fault: (type: string, duration_seconds: number, parameters: Record<string, unknown> = {}) =>
      request("/api/v1/test/simulator/faults/inject", { method: "POST", body: JSON.stringify({ type, duration_seconds, parameters }) }),
  },
};

export interface SystemStatus {
  overall: string;
  components: Record<string, { status: string; detail: string }>;
  p95_latency_ms: number | null;
  error_rate: number | null;
  requests_60s: number;
  data_age_s: number | null;
  degraded: string[];
}

export interface AuditRow {
  id: number;
  ts: number;
  kind: string;
  actor: string;
  epoch: number | null;
  tick: number | null;
  payload: Record<string, unknown>;
}

export interface DecidedRec {
  id: string;
  status: string;
  station_id: string;
  fuel_type: string;
  created_tick: number;
  quantity: number;
  route_id: string;
  decided_by: string | null;
  decision_note: string | null;
}

export const fmt = {
  l: (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v).toLocaleString()} L`),
  pct: (v: number | null | undefined, d = 0) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`),
  h: (v: number | null | undefined) => (v == null ? "none in horizon" : `${v.toFixed(1)} h`),
  name: (id: string) => id.replace(/^(station|depot|route|region)-/, "").replace(/-/g, " → "),
};
