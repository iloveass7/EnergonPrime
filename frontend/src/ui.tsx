import { useEffect, useRef, type ReactNode } from "react";
import type { Level } from "./api";

/* Shape-coded priority (never color alone): octagon > triangle > diamond > circle; dot = normal. */
type Shape = "octagon" | "triangle" | "diamond" | "circle" | "dot";
const SHAPE_PATH: Record<Shape, ReactNode> = {
  octagon: <path d="M4 1h4l3 3v4l-3 3H4L1 8V4z" fill="currentColor" />,
  triangle: <path d="M6 1.2 11.3 10.6H.7z" fill="currentColor" />,
  diamond: <path d="M6 1 11 6 6 11 1 6z" fill="currentColor" />,
  circle: <circle cx="6" cy="6" r="3.6" fill="none" stroke="currentColor" strokeWidth="1.6" />,
  dot: <circle cx="6" cy="6" r="3" fill="currentColor" />,
};

export function Glyph({ shape, className = "" }: { shape: Shape; className?: string }) {
  return <svg viewBox="0 0 12 12" aria-hidden className={`size-3 shrink-0 ${className}`}>{SHAPE_PATH[shape]}</svg>;
}

export function Icon({ d, className = "" }: { d: string; className?: string }) {
  return (
    <svg viewBox="0 0 16 16" aria-hidden className={`size-4 shrink-0 ${className}`} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  );
}
export const ICON = { close: "M4 4l8 8M12 4l-8 8", chevron: "M6 3.5 10.5 8 6 12.5", arrow: "M3 8h10M9 4l4 4-4 4" };

const LEVEL: Record<string, { shape: Shape; badge: string; glyph: string }> = {
  CRITICAL: { shape: "octagon", badge: "border-rose-400/70 bg-rose-500/20 text-rose-100", glyph: "text-rose-400" },
  HIGH: { shape: "triangle", badge: "border-orange-400/50 bg-orange-500/15 text-orange-200", glyph: "text-orange-400" },
  MEDIUM: { shape: "diamond", badge: "border-amber-300/40 bg-amber-400/10 text-amber-200", glyph: "text-amber-300" },
  LOW: { shape: "circle", badge: "border-slate-700 text-slate-300", glyph: "text-slate-400" },
};

export function LevelBadge({ level }: { level: Level | string }) {
  const s = LEVEL[level] ?? LEVEL.LOW;
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-xs font-semibold ${s.badge}`}>
      <Glyph shape={s.shape} className={`size-2.5 ${s.glyph}`} />
      {level}
    </span>
  );
}

const SEVERITY: Record<string, { shape: Shape; color: string }> = {
  critical: { shape: "octagon", color: "text-rose-400" },
  warning: { shape: "triangle", color: "text-amber-300" },
  info: { shape: "circle", color: "text-slate-400" },
};

export function SeverityGlyph({ severity, className = "" }: { severity: string; className?: string }) {
  const s = SEVERITY[severity] ?? SEVERITY.info;
  return <Glyph shape={s.shape} className={`${s.color} ${className}`} />;
}

const STATUS: Record<string, { shape: Shape; pill: string; glyph: string }> = {
  healthy: { shape: "dot", pill: "border-slate-700 text-slate-300", glyph: "text-emerald-400" },
  degraded: { shape: "triangle", pill: "border-amber-400/50 bg-amber-400/10 text-amber-100", glyph: "text-amber-300" },
  down: { shape: "octagon", pill: "border-rose-500/60 bg-rose-500/15 text-rose-100", glyph: "text-rose-400" },
};

export function StatusPill({ status, children }: { status: string; children?: ReactNode }) {
  const s = STATUS[status] ?? STATUS.degraded;
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${s.pill}`}>
      <Glyph shape={s.shape} className={`size-2.5 ${s.glyph}`} />
      {children ?? status}
    </span>
  );
}

