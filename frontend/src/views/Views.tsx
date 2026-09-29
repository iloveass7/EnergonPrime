import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, api, fmt, type Dashboard, type Level } from "../api";
import { Bar, Empty, Glyph, LevelBadge, Notice, Section, SeverityGlyph, Skeleton, StateLabel, Stat, StatStrip, StatusPill, Table } from "../ui";
import { RecommendationRow, useRecReview } from "./Recommendations";

const FUELS = ["DIESEL", "PETROL", "OCTANE"];

function LoadError({ error, what }: { error: unknown; what: string }) {
  return <Notice tone="bad" title={`Could not load ${what}`}>{(error as Error)?.message ?? "unknown error"}. Retrying…</Notice>;
}

export function CommandCenter({ d }: { d: Dashboard }) {
  const k = d.kpi;
  const top = d.risks.filter((r) => r.level !== "LOW").slice(0, 8);
  const recs = d.recommendations.slice(0, 5);
  const alerts = d.alerts.open.slice(0, 8);
  const critical = d.alerts.open.some((a) => a.severity === "critical");
  const [review, dialog] = useRecReview(d.recommendations, d.execution_blocked);
  return (
    <div className="space-y-4">
      <StatStrip className="grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Service level" value={fmt.pct(k.service_level, 2)} tone={k.service_level >= 0.99 ? undefined : k.service_level >= 0.95 ? "warn" : "bad"} hint="served / (served + unmet)" />
        <Stat label="Unmet demand" value={fmt.l(k.unmet_demand_liters)} tone={k.unmet_demand_liters > 0 ? "bad" : undefined} hint="since simulation start" />
        <Stat label="Critical / high risk" value={`${k.risk_counts.CRITICAL} / ${k.risk_counts.HIGH}`} tone={k.risk_counts.CRITICAL ? "bad" : k.risk_counts.HIGH ? "warn" : undefined} hint={`of ${d.risks.length} station-fuel keys`} />
        <Stat label="Open alerts" value={k.open_alerts} tone={critical ? "bad" : k.open_alerts ? "warn" : undefined} hint={critical ? "includes critical" : undefined} />
        <Stat label="Recommendations" value={k.active_recommendations} hint="awaiting operator" />
        <Stat label="Shipped" value={fmt.l(k.allocation_liters)} hint={`${k.allocation_failures} failed allocations`} tone={k.allocation_failures ? "warn" : undefined} />
      </StatStrip>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="min-w-0 space-y-4">
          <Section title="Top risks" sub="Next 8 simulated hours. Unmet and risk are model-estimated from the published demand profile.">
            {top.length === 0 ? <Empty>Every station-fuel key is at LOW risk over the next 8 hours.</Empty> : (
              <Table>
                <thead><tr><th>Level</th><th>Station · drivers</th><th>Fuel</th><th>Inventory</th><th className="text-right">Stockout</th><th className="text-right">Unmet</th><th className="text-right">Risk</th></tr></thead>
                <tbody>
                  {top.map((r) => (
                    <tr key={`${r.station_id}-${r.fuel_type}`} className="align-top">
                      <td><LevelBadge level={r.level} /></td>
                      <td className="min-w-44"><div className="whitespace-nowrap font-medium">{r.station_name}</div><div className="mt-0.5 text-xs text-slate-400">{r.drivers.join(" · ")}</div></td>
                      <td>{r.fuel_type}</td>
                      <td className="num w-36 min-w-32"><div className="flex justify-between gap-2"><span>{fmt.l(r.inventory)}</span><span className="text-slate-500">{r.fill_pct}%</span></div><Bar value={r.inventory} max={r.capacity} level={r.level} /></td>
                      <td className="num whitespace-nowrap text-right">{fmt.h(r.hours_to_stockout)}</td>
                      <td className="num whitespace-nowrap text-right">{fmt.l(r.expected_unmet)}</td>
                      <td className="num text-right">{fmt.pct(r.shortage_probability)}</td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Section>
          <Section title="Recent allocations">
            <AllocTable rows={d.allocations.slice(0, 8)} />
          </Section>
        </div>
        <div className="min-w-0 space-y-4">
          <Section title="Recommended actions" sub={d.recommendations.length > recs.length ? `Top ${recs.length} of ${d.recommendations.length} by priority` : "By priority"}>
            {recs.length === 0 ? <Empty>No shipments needed. The planner re-checks every tick.</Empty> : (
              <div className="-mx-2 divide-y divide-slate-800/80">{recs.map((r) => <RecommendationRow key={r.id} rec={r} onReview={review} />)}</div>
            )}
          </Section>
          <Section title="Alerts" sub={d.alerts.open.length > alerts.length ? `${alerts.length} of ${d.alerts.open.length} open` : undefined}>
            <AlertList alerts={alerts} />
          </Section>
          {dialog}
        </div>
      </div>
    </div>
  );
}

export function AlertList({ alerts }: { alerts: Dashboard["alerts"]["open"] }) {
  if (alerts.length === 0) return <Empty>No open alerts. New ones appear here as soon as the detector raises them.</Empty>;
  return (
    <ul className="divide-y divide-slate-800">
      {alerts.map((a) => (
        <li key={a.key} className="flex gap-2.5 py-2.5 first:pt-0 last:pb-0">
          <SeverityGlyph severity={a.severity} className="mt-1" />
          <div className="min-w-0 flex-1">
            <div className={`text-pretty text-sm font-medium ${a.severity === "critical" ? "text-rose-100" : ""}`}><span className="sr-only">{a.severity}: </span>{a.title}</div>
            <p className="mt-0.5 text-xs text-slate-400">
              <span className="text-slate-300">{fmt.label(a.kind)}</span> · {a.detail} · since tick <span className="num">{a.opened_tick}</span>{a.closed_tick != null ? <>, closed tick <span className="num">{a.closed_tick}</span></> : null}
            </p>
          </div>
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
          <Section key={dep.id} title={dep.name}
            sub={<>Dispatch this tick <span className="num">{fmt.l(dep.pending_dispatch)}</span> of <span className="num">{fmt.l(dep.dispatch_capacity_per_tick)}</span> ({fmt.pct(dep.dispatch_utilisation)})</>}
            right={<StatusPill status={dep.status === "OPEN" ? "healthy" : "degraded"}>{dep.status}</StatusPill>}>
            <div className="space-y-3">
              {FUELS.map((f) => (
                <div key={f} className="text-sm">
                  <div className="flex justify-between gap-2"><span className="text-slate-300">{f}</span><span className="num">{fmt.l(dep.inventory[f])} <span className="text-slate-500">/ {fmt.l(dep.capacity[f])}</span></span></div>
                  <Bar value={dep.inventory[f]} max={dep.capacity[f]} />
                </div>
              ))}
            </div>
            <div className="mt-4 border-t border-slate-800 pt-3">
              <h3 className="mb-1.5 text-xs font-medium text-slate-400">Next supply</h3>
              {dep.next_supply.length === 0 ? <p className="text-sm text-slate-400">None scheduled</p> : (
                <ul className="space-y-1 text-sm">
                  {dep.next_supply.map((s) => (
                    <li key={s.id} className="flex justify-between gap-3">
                      <span>{s.fuel_type} <span className="num">{fmt.l(s.quantity)}</span></span>
                      <span className={`num inline-flex items-center gap-1.5 text-xs ${s.status === "DELAYED" ? "text-amber-200" : "text-slate-400"}`}>
                        {s.status === "DELAYED" && <><Glyph shape="triangle" className="size-2.5 text-amber-300" />Delayed ·</>} tick {s.planned_tick}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </Section>
        ))}
      </div>
      <Section title="Stations" sub="Bars show fill against tank capacity; color appears only at MEDIUM risk and above">
        <Table>
          <thead><tr><th>Station</th><th>Status</th><th>Profile</th>{FUELS.map((f) => <th key={f}>{f}</th>)}</tr></thead>
          <tbody>
            {n.stations.map((s) => (
              <tr key={s.id} className="align-top">
                <td className="whitespace-nowrap"><div className="font-medium">{s.name}</div><div className="text-xs text-slate-500">{s.region_id}</div></td>
                <td><StatusPill status={s.status === "OPEN" ? "healthy" : "down"}>{s.status}</StatusPill></td>
                <td className="whitespace-nowrap text-xs text-slate-300">{s.demand_profile}{s.demand_multiplier !== 1 ? <span className="num ml-1 text-amber-200">×{s.demand_multiplier.toFixed(2)}</span> : null}</td>
                {FUELS.map((f) => (
                  <td key={f} className="num w-44 min-w-40">
                    <div className="flex items-center justify-between gap-2"><span>{fmt.l(s.inventory[f])}</span>{s.risk[f] && s.risk[f] !== "LOW" && <LevelBadge level={s.risk[f] as Level} />}</div>
                    <Bar value={s.inventory[f]} max={s.capacity[f]} level={s.risk[f]} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </Table>
      </Section>
      <Section title="Routes">
        <Table>
          <thead><tr><th>Route</th><th>Status</th><th className="text-right">Transit</th><th className="text-right">Max shipment</th></tr></thead>
          <tbody>
            {n.routes.map((r) => (
              <tr key={r.id}>
                <td className="num whitespace-nowrap text-xs">{r.id}</td>
                <td><StatusPill status={r.status === "AVAILABLE" ? "healthy" : "down"}>{r.status}</StatusPill></td>
                <td className="num whitespace-nowrap text-right">{r.transit_ticks} ticks <span className="text-slate-500">· {r.transit_ticks * 15} min</span></td>
                <td className="num whitespace-nowrap text-right">{fmt.l(r.max_shipment)}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Section>
    </div>
  );
}

function AllocTable({ rows, onCancel }: { rows: Dashboard["allocations"]; onCancel?: (id: number) => void }) {
  const [confirmId, setConfirmId] = useState<number | null>(null);
  if (rows.length === 0) return <Empty>No allocations yet. Approved recommendations show up here once the simulator accepts them.</Empty>;
  return (
    <Table>
      <thead>
        <tr>
          <th className="text-right">#</th><th>Status</th><th>Route</th><th>Fuel</th><th className="text-right">Quantity</th><th className="text-right">Created</th><th className="text-right">ETA / arrived</th><th>Key</th>
          {onCancel && <th><span className="sr-only">Action</span></th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((a) => (
          <tr key={a.id} className="align-top">
            <td className="num text-right text-slate-400">{a.id}</td>
            <td><StateLabel status={a.status} />{a.failure_reason && <div className="mt-0.5 text-xs text-rose-300">{a.failure_reason}</div>}</td>
            <td className="num whitespace-nowrap text-xs">{a.route_id}</td>
            <td>{a.fuel_type}</td>
            <td className="num whitespace-nowrap text-right">{fmt.l(a.quantity)}</td>
            <td className="num text-right">{a.created_tick}</td>
            <td className="num text-right">{a.actual_arrival_tick ?? a.expected_arrival_tick ?? "—"}</td>
            <td className="num max-w-40 truncate text-xs text-slate-500" title={a.idempotency_key}>{a.idempotency_key}</td>
            {onCancel && (
              <td className="whitespace-nowrap text-right">
                {a.status === "PENDING" && (confirmId === a.id ? (
                  <span className="inline-flex gap-1.5">
                    <button className="btn-danger" onClick={() => { onCancel(a.id); setConfirmId(null); }}>Cancel #{a.id}</button>
                    <button className="btn-ghost" onClick={() => setConfirmId(null)}>Keep</button>
                  </span>
                ) : <button className="btn-ghost" onClick={() => setConfirmId(a.id)}>Cancel…</button>)}
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

export function AllocationsView() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["allocations"], queryFn: api.allocations, refetchInterval: 2000 });
  const cancel = useMutation({ mutationFn: (id: number) => api.cancel(id), onSuccess: () => qc.invalidateQueries() });
  const err = cancel.error as ApiError | null;
  const intents = q.data?.data.intents_attention;
  return (
    <div className="space-y-4">
      {err && <Notice tone="bad" title={err.code}>{err.detail}</Notice>}
      {cancel.isSuccess && <Notice tone="good" title="Cancelled">Allocation #{cancel.variables} was cancelled and its fuel refunded to the depot.</Notice>}
      <Section title="Allocation ledger" sub="The simulator is the source of truth. Cancelling a PENDING allocation refunds the depot.">
        {q.data ? <AllocTable rows={q.data.data.allocations} onCancel={(id) => cancel.mutate(id)} /> : q.isError ? <LoadError error={q.error} what="allocations" /> : <Skeleton rows={5} />}
      </Section>
      <Section title="Intents needing attention" sub="Submitted intents not yet matched to a simulator allocation">
        {!intents ? <Skeleton rows={2} /> : intents.length === 0 ? <Empty>None: every submitted intent is reconciled.</Empty> : (
          <ul className="divide-y divide-slate-800 text-sm">
            {intents.map((i) => (
              <li key={i.idempotency_key} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-2 first:pt-0 last:pb-0">
                <StateLabel status={i.status} /><span className="num text-xs text-slate-300">{i.idempotency_key}</span><span className="text-slate-400">{i.detail}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

export function AlertsView({ d }: { d: Dashboard }) {
  const hist = useQuery({ queryKey: ["alert-history"], queryFn: api.alertHistory, refetchInterval: 5000 });
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Section title={`Open (${d.alerts.open.length})`}><AlertList alerts={d.alerts.open} /></Section>
      <Section title="History" sub="Incident timeline: the tick each alert opened and closed">
        {!hist.data ? (hist.isError ? <LoadError error={hist.error} what="alert history" /> : <Skeleton rows={6} />) : hist.data.data.length === 0 ? <Empty>No alerts recorded yet.</Empty> : (
          <Table>
            <thead><tr><th>Kind</th><th>Title</th><th className="text-right">Opened</th><th className="text-right">Closed</th></tr></thead>
            <tbody>{hist.data.data.map((a) => (
              <tr key={a.id} className="align-top">
                <td className="whitespace-nowrap text-xs text-slate-400">{fmt.label(a.kind)}</td>
                <td className="text-pretty text-sm">{a.title}</td>
                <td className="num text-right">{a.opened_tick}</td>
                <td className="num text-right">{a.closed_tick ?? <span className="font-medium text-slate-50">open</span>}</td>
              </tr>
            ))}</tbody>
          </Table>
        )}
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
      <StatStrip className="grid-cols-2 md:grid-cols-4">
        <Stat label="Overall" value={st ? <StatusPill status={st.overall} /> : "…"} />
        <Stat label="API p95 latency" value={st?.p95_latency_ms != null ? `${st.p95_latency_ms} ms` : "—"} hint={`${st?.requests_60s ?? 0} requests / 60 s`} />
        <Stat label="API error rate" value={st?.error_rate != null ? fmt.pct(st.error_rate, 2) : "—"} tone={st?.error_rate ? "warn" : undefined} />
        <Stat label="Data age" value={st?.data_age_s != null ? `${st.data_age_s} s` : "—"} tone={st?.data_age_s != null && st.data_age_s > 5 ? "warn" : undefined} />
      </StatStrip>
      <Section title="Components">
        {!st ? (s.isError ? <LoadError error={s.error} what="system status" /> : <Skeleton rows={8} />) : (
          <Table>
            <thead><tr><th>Component</th><th>Status</th><th>Detail</th></tr></thead>
            <tbody>{Object.entries(st.components).map(([name, c]) => (
              <tr key={name}><td className="w-56 whitespace-nowrap">{fmt.label(name)}</td><td className="w-32"><StatusPill status={c.status} /></td><td className="text-sm text-slate-400">{c.detail}</td></tr>
            ))}</tbody>
          </Table>
        )}
        {st && st.degraded.length > 0 && <div className="mt-3"><Notice tone="warn" title="Degraded">{st.degraded.join(", ")}</Notice></div>}
      </Section>
      <Section title="Intelligence">
        <StatStrip className="md:grid-cols-4">
          <Stat label="Forecast model" value={<span className="text-base">{intel.model_version}</span>} hint={intel.method} />
          <Stat label="WAPE (last 24 h)" value={fmt.pct(intel.wape_overall, 1)} hint={FUELS.map((f) => `${f} ${fmt.pct(intel.wape_by_fuel[f], 1)}`).join(" · ")} />
          <Stat label="Planner" value={<span className="text-base">{intel.planner.policy}</span>} hint={`${intel.planner.duration_ms} ms per plan`} />
          <Stat label="Refresh" value={`${d.health.worker.refresh_ms} ms`} hint={`circuit ${d.health.simulator.circuit} · stream ${d.health.simulator.stream}`} />
        </StatStrip>
      </Section>
    </div>
  );
}

/** {"result":{"tick":58}} → "result tick=58" — scannable, the full JSON stays in the title. */
const summarize = (p: Record<string, unknown>) =>
  Object.entries(p)
    .map(([k, v]) => `${k} ${v && typeof v === "object" ? Object.entries(v).map(([a, b]) => `${a}=${typeof b === "object" ? JSON.stringify(b) : String(b)}`).join(" ") : String(v)}`)
    .join(" · ");

export function DecisionsView() {
  const q = useQuery({ queryKey: ["decisions"], queryFn: api.decisions, refetchInterval: 3000 });
  if (!q.data) return q.isError ? <LoadError error={q.error} what="decisions" /> : <Skeleton rows={8} />;
  const { recommendations: recs, audit } = q.data.data;
  return (
    <div className="space-y-4">
      <Section title="Decided recommendations" sub="Operator outcomes, newest first">
        {recs.length === 0 ? <Empty>No decisions yet. Approve or reject a recommendation to see it here.</Empty> : (
          <Table>
            <thead><tr><th>Status</th><th>Station</th><th>Fuel</th><th className="text-right">Quantity</th><th className="text-right">Tick</th><th>Decided by · note</th></tr></thead>
            <tbody>{recs.map((r) => (
              <tr key={r.id}>
                <td><StateLabel status={r.status} /></td>
                <td className="whitespace-nowrap capitalize">{fmt.name(r.station_id)}</td>
                <td>{r.fuel_type}</td>
                <td className="num whitespace-nowrap text-right">{fmt.l(r.quantity)}</td>
                <td className="num text-right">{r.created_tick}</td>
                <td className="text-xs text-slate-400">{r.decided_by ?? "system"}{r.decision_note ? ` · ${r.decision_note}` : ""}</td>
              </tr>
            ))}</tbody>
          </Table>
        )}
      </Section>
      <Section title="Audit log" sub="Every write and test-plane action, newest first">
        {audit.length === 0 ? <Empty>Nothing recorded yet.</Empty> : (
          <Table>
            <thead><tr><th>Time</th><th className="text-right">Tick</th><th>Event</th><th>Actor</th><th>Details</th></tr></thead>
            <tbody>{audit.map((a) => (
              <tr key={a.id}>
                <td className="num whitespace-nowrap text-xs text-slate-400">{new Date(a.ts * 1000).toLocaleTimeString()}</td>
                <td className="num text-right text-xs">{a.tick ?? "—"}</td>
                <td className="num whitespace-nowrap text-xs text-slate-100">{a.kind}</td>
                <td className="whitespace-nowrap text-xs text-slate-300">{a.actor}</td>
                <td className="num max-w-[32rem] truncate text-xs text-slate-400" title={JSON.stringify(a.payload)}>{summarize(a.payload)}</td>
              </tr>
            ))}</tbody>
          </Table>
        )}
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
  const run = useMutation({ mutationFn: ({ fn }: { label: string; fn: () => Promise<unknown> }) => fn(), onSuccess: () => qc.invalidateQueries() });
  const go = (label: string, fn: () => Promise<unknown>) => run.mutate({ label, fn });
  const armed = confirmText.trim().toUpperCase() === "SIMULATE";
  const busy = run.isPending;
  const durationOk = duration >= 5 && duration <= 600;
  const err = run.error as ApiError | null;
  return (
    <div className="space-y-4">
      <section className={`rounded-xl border p-4 transition-colors duration-150 ${armed ? "border-rose-400/70 bg-rose-500/10" : "border-rose-500/30 bg-rose-500/5"}`}>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="max-w-prose">
            <h2 className="flex items-center gap-2 font-semibold text-rose-100">
              <Glyph shape={armed ? "octagon" : "circle"} className="text-rose-400" />
              {armed ? "Destructive actions armed" : "Destructive actions locked"}
            </h2>
            <p className="mt-1 text-sm text-rose-200/85">Reset, crisis scenarios and fault injection change the shared simulator world for everyone watching it.</p>
          </div>
          <label className="block">
            <span className="mb-1 block text-xs text-rose-200/85">Type SIMULATE to arm</span>
            <input className="field num w-44 uppercase" value={confirmText} onChange={(e) => setConfirmText(e.target.value)} autoComplete="off" spellCheck={false} />
          </label>
        </div>
      </section>
      {err && <Notice tone="bad" title={err.code}>{err.detail}</Notice>}
      {busy && <Notice tone="info">Running: {run.variables?.label}…</Notice>}
      {run.isSuccess && !busy && <Notice tone="good" title="Done">{run.variables?.label}</Notice>}
      <Section title="Clock" sub="One tick is 15 simulated minutes">
        <div className="flex flex-wrap gap-2">
          <button className="btn-ghost" disabled={busy} onClick={() => go("step 1 tick", () => api.test.action("step"))}>Step 1 tick</button>
          <button className="btn-ghost" disabled={busy} onClick={() => go("step 8 ticks", async () => { for (let i = 0; i < 8; i++) await api.test.action("step"); })}>Step 8 ticks (2 h)</button>
          <button className="btn-ghost" disabled={busy} onClick={() => go("run", () => api.test.action("run"))}>Run</button>
          <button className="btn-ghost" disabled={busy} onClick={() => go("pause", () => api.test.action("pause"))}>Pause</button>
          <button className="btn-danger" disabled={!armed || busy} onClick={() => go("reset world", () => api.test.action("reset"))}>Reset world</button>
        </div>
      </Section>
      <Section title="Crisis scenarios" sub="Each scenario targets the named station, depot or route only">
        {!scenarios.data ? (scenarios.isError ? <LoadError error={scenarios.error} what="scenarios" /> : <Skeleton rows={2} />) : (
          <div className="flex flex-wrap gap-2">
            {Object.keys(scenarios.data.data).map((name) => (
              <button key={name} className="btn-danger" disabled={!armed || busy} onClick={() => go(`scenario ${fmt.label(name).toLowerCase()}`, () => api.test.scenario(name))}>{fmt.label(name)}</button>
            ))}
          </div>
        )}
      </Section>
      <Section title="Fault injection" sub="Exercises the resilient client: circuit breaker, cache fallback and stream reconnect">
        <div className="flex flex-wrap items-start gap-3">
          <label className="block">
            <span className="mb-1 block text-xs text-slate-400">Fault</span>
            <select className="field" value={fault} onChange={(e) => setFault(e.target.value)}>
              {["unavailable", "error_rate", "latency", "stale_data", "stream_disconnect"].map((f) => <option key={f} value={f}>{fmt.label(f)}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-slate-400">Duration (s)</span>
            <input type="number" className="field num w-24" value={duration} min={5} max={600} onChange={(e) => setDuration(Number(e.target.value))}
              aria-invalid={durationOk ? undefined : true} aria-describedby="duration-help" />
            <span id="duration-help" className={`mt-1 block text-xs ${durationOk ? "text-slate-500" : "text-rose-300"}`}>5 to 600</span>
          </label>
          <div className="flex gap-2 pt-5">
            <button className="btn-danger" disabled={!armed || busy || !durationOk} onClick={() => go(`inject ${fmt.label(fault).toLowerCase()} for ${duration} s`, () => api.test.fault(fault, duration))}>Inject fault</button>
            <button className="btn-ghost" disabled={busy} onClick={() => go("clear all faults", () => api.test.action("faults-clear"))}>Clear all faults</button>
          </div>
        </div>
      </Section>
    </div>
  );
}
