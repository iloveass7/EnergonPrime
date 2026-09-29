import type { ReactNode } from "react";
import type { Level } from "./api";

const LEVEL_STYLE: Record<string, string> = {
  CRITICAL: "bg-rose-600/20 text-rose-300 border-rose-500/60",
  HIGH: "bg-orange-500/15 text-orange-300 border-orange-500/60",
  MEDIUM: "bg-amber-400/10 text-amber-200 border-amber-400/50",
  LOW: "bg-emerald-500/10 text-emerald-300 border-emerald-500/40",
};
const LEVEL_ICON: Record<string, string> = { CRITICAL: "▲▲", HIGH: "▲", MEDIUM: "◆", LOW: "●" };

export function LevelBadge({ level }: { level: Level | string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-semibold ${LEVEL_STYLE[level] ?? "border-slate-600"}`}>
      <span aria-hidden>{LEVEL_ICON[level] ?? "?"}</span>
      {level}
    </span>
  );
}

const STATUS_STYLE: Record<string, string> = {
  healthy: "text-emerald-300 border-emerald-500/50 bg-emerald-500/10",
  degraded: "text-amber-200 border-amber-400/50 bg-amber-400/10",
  down: "text-rose-300 border-rose-500/60 bg-rose-600/15",
};
const STATUS_ICON: Record<string, string> = { healthy: "✓", degraded: "!", down: "✕" };

export function StatusPill({ status, children }: { status: string; children?: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[status] ?? STATUS_STYLE.degraded}`}>
      <span aria-hidden>{STATUS_ICON[status] ?? "?"}</span>
      {children ?? status}
    </span>
  );
}

const ALLOC_STYLE: Record<string, string> = {
  PENDING: "text-sky-300",
  IN_TRANSIT: "text-indigo-300",
  ARRIVED: "text-emerald-300",
  FAILED: "text-rose-300",
  CANCELLED: "text-slate-400",
};

export function AllocStatus({ status }: { status: string }) {
  return <span className={`text-xs font-semibold ${ALLOC_STYLE[status] ?? ""}`}>{status}</span>;
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "good" | "warn" | "bad" }) {
  const color = tone === "bad" ? "text-rose-300" : tone === "warn" ? "text-amber-200" : tone === "good" ? "text-emerald-300" : "text-slate-100";
  return (
    <div className="bg-slate-900 px-4 py-3.5">
      <div className="text-[13px] text-slate-400">{label}</div>
      <div className={`num mt-1 text-2xl font-medium ${color}`}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

/** One continuous strip of stats; 1px gaps draw the dividers at any wrap. */
export function StatStrip({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`grid gap-px overflow-hidden rounded-xl border border-slate-800 bg-slate-800 ${className}`}>{children}</div>;
}

export function Bar({ value, max, level }: { value: number; max: number; level?: string }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (100 * value) / max)) : 0;
  const color = level === "CRITICAL" ? "bg-rose-500" : level === "HIGH" ? "bg-orange-400" : level === "MEDIUM" ? "bg-amber-300" : "bg-emerald-400";
  return (
    <div className="h-1.5 w-full rounded-full bg-slate-800" role="meter" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
      <div className={`h-1.5 rounded-full ${color} transition-[width] duration-300 ease-out`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Section({ title, sub, right, children }: { title: string; sub?: ReactNode; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-[15px] font-semibold text-slate-100">{title}</h2>
          {sub && <p className="mt-0.5 text-xs text-slate-500">{sub}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-slate-800 py-6 text-center text-sm text-slate-500">{children}</div>;
}

export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/75 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-label={title} onClick={onClose}>
      <div className="card w-full max-w-2xl shadow-2xl shadow-black/50" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h3 className="font-semibold">{title}</h3>
          <button className="btn-ghost" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}