/** Lifecycle states of allocations and decided recommendations. */
const STATE_STYLE: Record<string, string> = {
  PENDING: "text-sky-300",
  IN_TRANSIT: "text-slate-100",
  ARRIVED: "text-slate-300",
  EXECUTED: "text-slate-100",
  APPROVED: "text-slate-100",
  FAILED: "text-rose-300",
  REJECTED: "text-rose-300",
  CANCELLED: "text-slate-500",
  SUPERSEDED: "text-slate-500",
  EXPIRED: "text-slate-500",
};

export function StateLabel({ status }: { status: string }) {
  return <span className={`whitespace-nowrap text-xs font-semibold tracking-wide ${STATE_STYLE[status] ?? "text-slate-300"}`}>{status.replace(/_/g, " ")}</span>;
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "warn" | "bad" }) {
  const color = tone === "bad" ? "text-rose-300" : tone === "warn" ? "text-amber-200" : "text-slate-50";
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
  const color = level === "CRITICAL" ? "bg-rose-500" : level === "HIGH" ? "bg-orange-400" : level === "MEDIUM" ? "bg-amber-300" : "bg-slate-400";
  return (
    <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-800" role="meter" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} aria-label="Fill level">
      <div className={`h-full rounded-full ${color} transition-[width] duration-300 ease-out`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Section({ title, sub, right, children }: { title: string; sub?: ReactNode; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
        <div>
          <h2 className="text-[15px] font-semibold text-slate-50">{title}</h2>
          {sub && <p className="mt-0.5 text-xs text-slate-400">{sub}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

/** Tables scroll inside their card, never the page. */
export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="-mx-4 overflow-x-auto px-4">
      <table className="w-full">{children}</table>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-slate-800 px-4 py-6 text-center text-sm text-slate-400">{children}</div>;
}

export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div role="status" className="space-y-2">
      <span className="sr-only">Loading…</span>
      {Array.from({ length: rows }, (_, i) => <div key={i} aria-hidden className="h-8 animate-pulse rounded-md bg-slate-800/70" />)}
    </div>
  );
}

const NOTICE: Record<string, { box: string; shape: Shape; glyph: string }> = {
  bad: { box: "border-rose-500/50 bg-rose-500/10 text-rose-100", shape: "octagon", glyph: "text-rose-400" },
  warn: { box: "border-amber-400/40 bg-amber-400/10 text-amber-100", shape: "triangle", glyph: "text-amber-300" },
  good: { box: "border-emerald-500/40 bg-emerald-500/10 text-emerald-100", shape: "dot", glyph: "text-emerald-400" },
  info: { box: "border-sky-400/40 bg-sky-400/10 text-sky-100", shape: "circle", glyph: "text-sky-300" },
};

export function Notice({ tone, title, children }: { tone: "bad" | "warn" | "good" | "info"; title?: ReactNode; children?: ReactNode }) {
  const s = NOTICE[tone];
  return (
    <div role={tone === "bad" ? "alert" : "status"} className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${s.box}`}>
      <Glyph shape={s.shape} className={`mt-1 ${s.glyph}`} />
      <div className="min-w-0">{title && <b className="font-semibold">{title}</b>}{title && children ? " · " : null}{children}</div>
    </div>
  );
}

/** Native modal dialog: focus trap, Escape and top layer come from the platform. The footer stays pinned below the scroll area. */
export function Modal({ title, children, footer, onClose }: { title: string; children: ReactNode; footer?: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);
  return (
    <dialog ref={ref} aria-label={title} onClose={onClose} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      className="m-auto max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-2xl flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900 p-0 text-slate-100 shadow-2xl shadow-black/60 open:flex">
      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-slate-800 px-5 py-3">
        <h2 className="font-semibold">{title}</h2>
        <button className="btn-ghost size-8 p-0" onClick={onClose} aria-label="Close"><Icon d={ICON.close} /></button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
      {footer && <div className="shrink-0 border-t border-slate-800 bg-slate-900 px-5 py-4">{footer}</div>}
    </dialog>
  );
}
