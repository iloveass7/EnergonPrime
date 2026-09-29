import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ApiError, api, fmt, type Recommendation } from "../api";
import { Empty, LevelBadge, Modal, Section } from "../ui";

export function RecommendationCard({ rec, blocked, compact }: { rec: Recommendation; blocked: string | null; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-md border border-slate-800 bg-slate-950/60 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <LevelBadge level={rec.level} />
          <span className="font-medium">{rec.station_name}</span>
          <span className="text-slate-400">{rec.fuel_type}</span>
        </div>
        <button className="btn-primary" onClick={() => setOpen(true)} aria-label={`Review recommendation for ${rec.station_name} ${rec.fuel_type}`}>
          Review
        </button>
      </div>
      <div className="num mt-2 text-sm text-slate-300">
        Send <b className="text-slate-100">{fmt.l(rec.quantity)}</b> from {rec.depot_name} via {rec.route_id} · ETA tick {rec.eta_tick}
      </div>
      {!compact && (
        <div className="num mt-1 text-xs text-slate-400">
          Projected unmet {fmt.l(rec.impact.unmet_before_l)} → {fmt.l(rec.impact.unmet_after_l)} · model-estimated risk{" "}
          {fmt.pct(rec.impact.risk_before)} → {fmt.pct(rec.impact.risk_after)}
        </div>
      )}
      {open && <RecommendationDetail rec={rec} blocked={blocked} onClose={() => setOpen(false)} />}
    </div>
  );
}

