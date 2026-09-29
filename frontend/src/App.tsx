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

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 border-b border-slate-800 bg-slate-950/95 backdrop-blur">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2 text-sm">
          <div className="font-semibold tracking-wide">FuelOps <span className="ml-1 rounded bg-slate-800 px-1.5 py-0.5 text-xs text-slate-300">SIMULATED</span></div>
          {m ? (
            <>
              <span className="num">tick <b>{m.tick}</b> · {new Date(m.sim_time).toISOString().slice(5, 16).replace("T", " ")} sim · {m.sim_status}</span>
              <span className="num">epoch {m.epoch}</span>
              <span className={`num ${m.age_s > 5 ? "text-amber-200" : "text-slate-300"}`}>data age {m.age_s.toFixed(1)} s</span>
              <span>stream: {d?.health.simulator.stream ?? "?"} · UI {push}</span>
            </>
          ) : <span className="text-slate-400">waiting for state…</span>}
          <span className="ml-auto">{status.data ? <StatusPill status={status.data.data.overall}>platform {status.data.data.overall}</StatusPill> : null}</span>
        </div>
        {m?.execution_blocked && <div role="alert" className="border-t border-amber-400/40 bg-amber-400/10 px-4 py-1.5 text-sm text-amber-200">⚠ Execution blocked — {m.execution_blocked}. Showing last known state; approvals disabled.</div>}
        {m && m.degraded.filter((x) => x !== "database:local_sqlite").length > 0 && !m.execution_blocked && (
          <div className="border-t border-amber-400/30 bg-amber-400/5 px-4 py-1 text-xs text-amber-200">Degraded: {m.degraded.join(", ")}</div>
        )}
        {dash.isError && <div role="alert" className="border-t border-rose-500/50 bg-rose-600/15 px-4 py-1.5 text-sm text-rose-200">API unreachable — {String((dash.error as Error).message)}. Retrying…</div>}
        <nav className="flex gap-1 overflow-x-auto px-3" aria-label="Views">
          {tabs.map((t) => (
            <button key={t} onClick={() => setTab(t)} aria-current={tab === t ? "page" : undefined}
              className={`whitespace-nowrap border-b-2 px-3 py-2 text-sm ${tab === t ? "border-sky-400 text-sky-200" : "border-transparent text-slate-400 hover:text-slate-200"} ${t === "Test Controls" ? "text-rose-300" : ""}`}>
              {t}{t === "Recommendations" && d ? ` (${d.recommendations.length})` : ""}{t === "Alerts" && d ? ` (${d.alerts.open.length})` : ""}
            </button>
          ))}
        </nav>
      </header>
      <main className="p-4">
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
  );
}
