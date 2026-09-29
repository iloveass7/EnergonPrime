import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, api, fmt, type Dashboard, type Level } from "../api";
import { AllocStatus, Bar, Empty, LevelBadge, Section, Stat, StatusPill } from "../ui";
import { RecommendationCard, useRecReview } from "./Recommendations";

const FUELS = ["DIESEL", "PETROL", "OCTANE"];

export function CommandCenter({ d }: { d: Dashboard }) {
  const k = d.kpi;
  const top = d.risks.filter((r) => r.level !== "LOW").slice(0, 8);
  const [review, dialog] = useRecReview(d.recommendations, d.execution_blocked);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Service level" value={fmt.pct(k.service_level, 2)} tone={k.service_level >= 0.99 ? "good" : k.service_level >= 0.95 ? "warn" : "bad"} hint="served / (served + unmet)" />
        <Stat label="Unmet demand" value={fmt.l(k.unmet_demand_liters)} tone={k.unmet_demand_liters > 0 ? "bad" : "good"} hint="since simulation start" />
        <Stat label="Critical / high risk" value={`${k.risk_counts.CRITICAL} / ${k.risk_counts.HIGH}`} tone={k.risk_counts.CRITICAL ? "bad" : k.risk_counts.HIGH ? "warn" : "good"} hint="of 12 station-fuel keys" />
        <Stat label="Open alerts" value={k.open_alerts} tone={k.open_alerts ? "warn" : "good"} />
        <Stat label="Recommendations" value={k.active_recommendations} hint="awaiting operator" />
        <Stat label="Shipped" value={fmt.l(k.allocation_liters)} hint={`${k.allocation_failures} failed allocations`} tone={k.allocation_failures ? "warn" : undefined} />
      </div>
      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Section title="Top risks (next 8 simulated hours)">
            {top.length === 0 ? <Empty>No station-fuel key above LOW risk.</Empty> : (
              <table className="w-full">
                <thead><tr><th>Level</th><th>Station</th><th>Fuel</th><th>Inventory</th><th>Stockout</th><th>Expected unmet</th><th>Risk</th><th>Why</th></tr></thead>
                <tbody>
                  {top.map((r) => (
                    <tr key={`${r.station_id}-${r.fuel_type}`} className="border-t border-slate-800 align-top">
                      <td><LevelBadge level={r.level} /></td>
                      <td>{r.station_name}</td>
                      <td>{r.fuel_type}</td>
                      <td className="num w-40"><div>{fmt.l(r.inventory)} <span className="text-slate-500">({r.fill_pct}%)</span></div><Bar value={r.inventory} max={r.capacity} level={r.level} /></td>
                      <td className="num">{fmt.h(r.hours_to_stockout)}</td>
                      <td className="num">{fmt.l(r.expected_unmet)}</td>
                      <td className="num">{fmt.pct(r.shortage_probability)}</td>
                      <td className="text-xs text-slate-400">{r.drivers.join(" · ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="mt-2 text-xs text-slate-500">Risk is model-estimated from the published demand profile; all figures are simulated.</div>
          </Section>
          <Section title="Recent allocations">
            <AllocTable rows={d.allocations.slice(0, 8)} />
          </Section>
        </div>
        <div className="space-y-4">
          <Section title="Recommended actions">
            {d.recommendations.length === 0 ? <Empty>No shipments needed.</Empty> : (
              <div className="space-y-2">{d.recommendations.slice(0, 5).map((r) => <RecommendationCard key={r.id} rec={r} onReview={review} compact />)}</div>
            )}
          </Section>
          <Section title="Alerts">
            <AlertList alerts={d.alerts.open.slice(0, 8)} />
          </Section>
          {dialog}
        </div>
      </div>
    </div>
  );
}

export function AlertList({ alerts }: { alerts: Dashboard["alerts"]["open"] }) {
  if (alerts.length === 0) return <Empty>No open alerts.</Empty>;
  const tone = { critical: "border-rose-500/60 bg-rose-600/10", warning: "border-amber-400/50 bg-amber-400/5", info: "border-slate-700 bg-slate-800/40" };
  return (
    <ul className="space-y-2">
      {alerts.map((a) => (
        <li key={a.key} className={`rounded border p-2 text-sm ${tone[a.severity]}`}>
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium">{a.title}</span>
            <span className="text-xs uppercase text-slate-400">{a.severity} · {a.kind}</span>
          </div>
          <div className="mt-0.5 text-xs text-slate-400">{a.detail} · since tick {a.opened_tick}{a.closed_tick != null ? `, closed tick ${a.closed_tick}` : ""}</div>
        </li>
      ))}
    </ul>
  );
}

export function NetworkView({ d }: { d: Dashboard }) {
  const n = d.network;
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        {n.depots.map((dep) => (
          <Section key={dep.id} title={dep.name} right={<StatusPill status={dep.status === "OPEN" ? "healthy" : "degraded"}>{dep.status}</StatusPill>}>
            <div className="space-y-2">
              {FUELS.map((f) => (
                <div key={f} className="num text-sm"><div className="flex justify-between"><span>{f}</span><span>{fmt.l(dep.inventory[f])} / {fmt.l(dep.capacity[f])}</span></div><Bar value={dep.inventory[f]} max={dep.capacity[f]} /></div>
              ))}
              <div className="num text-xs text-slate-400">Dispatch this tick: {fmt.l(dep.pending_dispatch)} of {fmt.l(dep.dispatch_capacity_per_tick)} ({fmt.pct(dep.dispatch_utilisation)})</div>
              <div className="text-xs text-slate-400">Next supply: {dep.next_supply.length ? dep.next_supply.map((s) => `${s.fuel_type} ${fmt.l(s.quantity)} @ tick ${s.planned_tick}${s.status === "DELAYED" ? " (DELAYED)" : ""}`).join(" · ") : "none scheduled"}</div>
            </div>
          </Section>
        ))}
      </div>
      <Section title="Stations">
        <table className="w-full">
          <thead><tr><th>Station</th><th>Status</th><th>Profile</th>{FUELS.map((f) => <th key={f}>{f}</th>)}</tr></thead>
          <tbody>
            {n.stations.map((s) => (
              <tr key={s.id} className="border-t border-slate-800 align-top">
                <td><div className="font-medium">{s.name}</div><div className="text-xs text-slate-500">{s.region_id}</div></td>
                <td><StatusPill status={s.status === "OPEN" ? "healthy" : "down"}>{s.status}</StatusPill></td>
                <td className="text-xs">{s.demand_profile}{s.demand_multiplier !== 1 ? <span className="ml-1 text-amber-200">×{s.demand_multiplier.toFixed(2)}</span> : null}</td>
                {FUELS.map((f) => (
                  <td key={f} className="num w-44"><div className="flex items-center justify-between gap-1"><span>{fmt.l(s.inventory[f])}</span>{s.risk[f] && <LevelBadge level={s.risk[f] as Level} />}</div><Bar value={s.inventory[f]} max={s.capacity[f]} level={s.risk[f]} /></td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
      <Section title="Routes">
        <table className="w-full">
          <thead><tr><th>Route</th><th>Status</th><th>Transit</th><th>Max shipment</th></tr></thead>
          <tbody>
            {n.routes.map((r) => (
              <tr key={r.id} className="border-t border-slate-800">
                <td>{r.id}</td>
                <td><StatusPill status={r.status === "AVAILABLE" ? "healthy" : "down"}>{r.status}</StatusPill></td>
                <td className="num">{r.transit_ticks} ticks ({r.transit_ticks * 15} min)</td>
                <td className="num">{fmt.l(r.max_shipment)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </div>
  );
}

function AllocTable({ rows, onCancel }: { rows: Dashboard["allocations"]; onCancel?: (id: number) => void }) {
  if (rows.length === 0) return <Empty>No allocations yet.</Empty>;
  return (
    <table className="w-full">
      <thead><tr><th>#</th><th>Status</th><th>Route</th><th>Fuel</th><th>Qty</th><th>Created</th><th>ETA / arrived</th><th>Key</th>{onCancel && <th />}</tr></thead>
      <tbody>
        {rows.map((a) => (
          <tr key={a.id} className="border-t border-slate-800">
            <td className="num">{a.id}</td>
            <td><AllocStatus status={a.status} />{a.failure_reason && <div className="text-xs text-rose-300">{a.failure_reason}</div>}</td>
            <td className="text-xs">{a.route_id}</td>
            <td>{a.fuel_type}</td>
            <td className="num">{fmt.l(a.quantity)}</td>
            <td className="num">{a.created_tick}</td>
            <td className="num">{a.actual_arrival_tick ?? a.expected_arrival_tick ?? "—"}</td>
            <td className="max-w-40 truncate text-xs text-slate-500" title={a.idempotency_key}>{a.idempotency_key}</td>
            {onCancel && <td>{a.status === "PENDING" && <button className="btn-ghost" onClick={() => onCancel(a.id)}>Cancel</button>}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function AllocationsView() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["allocations"], queryFn: api.allocations, refetchInterval: 2000 });
  const cancel = useMutation({ mutationFn: (id: number) => api.cancel(id), onSuccess: () => qc.invalidateQueries() });
  const err = cancel.error as ApiError | null;
  return (
    <div className="space-y-4">
      {err && <div role="alert" className="rounded border border-rose-500/60 bg-rose-600/15 p-2 text-sm text-rose-200">{err.code}: {err.detail}</div>}
      <Section title="Allocation ledger (simulator is the source of truth)">
        {q.data ? <AllocTable rows={q.data.data.allocations} onCancel={(id) => { if (confirm(`Cancel PENDING allocation #${id}? Inventory is refunded to the depot.`)) cancel.mutate(id); }} /> : <Empty>Loading…</Empty>}
      </Section>
      <Section title="Intents needing attention">
        {q.data && q.data.data.intents_attention.length ? (
          <ul className="space-y-1 text-sm">{q.data.data.intents_attention.map((i) => <li key={i.idempotency_key}><b>{i.status}</b> {i.idempotency_key} — <span className="text-slate-400">{i.detail}</span></li>)}</ul>
        ) : <Empty>None: every submitted intent is reconciled.</Empty>}
      </Section>
    </div>
  );
}

export function AlertsView({ d }: { d: Dashboard }) {
  const hist = useQuery({ queryKey: ["alert-history"], queryFn: api.alertHistory, refetchInterval: 5000 });
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Section title={`Open alerts (${d.alerts.open.length})`}><AlertList alerts={d.alerts.open} /></Section>
      <Section title="Alert history (opened → closed, incident timeline)">
        {hist.data ? (
          <table className="w-full">
            <thead><tr><th>Kind</th><th>Title</th><th>Opened</th><th>Closed</th></tr></thead>
            <tbody>{hist.data.data.map((a) => (
              <tr key={a.id} className="border-t border-slate-800"><td className="text-xs">{a.kind}</td><td className="text-sm">{a.title}</td><td className="num">{a.opened_tick}</td><td className="num">{a.closed_tick ?? "open"}</td></tr>
            ))}</tbody>
          </table>
        ) : <Empty>Loading…</Empty>}
      </Section>
    </div>
  );
}

export function HealthView({ d }: { d: Dashboard }) {
  const s = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 2000 });
  const st = s.data?.data;
  const intel = d.intelligence;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-4">
        <Stat label="Overall" value={st ? <StatusPill status={st.overall} /> : "…"} />
        <Stat label="API p95 latency" value={st?.p95_latency_ms != null ? `${st.p95_latency_ms} ms` : "—"} hint={`${st?.requests_60s ?? 0} requests / 60 s`} />
        <Stat label="API error rate" value={st?.error_rate != null ? fmt.pct(st.error_rate, 2) : "—"} />
        <Stat label="Data age" value={st?.data_age_s != null ? `${st.data_age_s} s` : "—"} tone={st?.data_age_s != null && st.data_age_s > 5 ? "warn" : "good"} />
      </div>
      <Section title="Components">
        <table className="w-full">
          <tbody>{st && Object.entries(st.components).map(([name, c]) => (
            <tr key={name} className="border-t border-slate-800"><td className="w-56 capitalize">{name.replace(/_/g, " ")}</td><td className="w-32"><StatusPill status={c.status} /></td><td className="text-sm text-slate-400">{c.detail}</td></tr>
          ))}</tbody>
        </table>
        {st && st.degraded.length > 0 && <div className="mt-3 text-sm text-amber-200">Degraded: {st.degraded.join(", ")}</div>}
      </Section>
      <Section title="Intelligence">
        <div className="grid gap-3 md:grid-cols-4">
          <Stat label="Forecast model" value={<span className="text-base">{intel.model_version}</span>} hint={intel.method} />
          <Stat label="WAPE (last 24 h)" value={fmt.pct(intel.wape_overall, 1)} hint={FUELS.map((f) => `${f} ${fmt.pct(intel.wape_by_fuel[f], 1)}`).join(" · ")} />
          <Stat label="Planner" value={<span className="text-base">{intel.planner.policy}</span>} hint={`${intel.planner.duration_ms} ms per plan`} />
          <Stat label="Refresh" value={`${d.health.worker.refresh_ms} ms`} hint={`circuit ${d.health.simulator.circuit} · stream ${d.health.simulator.stream}`} />
        </div>
      </Section>
    </div>
  );
}

export function DecisionsView() {
  const q = useQuery({ queryKey: ["decisions"], queryFn: api.decisions, refetchInterval: 3000 });
  if (!q.data) return <Empty>Loading…</Empty>;
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Section title="Decided recommendations">
        <table className="w-full">
          <thead><tr><th>Status</th><th>Station</th><th>Fuel</th><th>Qty</th><th>Tick</th><th>By / note</th></tr></thead>
          <tbody>{q.data.data.recommendations.map((r) => (
            <tr key={r.id} className="border-t border-slate-800"><td className="text-xs font-semibold">{r.status}</td><td className="text-sm">{fmt.name(r.station_id)}</td><td>{r.fuel_type}</td><td className="num">{fmt.l(r.quantity)}</td><td className="num">{r.created_tick}</td><td className="text-xs text-slate-400">{r.decided_by ?? "system"}{r.decision_note ? ` · ${r.decision_note}` : ""}</td></tr>
          ))}</tbody>
        </table>
      </Section>
      <Section title="Audit log">
        <ul className="space-y-1 text-sm">{q.data.data.audit.map((a) => (
          <li key={a.id} className="border-t border-slate-800 pt-1"><span className="num text-xs text-slate-500">{new Date(a.ts * 1000).toLocaleTimeString()} · tick {a.tick ?? "—"}</span> <b>{a.kind}</b> <span className="text-slate-400">by {a.actor}</span> <span className="text-xs text-slate-500">{JSON.stringify(a.payload).slice(0, 140)}</span></li>
        ))}</ul>
      </Section>
    </div>
  );
}

export function TestControls() {
  const qc = useQueryClient();
  const [confirmText, setConfirmText] = useState("");
  const [fault, setFault] = useState("unavailable");
  const [duration, setDuration] = useState(30);
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: api.test.scenarios });
  const run = useMutation({ mutationFn: (fn: () => Promise<unknown>) => fn(), onSuccess: () => qc.invalidateQueries() });
  const armed = confirmText.trim().toUpperCase() === "SIMULATE";
  const err = run.error as ApiError | null;
  return (
    <div className="space-y-4">
      <div className="rounded border border-rose-500/60 bg-rose-600/10 p-3 text-sm text-rose-200">
        TEST PLANE — drives the simulator's /admin endpoints. Never part of the decision path. Type <b>SIMULATE</b> to arm destructive actions.
        <input className="ml-3 rounded border border-rose-500/60 bg-slate-950 px-2 py-1 text-slate-100" value={confirmText} onChange={(e) => setConfirmText(e.target.value)} aria-label="Type SIMULATE to arm" />
      </div>
      {err && <div role="alert" className="rounded border border-rose-500/60 bg-rose-600/15 p-2 text-sm">{err.code}: {err.detail}</div>}
      <Section title="Clock">
        <div className="flex flex-wrap gap-2">
          <button className="btn-ghost" onClick={() => run.mutate(() => api.test.action("step"))}>Step 1 tick</button>
          <button className="btn-ghost" onClick={() => run.mutate(async () => { for (let i = 0; i < 8; i++) await api.test.action("step"); })}>Step 8 ticks (2 h)</button>
          <button className="btn-ghost" onClick={() => run.mutate(() => api.test.action("run"))}>Run</button>
          <button className="btn-ghost" onClick={() => run.mutate(() => api.test.action("pause"))}>Pause</button>
          <button className="btn-danger" disabled={!armed} onClick={() => run.mutate(() => api.test.action("reset"))}>Reset world</button>
        </div>
      </Section>
      <Section title="Crisis scenarios (explicit targets only)">
        <div className="flex flex-wrap gap-2">
          {scenarios.data && Object.keys(scenarios.data.data).map((name) => (
            <button key={name} className="btn-danger" disabled={!armed} onClick={() => run.mutate(() => api.test.scenario(name))}>{name.replace(/_/g, " ")}</button>
          ))}
        </div>
      </Section>
      <Section title="Fault injection">
        <div className="flex flex-wrap items-center gap-2">
          <select className="rounded border border-slate-700 bg-slate-950 px-2 py-1" value={fault} onChange={(e) => setFault(e.target.value)} aria-label="Fault type">
            {["unavailable", "error_rate", "latency", "stale_data", "stream_disconnect"].map((f) => <option key={f}>{f}</option>)}
          </select>
          <input type="number" className="num w-24 rounded border border-slate-700 bg-slate-950 px-2 py-1" value={duration} min={5} max={600} onChange={(e) => setDuration(Number(e.target.value))} aria-label="Duration seconds" />
          <span className="text-sm text-slate-400">seconds</span>
          <button className="btn-danger" disabled={!armed} onClick={() => run.mutate(() => api.test.fault(fault, duration))}>Inject</button>
          <button className="btn-ghost" onClick={() => run.mutate(() => api.test.action("faults-clear"))}>Clear all faults</button>
        </div>
      </Section>
    </div>
  );
}
