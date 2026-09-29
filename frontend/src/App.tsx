import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import { Glyph, Notice, Skeleton, StatusPill } from "./ui";
import { RecommendationsView } from "./views/Recommendations";
import { AlertsView, AllocationsView, CommandCenter, DecisionsView, HealthView, NetworkView, TestControls } from "./views/Views";

const TABS = ["Command Center", "Network", "Recommendations", "Allocations", "Alerts", "Decisions", "System Health", "Test Controls"] as const;
type Tab = (typeof TABS)[number];

const SUBTITLE: Record<Tab, string> = {
  "Command Center": "Network risk and pending actions for the next 8 simulated hours",
  Network: "Depots, stations and routes as the simulator reports them",
  Recommendations: "Planner proposals with their projected impact",
  Allocations: "Shipments in the simulator ledger",
  Alerts: "Open conditions and their incident timeline",
  Decisions: "Operator outcomes and the full audit trail",
  "System Health": "Platform components, latency and model quality",
  "Test Controls": "Test plane: drives the simulator's /admin endpoints and is never part of the decision path",
};

// Hash routes (#network) give every view a shareable link and a working back button.
const slug = (t: Tab) => t.toLowerCase().replace(/ /g, "-");
const fromHash = (): Tab => TABS.find((t) => `#${slug(t)}` === window.location.hash) ?? "Command Center";