function RecommendationDetail({ rec, blocked, onClose }: { rec: Recommendation; blocked: string | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [qty, setQty] = useState<string>(String(rec.quantity));
  const [note, setNote] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const approve = useMutation({
    mutationFn: () => api.approve(rec.id, Number(qty) !== rec.quantity ? Number(qty) : undefined, note || undefined),
    onSuccess: () => qc.invalidateQueries(),
  });
  const reject = useMutation({ mutationFn: () => api.reject(rec.id, rejectReason), onSuccess: () => { qc.invalidateQueries(); onClose(); } });
  const chart = rec.impact.inventory_before.map((v, i) => ({ t: `+${i + 1}`, "No shipment": v, Recommended: rec.impact.inventory_after[i] }));
  const err = (approve.error ?? reject.error) as ApiError | null;
  const result = approve.data?.data;

  return (
    <Modal title={`${rec.station_name} · ${rec.fuel_type}`} onClose={onClose}>
      <div className="max-h-[75vh] space-y-4 overflow-y-auto pr-1 text-sm">
        <div className="flex flex-wrap gap-2 text-xs text-slate-400">
          <LevelBadge level={rec.level} /> <span>confidence {rec.confidence}</span> <span>policy {rec.policy_used}</span>
          <span>model {rec.model_version}</span> <span>valid until tick {rec.valid_until_tick}</span>
        </div>
        <table className="w-full">
          <thead><tr><th>Plan</th><th>Unmet L</th><th>Risk</th><th>Why</th></tr></thead>
          <tbody>
            {rec.comparison.map((c) => (
              <tr key={c.plan} className="border-t border-slate-800 align-top">
                <td>{c.plan}</td>
                <td className="num">{fmt.l(c.projected_unmet_l)}</td>
                <td className="num">{c.risk == null ? "—" : fmt.pct(c.risk)}</td>
                <td className="text-xs text-slate-400">{c.why}{c.binding_constraint ? ` · binding: ${c.binding_constraint}` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="h-48" aria-label="Projected inventory with and without the shipment">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chart} margin={{ left: 8, right: 8 }}>
              <CartesianGrid stroke="#1e293b" />
              <XAxis dataKey="t" stroke="#64748b" fontSize={11} interval={3} />
              <YAxis stroke="#64748b" fontSize={11} tickFormatter={(v) => `${(v / 1000).toFixed(1)}k`} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155" }} formatter={(v) => fmt.l(Number(v))} />
              <Legend />
              <ReferenceLine y={rec.impact.capacity} stroke="#64748b" strokeDasharray="4 4" label={{ value: "capacity", fill: "#64748b", fontSize: 10 }} />
              <Line dataKey="No shipment" stroke="#f97316" dot={false} strokeDasharray="5 3" />
              <Line dataKey="Recommended" stroke="#38bdf8" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="text-xs text-slate-400">Stockout: {fmt.h(rec.impact.stockout_before_h)} → {fmt.h(rec.impact.stockout_after_h)} · ticks of 15 simulated minutes · model-estimated on simulated data</div>
        <ul className="list-disc space-y-1 pl-5 text-slate-300">{rec.reasons.map((r) => <li key={r}>{r}</li>)}</ul>

        {blocked && <div className="rounded border border-amber-400/50 bg-amber-400/10 p-2 text-amber-200">Execution blocked: {blocked}</div>}
        {err && <div role="alert" className="rounded border border-rose-500/60 bg-rose-600/15 p-2 text-rose-200">{err.code}: {err.detail}{err.upstream ? ` (simulator: ${err.upstream})` : ""}</div>}
        {result && (
          <div role="status" className="rounded border border-emerald-500/50 bg-emerald-500/10 p-2 text-emerald-200">
            {result.recommendation.status} · {result.intents.map((i) => `${i.status} ${i.idempotency_key}${i.sim_allocation_id ? ` → allocation #${i.sim_allocation_id}` : ""}${i.upstream_code ? ` (${i.upstream_code})` : ""}`).join(", ")}
          </div>
        )}

        {!result && (
          <div className="space-y-2 border-t border-slate-800 pt-3">
            <label className="flex items-center gap-2">
              <span className="w-24 text-slate-400">Quantity (L)</span>
              <input className="num w-32 rounded border border-slate-700 bg-slate-950 px-2 py-1" value={qty} onChange={(e) => setQty(e.target.value)} inputMode="numeric" />
            </label>
            <label className="flex items-center gap-2">
              <span className="w-24 text-slate-400">Note</span>
              <input className="flex-1 rounded border border-slate-700 bg-slate-950 px-2 py-1" value={note} onChange={(e) => setNote(e.target.value)} placeholder="optional" />
            </label>
            {!confirming ? (
              <div className="flex flex-wrap gap-2">
                <button className="btn-primary" disabled={!!blocked || approve.isPending} onClick={() => setConfirming(true)}>Approve & submit</button>
                <input className="flex-1 rounded border border-slate-700 bg-slate-950 px-2 py-1" value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} placeholder="reason to reject" aria-label="Reason to reject" />
                <button className="btn-ghost" disabled={rejectReason.trim().length < 3 || reject.isPending} onClick={() => reject.mutate()}>Reject</button>
              </div>
            ) : (
              <div className="flex items-center gap-2 rounded border border-sky-600/50 bg-sky-600/10 p-2">
                <span className="flex-1">Submit {fmt.l(Number(qty))} {rec.fuel_type} to the simulator? This creates a real (simulated) allocation.</span>
                <button className="btn-primary" disabled={approve.isPending} onClick={() => approve.mutate()}>{approve.isPending ? "Submitting…" : "Confirm"}</button>
                <button className="btn-ghost" onClick={() => setConfirming(false)}>Back</button>
              </div>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}

export function RecommendationsView({ recs, blocked }: { recs: Recommendation[]; blocked: string | null }) {
  return (
    <Section title={`Recommendations (${recs.length})`} right={<span className="text-xs text-slate-400">MANUAL policy · human approval required</span>}>
      {recs.length === 0 ? <Empty>No shipments needed right now: every station is covered over the horizon.</Empty> : (
        <div className="grid gap-3 lg:grid-cols-2">{recs.map((r) => <RecommendationCard key={r.id} rec={r} blocked={blocked} />)}</div>
      )}
    </Section>
  );
}
