import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ApiError, api, fmt, type Recommendation } from "../api";
import { Empty, ICON, Icon, LevelBadge, Modal, Notice, Section, Table } from "../ui";

const reviewLabel = (rec: Recommendation) => `Review recommendation for ${rec.station_name} ${rec.fuel_type}`;

function Delta({ from, to }: { from: string; to: string }) {
  return <span className="whitespace-nowrap"><span className="text-slate-500">{from}</span> <span aria-hidden className="text-slate-500">→</span><span className="sr-only">to</span> <span className="text-slate-50">{to}</span></span>;
}

/** Compact row for side panels: the whole row opens the review. */
export function RecommendationRow({ rec, onReview }: { rec: Recommendation; onReview: (rec: Recommendation) => void }) {
  return (
    <button onClick={() => onReview(rec)} aria-label={reviewLabel(rec)}
      className="focus-ring group flex w-full cursor-pointer items-start gap-3 rounded-lg px-2 py-2.5 text-left transition-colors duration-150 hover:bg-slate-800/60">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <LevelBadge level={rec.level} />
          <span className="truncate text-sm font-medium">{rec.station_name}</span>
          <span className="ml-auto text-xs text-slate-400">{rec.fuel_type}</span>
        </div>
        <div className="mt-1 text-xs text-slate-400">
          Send <span className="num text-slate-100">{fmt.l(rec.quantity)}</span> from {rec.depot_name} · ETA tick <span className="num">{rec.eta_tick}</span>
        </div>
      </div>
      <Icon d={ICON.chevron} className="mt-1 text-slate-500 transition-colors duration-150 group-hover:text-slate-200" />
    </button>
  );
}

/** Review dialog state lives above the list, so a re-plan never closes it under the operator. */
export function useRecReview(recs: Recommendation[], blocked: string | null) {
  const [open, setOpen] = useState<Recommendation | null>(null);
  // Follow re-plans: review the planner's current recommendation for the same station-fuel,
  // so approving never targets an id the planner already replaced.
  const current = open && (recs.find((r) => r.id === open.id) ?? recs.find((r) => r.station_id === open.station_id && r.fuel_type === open.fuel_type));
  const dialog = open ? (
    <RecommendationDetail key={`${open.station_id}-${open.fuel_type}`} rec={current ?? open} blocked={blocked} superseded={!current} onClose={() => setOpen(null)} />
  ) : null;
  return [setOpen, dialog] as const;
}