function useHashTab() {
  const [tab, setTab] = useState<Tab>(fromHash);
  useEffect(() => {
    const on = () => setTab(fromHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [tab, (t: Tab) => { window.location.hash = slug(t); }] as const;
}

function usePush(): string {
  // Browser SSE from our API (Redis pub/sub); falls back to the 1 s poll when it drops.
  const qc = useQueryClient();
  const [state, setState] = useState("connecting");
  useEffect(() => {
    const es = new EventSource("/api/v1/stream");
    es.onopen = () => setState("live");
    es.onerror = () => setState("polling");
    const refresh = () => qc.invalidateQueries({ queryKey: ["dashboard"] });
    for (const ev of ["state", "resync", "alert.opened", "recommendation.updated"]) es.addEventListener(ev, refresh);
    return () => es.close();
  }, [qc]);
  return state;
}

function Readout({ label, children, warn }: { label: string; children: ReactNode; warn?: boolean }) {
  return (
    <span className="inline-flex items-baseline gap-1.5 whitespace-nowrap">
      <span className="text-xs text-slate-400">{label}</span>
      <span className={`num text-sm ${warn ? "text-amber-200" : "text-slate-100"}`}>{children}</span>
    </span>
  );
}

export default function App() {
  const [tab, setTab] = useHashTab();
  const push = usePush();
  const dash = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard, refetchInterval: 1000 });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 2000 });
  const caps = useQuery({ queryKey: ["caps"], queryFn: api.capabilities });

  const d = dash.data?.data;
  const m = dash.data?.meta;
  const tabs = TABS.filter((t) => t !== "Test Controls" || caps.data?.data.test_plane);

  const blocked = m?.execution_blocked;
  const degraded = m ? m.degraded.filter((x) => x !== "database:local_sqlite") : [];
  const criticalAlert = !!d?.alerts.open.some((a) => a.severity === "critical");
  const count = (t: Tab) => (t === "Recommendations" && d ? d.recommendations.length : t === "Alerts" && d ? d.alerts.open.length : 0);
  const running = m?.sim_status === "RUNNING";

  const navItem = (t: Tab) => {
    const n = count(t);
    const on = tab === t;
    const badge = on ? "bg-sky-400 text-slate-950" : t === "Alerts" && criticalAlert ? "bg-rose-500/25 text-rose-100" : "bg-slate-800 text-slate-300";
    return (
      <button key={t} onClick={() => setTab(t)} aria-current={on ? "page" : undefined}
        className={`focus-ring flex cursor-pointer items-center justify-between gap-3 whitespace-nowrap rounded-lg px-3 py-2 text-left text-sm transition-colors duration-150 ${on ? "bg-slate-800 font-medium text-slate-50" : "text-slate-400 hover:bg-slate-800/50 hover:text-slate-100"}`}>
        <span>{t}</span>
        {n > 0 && <span className={`num rounded-full px-1.5 text-xs ${badge}`}>{n}</span>}
      </button>
    );
  };

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[13.5rem_minmax(0,1fr)]">
      <a href="#main" className="focus-ring sr-only rounded-lg bg-sky-400 px-3 py-2 text-sm font-medium text-slate-950 focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50">Skip to content</a>
      <aside className="border-b border-slate-800 bg-slate-900/50 lg:sticky lg:top-0 lg:h-screen lg:border-b-0 lg:border-r">
        <div className="flex items-center gap-2 px-4 py-3 lg:py-4">
          <svg viewBox="0 0 16 16" aria-hidden className="size-5 text-sky-400">
            <path fill="currentColor" d="M8 1.2c2.9 3.4 4.8 6.1 4.8 8.5a4.8 4.8 0 0 1-9.6 0c0-2.4 1.9-5.1 4.8-8.5z" />
            <path fill="none" stroke="var(--color-slate-950)" strokeWidth="1.4" strokeLinecap="round" d="M5.9 10.2a2.2 2.2 0 0 0 2.1 1.9" />
          </svg>
          <span className="font-semibold tracking-tight">FuelOps</span>
          <span className="rounded border border-slate-700 px-1.5 py-px text-[10px] font-medium tracking-wide text-slate-300">SIMULATED</span>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-2 pb-2 [scrollbar-width:none] lg:flex-col lg:px-3" aria-label="Views">
          {tabs.filter((t) => t !== "Test Controls").map(navItem)}
          {tabs.includes("Test Controls") && (
            <div className="flex gap-1 border-l border-slate-800 pl-1 lg:mt-4 lg:flex-col lg:border-l-0 lg:border-t lg:pl-0 lg:pt-3">
              <span className="hidden px-3 pb-1 text-xs text-slate-500 lg:block">Test plane</span>
              {navItem("Test Controls")}
            </div>
          )}
        </nav>
      </aside>

      <div className="min-w-0">
        <header className="sticky top-0 z-40 border-b border-slate-800 bg-slate-950">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 px-5 py-2.5">
            {m ? (
              <>
                <span className={`inline-flex items-center gap-1.5 text-xs font-semibold tracking-wide ${running ? "text-slate-200" : "text-amber-200"}`}>
                  <span aria-hidden className={`size-2 rounded-full ${running ? "animate-pulse bg-emerald-400" : "bg-amber-300"}`} />{m.sim_status}
                </span>
                <Readout label="Tick">{m.tick}</Readout>
                <Readout label="Sim time">{new Date(m.sim_time).toISOString().slice(5, 16).replace("T", " ")}</Readout>
                <span aria-hidden className="hidden h-4 w-px bg-slate-800 sm:block" />
                <Readout label="Data age" warn={m.age_s > 5 || m.stale}>{m.age_s.toFixed(1)} s</Readout>
                <Readout label="Stream" warn={d?.health.simulator.stream !== "connected"}>{d?.health.simulator.stream ?? "?"}</Readout>
                <Readout label="UI" warn={push !== "live"}>{push}</Readout>
                <Readout label="Epoch">{m.epoch}</Readout>
              </>
            ) : <span className="text-sm text-slate-400">Waiting for state…</span>}
            <span className="ml-auto">{status.data ? <StatusPill status={status.data.data.overall}>platform {status.data.data.overall}</StatusPill> : null}</span>
          </div>
          {blocked && <Banner tone="warn"><b className="font-semibold">Execution blocked</b> · {blocked}. Showing last known state; approvals disabled.</Banner>}
          {!blocked && m?.stale && <Banner tone="warn"><b className="font-semibold">Stale data</b> · last good state is {m.age_s.toFixed(0)} s old. Figures below may not reflect the simulator right now.</Banner>}
          {degraded.length > 0 && !blocked && <Banner tone="warn">Degraded: {degraded.join(", ")}</Banner>}
          {dash.isError && <Banner tone="bad"><b className="font-semibold">API unreachable</b> · {String((dash.error as Error).message)}. Retrying…</Banner>}
        </header>
        <main id="main" className="mx-auto max-w-[1600px] p-4 sm:p-5">
          <div className="mb-4">
            <h1 className="text-lg font-semibold tracking-tight">{tab}</h1>
            <p className="mt-0.5 text-sm text-slate-400">{SUBTITLE[tab]}</p>
          </div>
          {!d ? (
            dash.isError ? <Notice tone="bad" title="No state available yet">Is the worker running? This page retries every second.</Notice> : <Skeleton rows={6} />
          ) : (
            <>
              {tab === "Command Center" && <CommandCenter d={d} />}
              {tab === "Network" && <NetworkView d={d} />}
              {tab === "Recommendations" && <RecommendationsView recs={d.recommendations} blocked={d.execution_blocked} />}
              {tab === "Allocations" && <AllocationsView />}
              {tab === "Alerts" && <AlertsView d={d} />}
              {tab === "Decisions" && <DecisionsView />}
              {tab === "System Health" && <HealthView d={d} />}
              {tab === "Test Controls" && <TestControls />}
            </>
          )}
        </main>
      </div>
    </div>
  );
}

function Banner({ tone, children }: { tone: "warn" | "bad"; children: ReactNode }) {
  const box = tone === "bad" ? "border-rose-500/50 bg-rose-500/15 text-rose-100" : "border-amber-400/40 bg-amber-400/10 text-amber-100";
  return (
    <div role="alert" className={`flex items-start gap-2 border-t px-5 py-2 text-sm ${box}`}>
      <Glyph shape={tone === "bad" ? "octagon" : "triangle"} className={`mt-1 ${tone === "bad" ? "text-rose-400" : "text-amber-300"}`} />
      <div>{children}</div>
    </div>
  );
}
