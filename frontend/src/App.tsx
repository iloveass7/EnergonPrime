import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "./api";
import { StatusPill } from "./ui";
import { RecommendationsView } from "./views/Recommendations";
import { AlertsView, AllocationsView, CommandCenter, DecisionsView, HealthView, NetworkView, TestControls } from "./views/Views";

const TABS = ["Command Center", "Network", "Recommendations", "Allocations", "Alerts", "Decisions", "System Health", "Test Controls"] as const;
type Tab = (typeof TABS)[number];

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

export default function App() {
  const [tab, setTab] = useState<Tab>(() => (localStorage.getItem("tab") as Tab) || "Command Center");
  const push = usePush();
  const dash = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard, refetchInterval: 1000 });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 2000 });
  const caps = useQuery({ queryKey: ["caps"], queryFn: api.capabilities });
  useEffect(() => { try { localStorage.setItem("tab", tab); } catch { /* ignore */ } }, [tab]);

  const d = dash.data?.data;
  const m = dash.data?.meta;
  const tabs = TABS.filter((t) => t !== "Test Controls" || caps.data?.data.test_plane);

  const blocked = m?.execution_blocked;
  const degraded = m ? m.degraded.filter((x) => x !== "database:local_sqlite") : [];
  const count = (t: Tab) => (t === "Recommendations" && d ? d.recommendations.length : t === "Alerts" && d ? d.alerts.open.length : 0);

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[13.5rem_1fr]">
      <aside className="border-b border-slate-800 bg-slate-900/60 lg:sticky lg:top-0 lg:h-screen lg:border-b-0 lg:border-r">
        <div className="flex items-center gap-2 px-4 py-3 lg:py-4">
          <span aria-hidden className="grid size-7 place-items-center rounded-lg bg-sky-400 text-sm font-bold text-slate-950">F</span>
          <span className="font-semibold tracking-tight">FuelOps</span>
          <span className="rounded-md border border-slate-700 px-1.5 py-px text-[10px] font-medium text-slate-300">SIMULATED</span>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-2 pb-2 lg:flex-col lg:px-3" aria-label="Views">
          {tabs.map((t) => {
            const n = count(t);
            const on = tab === t;
            return (
              <button key={t} onClick={() => setTab(t)} aria-current={on ? "page" : undefined}
                className={`flex items-center justify-between gap-3 whitespace-nowrap rounded-lg px-3 py-2 text-left text-sm transition-colors duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 ${on ? "bg-slate-800 font-medium text-slate-50" : "text-slate-400 hover:bg-slate-800/50 hover:text-slate-200"} ${t === "Test Controls" ? "lg:mt-4" : ""}`}>
                <span className={t === "Test Controls" && !on ? "text-rose-300/80" : ""}>{t}</span>
                {n > 0 && <span className={`num rounded-full px-1.5 text-xs ${on ? "bg-sky-400 text-slate-950" : "bg-slate-800 text-slate-300"}`}>{n}</span>}
              </button>
            );
          })}
        </nav>
      </aside>

      <div className="min-w-0">
        <header className="sticky top-0 z-40 border-b border-slate-800 bg-slate-950/90 backdrop-blur">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-5 py-2.5 text-sm">
            {m ? (
              <>
                <span className="num text-slate-100">tick <b className="font-medium">{m.tick}</b></span>
                <span className="num text-slate-400">{new Date(m.sim_time).toISOString().slice(5, 16).replace("T", " ")} sim</span>
                <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${m.sim_status === "RUNNING" ? "text-emerald-300" : "text-amber-200"}`}>
                  <span aria-hidden className={`size-1.5 rounded-full ${m.sim_status === "RUNNING" ? "bg-emerald-400 animate-pulse" : "bg-amber-300"}`} />{m.sim_status}
                </span>
                <span aria-hidden className="h-4 w-px bg-slate-800" />
                <span className="num text-xs text-slate-400">epoch {m.epoch}</span>
                <span className={`num text-xs ${m.age_s > 5 ? "text-amber-200" : "text-slate-400"}`}>data age {m.age_s.toFixed(1)} s</span>
                <span className="text-xs text-slate-400">stream {d?.health.simulator.stream ?? "?"} · UI {push}</span>
              </>
            ) : <span className="text-slate-400">Waiting for state…</span>}
            <span className="ml-auto">{status.data ? <StatusPill status={status.data.data.overall}>platform {status.data.data.overall}</StatusPill> : null}</span>
          </div>
          {blocked && <div role="alert" className="border-t border-amber-400/40 bg-amber-400/10 px-5 py-2 text-sm text-amber-100"><b className="font-semibold">Execution blocked</b> · {blocked}. Showing last known state; approvals disabled.</div>}
          {degraded.length > 0 && !blocked && <div className="border-t border-amber-400/30 bg-amber-400/5 px-5 py-1.5 text-xs text-amber-200">Degraded: {degraded.join(", ")}</div>}
          {dash.isError && <div role="alert" className="border-t border-rose-500/50 bg-rose-600/15 px-5 py-2 text-sm text-rose-100"><b className="font-semibold">API unreachable</b> · {String((dash.error as Error).message)}. Retrying…</div>}
        </header>
        <main className="mx-auto max-w-[1600px] p-5">
        {!d ? <div className="card text-slate-400">{dash.isError ? "No state available yet: is the worker running?" : "Loading operational state…"}</div> : (
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