function RecommendationDetail({ rec, blocked, superseded, onClose }: { rec: Recommendation; blocked: string | null; superseded: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [qty, setQty] = useState<string>(String(rec.quantity));
  const [note, setNote] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [mode, setMode] = useState<"idle" | "confirm" | "reject">("idle");
  const q = Number(qty);
  const qtyError = qty.trim() === "" || !Number.isFinite(q) || q <= 0 ? "Enter a quantity above 0 L." : null;
  const approve = useMutation({
    mutationFn: () => api.approve(rec.id, q !== rec.quantity ? q : undefined, note || undefined),
    onSuccess: () => qc.invalidateQueries(),
  });
  const reject = useMutation({ mutationFn: () => api.reject(rec.id, rejectReason), onSuccess: () => { qc.invalidateQueries(); onClose(); } });
  const chart = rec.impact.inventory_before.map((v, i) => ({ t: `+${i + 1}`, "No shipment": v, Recommended: rec.impact.inventory_after[i] }));
  const horizonH = rec.impact.inventory_before.length / 4;
  const err = (approve.error ?? reject.error) as ApiError | null;
  const result = approve.data?.data;

  const footer = (
    <div className="space-y-3 text-sm">
      {superseded && !approve.data && <Notice tone="warn" title="Replaced by the planner">Approving this one will be refused. Close and review the current recommendation.</Notice>}
      {blocked && <Notice tone="warn" title="Execution blocked">{blocked}</Notice>}
      {err && <Notice tone="bad" title={err.code}>{err.detail}{err.upstream ? ` (simulator: ${err.upstream})` : ""}</Notice>}
      {result ? (
        <>
          <Notice tone="good" title={result.recommendation.status}>
            {result.intents.map((i) => `${i.status} ${i.idempotency_key}${i.sim_allocation_id ? ` → allocation #${i.sim_allocation_id}` : ""}${i.upstream_code ? ` (${i.upstream_code})` : ""}`).join(", ")}
          </Notice>
          <button className="btn-ghost" onClick={onClose}>Close</button>
        </>
      ) : mode === "reject" ? (
        <div className="space-y-2">
          <label className="block">
            <span className="mb-1 block text-xs text-slate-400">Reason for rejecting</span>
            <input autoFocus className="field w-full" value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} aria-describedby="reject-help" />
            <span id="reject-help" className="mt-1 block text-xs text-slate-500">At least 3 characters. Recorded in the audit log.</span>
          </label>
          <div className="flex flex-wrap gap-2">
            <button className="btn-danger" disabled={rejectReason.trim().length < 3 || reject.isPending} onClick={() => reject.mutate()}>{reject.isPending ? "Rejecting…" : "Reject recommendation"}</button>
            <button className="btn-ghost" disabled={reject.isPending} onClick={() => setMode("idle")}>Back</button>
          </div>
        </div>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-[11rem_1fr]">
            <label className="block">
              <span className="mb-1 block text-xs text-slate-400">Quantity (L)</span>
              <input className="field num w-full" value={qty} onChange={(e) => setQty(e.target.value)} inputMode="numeric" disabled={mode === "confirm"}
                aria-invalid={qtyError ? true : undefined} aria-describedby="qty-help" />
              <span id="qty-help" className={`mt-1 block text-xs ${qtyError ? "text-rose-300" : "text-slate-500"}`}>
                {qtyError ?? (q !== rec.quantity ? `Planner suggested ${fmt.l(rec.quantity)}` : "Planner suggestion")}
              </span>
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-slate-400">Note <span className="text-slate-500">(optional)</span></span>
              <input className="field w-full" value={note} onChange={(e) => setNote(e.target.value)} disabled={mode === "confirm"} />
            </label>
          </div>
          {mode === "idle" ? (
            <div className="flex flex-wrap gap-2">
              <button className="btn-primary" disabled={!!blocked || !!qtyError} onClick={() => setMode("confirm")}>Approve & submit</button>
              <button className="btn-ghost" onClick={() => setMode("reject")}>Reject…</button>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2 rounded-lg border border-sky-400/40 bg-sky-400/10 p-3">
              <p className="min-w-48 flex-1 text-sky-100">Submit <b className="num font-semibold">{fmt.l(q)}</b> {rec.fuel_type} to the simulator? This creates a real (simulated) allocation.</p>
              <button className="btn-primary" disabled={approve.isPending || !!blocked} onClick={() => approve.mutate()}>{approve.isPending ? "Submitting…" : "Confirm"}</button>
              <button className="btn-ghost" disabled={approve.isPending} onClick={() => setMode("idle")}>Back</button>
            </div>
          )}
        </>
      )}
    </div>
  );

  return (
    <Modal title={`${rec.station_name} · ${rec.fuel_type}`} onClose={onClose} footer={footer}>
      <div className="space-y-5 text-sm">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
          <LevelBadge level={rec.level} />
          <span>Confidence <b className="font-medium text-slate-200">{rec.confidence}</b></span>
          <span>Policy <b className="font-medium text-slate-200">{rec.policy_used}</b></span>
          <span>Model <b className="num font-medium text-slate-200">{rec.model_version}</b></span>
          <span>Valid until tick <b className="num font-medium text-slate-200">{rec.valid_until_tick}</b></span>
        </div>

        <div>
          <h3 className="mb-2 text-xs font-medium text-slate-400">Plans compared</h3>
          <Table>
            <thead><tr><th>Plan</th><th className="text-right">Unmet</th><th className="text-right">Risk</th><th>Why</th></tr></thead>
            <tbody>
              {rec.comparison.map((c) => {
                const chosen = c.plan.startsWith("Recommended");
                return (
                  <tr key={c.plan} className={`align-top ${chosen ? "bg-sky-400/[0.06]" : ""}`}>
                    <td className={chosen ? "font-medium text-slate-50" : "text-slate-300"}>{c.plan}</td>
                    <td className="num whitespace-nowrap text-right">{fmt.l(c.projected_unmet_l)}</td>
                    <td className="num text-right">{c.risk == null ? "—" : fmt.pct(c.risk)}</td>
                    <td className="min-w-40 text-xs text-slate-400">{c.why}{c.binding_constraint ? ` · binding: ${c.binding_constraint}` : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        </div>

        <figure>
          <figcaption className="mb-2 flex flex-wrap justify-between gap-x-3 text-xs text-slate-400">
            <span>Projected inventory, next {horizonH} simulated hours</span>
            <span className="num">Stockout {fmt.h(rec.impact.stockout_before_h)} → {fmt.h(rec.impact.stockout_after_h)}</span>
          </figcaption>
          <div className="h-52" role="img" aria-label={`Projected inventory: without shipment stockout ${fmt.h(rec.impact.stockout_before_h)}; with the recommended shipment ${fmt.h(rec.impact.stockout_after_h)}.`}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chart} margin={{ top: 8, left: 0, right: 8, bottom: 0 }}>
                <CartesianGrid stroke="var(--color-slate-800)" vertical={false} />
                <XAxis dataKey="t" stroke="var(--color-slate-500)" fontSize={11} interval={3} tickLine={false} />
                <YAxis stroke="var(--color-slate-500)" fontSize={11} width={40} tickLine={false} axisLine={false} tickFormatter={(v) => `${(v / 1000).toFixed(1)}k`} />
                <Tooltip
                  contentStyle={{ background: "var(--color-slate-950)", border: "1px solid var(--color-slate-700)", borderRadius: 8, fontSize: 12 }}
                  labelStyle={{ color: "var(--color-slate-300)" }}
                  labelFormatter={(l) => `${l} ticks`}
                  formatter={(v) => fmt.l(Number(v))}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <ReferenceLine y={rec.impact.capacity} stroke="var(--color-slate-500)" strokeDasharray="4 4" label={{ value: "capacity", fill: "var(--color-slate-500)", fontSize: 10 }} />
                <Line dataKey="No shipment" stroke="var(--color-orange-400)" dot={false} strokeDasharray="5 3" isAnimationActive={false} />
                <Line dataKey="Recommended" stroke="var(--color-sky-400)" dot={false} strokeWidth={2} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-1 text-xs text-slate-500">Ticks of 15 simulated minutes. Model-estimated on simulated data.</p>
        </figure>

        <div>
          <h3 className="mb-1.5 text-xs font-medium text-slate-400">Why this plan</h3>
          <ul className="list-disc space-y-1 pl-5 text-slate-300 marker:text-slate-600">{rec.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      </div>
    </Modal>
  );
}

export function RecommendationsView({ recs, blocked }: { recs: Recommendation[]; blocked: string | null }) {
  const [review, dialog] = useRecReview(recs, blocked);
  return (
    <Section title={`Awaiting approval (${recs.length})`} sub="MANUAL policy: nothing ships until an operator approves">
      {recs.length === 0 ? <Empty>No shipments needed: every station is covered over the 8-hour horizon.</Empty> : (
        <Table>
          <thead>
            <tr>
              <th>Level</th><th>Station</th><th>Fuel</th><th className="text-right">Ship</th><th>From</th><th className="text-right">ETA tick</th>
              <th className="text-right">Projected unmet</th><th className="text-right">Shortage risk</th><th><span className="sr-only">Action</span></th>
            </tr>
          </thead>
          <tbody>
            {recs.map((r) => (
              <tr key={r.id}>
                <td><LevelBadge level={r.level} /></td>
                <td className="whitespace-nowrap font-medium">{r.station_name}</td>
                <td>{r.fuel_type}</td>
                <td className="num whitespace-nowrap text-right text-slate-50">{fmt.l(r.quantity)}</td>
                <td className="whitespace-nowrap"><div>{r.depot_name}</div><div className="num text-xs text-slate-500">{r.route_id}</div></td>
                <td className="num text-right">{r.eta_tick}</td>
                <td className="num text-right"><Delta from={fmt.l(r.impact.unmet_before_l)} to={fmt.l(r.impact.unmet_after_l)} /></td>
                <td className="num text-right"><Delta from={fmt.pct(r.impact.risk_before)} to={fmt.pct(r.impact.risk_after)} /></td>
                <td className="text-right"><button className="btn-secondary" onClick={() => review(r)} aria-label={reviewLabel(r)}>Review</button></td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {dialog}
    </Section>
  );
}
