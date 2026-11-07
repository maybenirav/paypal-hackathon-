"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import * as api from "./api";
import { ApiError, API_URL, type Experiment, type MemoryRecord, type Metrics, type Scenario } from "./api";
import ArmsTab from "./components/ArmsTab";
import AutoStops from "./components/AutoStops";
import Composer from "./components/Composer";
import ExperimentHeader from "./components/ExperimentHeader";
import GuardrailsTab from "./components/GuardrailsTab";
import PayPalTab from "./components/PayPalTab";
import Rail from "./components/Rail";
import ResultsTab from "./components/ResultsTab";
import Verdict from "./components/Verdict";
import { armText } from "./format";
import { Icon } from "./icons";

const SaveTab = dynamic(() => import("./components/SaveTab"), {
  ssr: false,
  loading: () => <p className="grid-loading" role="status">Loading…</p>,
});

type Tab = "results" | "arms" | "guardrails" | "save" | "paypal";
const TABS: { id: Tab; label: string }[] = [
  { id: "results", label: "Results" },
  { id: "arms", label: "Price options" },
  { id: "guardrails", label: "Guardrails" },
  { id: "save", label: "Save Agent" },
  { id: "paypal", label: "PayPal" },
];
const FRAME_MS = 380;
const STOP_PAUSE_MS = 1600;   // long enough to read that an option has just been stopped
const WEEK = 7;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export default function Workspace() {
  const [ready, setReady] = useState(false);
  const [apiDown, setApiDown] = useState(false);
  const [gemini, setGemini] = useState<boolean | null>(null);
  const [paypal, setPaypal] = useState<"connected" | "off" | "unknown">("unknown");
  const [list, setList] = useState<api.ExperimentSummary[]>([]);
  const [memory, setMemory] = useState<MemoryRecord[]>([]);

  const [exp, setExp] = useState<Experiment | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [metricsDay, setMetricsDay] = useState(0);
  const [day, setDay] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [refetching, setRefetching] = useState(false);
  const [tab, setTab] = useState<Tab>("arms");

  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: "error" | "ok"; text: string } | null>(null);

  const frames = useRef(new Map<string, Metrics>());
  const frameRequest = useRef(0);
  const openRequest = useRef(0);
  const verdictRef = useRef<HTMLDivElement>(null);

  const fail = useCallback((error: unknown) => {
    if (error instanceof ApiError && error.status === 0) {
      setApiDown(true);
      return;
    }
    setNotice({ kind: "error", text: error instanceof Error ? error.message : String(error) });
  }, []);

  const refreshSidebar = useCallback(async () => {
    const [experiments, mem] = await Promise.all([api.listExperiments(), api.getMemory()]);
    setList(experiments);
    setMemory(mem.records);
  }, []);

  const checkPayPal = useCallback(async () => {
    try {
      const status = await api.paypalHealth();
      setPaypal(status.paypal === "connected" ? "connected" : "off");
      return status;
    } catch {
      setPaypal("unknown");
      return null;
    }
  }, []);

  /** One metrics snapshot per timeline day. Frames are cached because each costs a Monte Carlo run. */
  const frame = useCallback(async (id: string, target: number, windowDays: number) => {
    const key = `${id}:${target}`;
    const cached = frames.current.get(key);
    if (cached) return cached;
    const fresh = await api.getMetrics(id, target >= windowDays ? undefined : target);
    frames.current.set(key, fresh);
    return fresh;
  }, []);

  const open = useCallback(async (id: string, nextTab?: Tab) => {
    frames.current.clear();
    setPlaying(false);
    const request = ++openRequest.current;
    const [detail, snapshot] = await Promise.all([api.getExperiment(id), api.getMetrics(id)]);
    if (request !== openRequest.current) return;   // the merchant has already clicked something else
    frames.current.set(`${id}:${snapshot.window_days}`, snapshot);
    setExp(detail);
    setMetrics(snapshot);
    setDay(snapshot.window_days);
    setMetricsDay(snapshot.window_days);
    setTab(nextTab ?? (detail.events.real + detail.events.simulated > 0 ? "results" : "arms"));
    setNotice(null);
    window.history.replaceState(null, "", `/?exp=${id}`);
  }, []);

  const startNew = useCallback(() => {
    openRequest.current += 1;
    setPlaying(false);
    setExp(null);
    setMetrics(null);
    setNotice(null);
    window.history.replaceState(null, "", "/");
  }, []);

  const boot = useCallback(async () => {
    try {
      const status = await api.health();
      setGemini(status.gemini);
      setApiDown(false);
      await refreshSidebar();
      void checkPayPal();
      const id = new URLSearchParams(window.location.search).get("exp");
      if (id) {
        try {
          await open(id);
        } catch (error) {
          if (!(error instanceof ApiError) || error.status !== 404) throw error;
          startNew();
          setNotice({ kind: "error", text: "That experiment doesn't exist any more." });
        }
      }
    } catch (error) {
      fail(error);
    } finally {
      setReady(true);
    }
  }, [checkPayPal, fail, open, refreshSidebar, startNew]);

  useEffect(() => { void boot(); }, [boot]);

  // While the API is unreachable, keep checking so the page recovers by itself.
  useEffect(() => {
    if (!apiDown) return;
    const timer = setInterval(() => { void boot(); }, 4000);
    return () => clearInterval(timer);
  }, [apiDown, boot]);

  // Dragging the timeline: wait for the pointer to settle, and ignore responses that arrive late.
  useEffect(() => {
    if (!exp || !metrics || playing || day === metricsDay) return;
    const timer = setTimeout(async () => {
      const request = ++frameRequest.current;
      setRefetching(true);
      try {
        const snapshot = await frame(exp.id, day, metrics.window_days);
        if (request === frameRequest.current) {
          setMetrics(snapshot);
          setMetricsDay(day);
        }
      } catch (error) {
        fail(error);
      } finally {
        if (request === frameRequest.current) setRefetching(false);
      }
    }, 120);
    return () => clearTimeout(timer);
  }, [day, exp, fail, frame, metrics, metricsDay, playing]);

  // Replay: step through the timeline a week at a time until the end, pausing wherever an option is stopped.
  useEffect(() => {
    if (!playing || !exp || !metrics) return;
    const id = exp.id;
    const windowDays = metrics.window_days;
    const stopDays = exp.stops.flatMap((stop) => (stop.day === null ? [] : [stop.day]));
    let cancelled = false;
    (async () => {
      let current = metricsDay >= windowDays ? 0 : metricsDay;
      try {
        while (!cancelled) {
          const snapshot = await frame(id, current, windowDays);
          if (cancelled) return;
          setMetrics(snapshot);
          setMetricsDay(current);
          setDay(current);
          if (current >= windowDays) break;
          await sleep(stopDays.includes(current) ? STOP_PAUSE_MS : FRAME_MS);
          const later = stopDays.filter((stopDay) => stopDay > current);
          current = Math.min(windowDays, (Math.floor(current / WEEK) + 1) * WEEK, ...later);
        }
      } catch (error) {
        fail(error);
      }
      if (!cancelled) setPlaying(false);
    })();
    return () => { cancelled = true; };
    // Only the play button starts or stops this loop; it reads the rest once at the start.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing]);

  const act = useCallback(async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setNotice(null);
    try {
      await action();
    } catch (error) {
      fail(error);
    } finally {
      setBusy(null);
    }
  }, [fail]);

  /** Reloads the open experiment after something changed it, keeping the timeline where it is. */
  const reload = useCallback(async (id: string) => {
    frames.current.clear();
    const [detail] = await Promise.all([api.getExperiment(id), refreshSidebar()]);
    const snapshot = await api.getMetrics(id);
    frames.current.set(`${id}:${snapshot.window_days}`, snapshot);
    setExp(detail);
    // Real data has no timeline to replay, and its window grows as new events arrive
    const target = detail.events.real > 0 ? snapshot.window_days : Math.min(day, snapshot.window_days);
    const shown = target >= snapshot.window_days ? snapshot : await frame(id, target, snapshot.window_days);
    setMetrics(shown);
    setDay(target);
    setMetricsDay(target);
  }, [day, frame, refreshSidebar]);

  // Real sign-ups and payments arrive by webhook at any time, so keep the numbers current.
  const hasRealEvents = (exp?.events.real ?? 0) > 0;
  const openId = exp?.id;
  useEffect(() => {
    if (!openId || !hasRealEvents || (tab !== "results" && tab !== "guardrails") || busy !== null) return;
    const timer = setInterval(() => {
      if (!document.hidden) reload(openId).catch(() => { /* the next tick tries again */ });
    }, 15_000);
    return () => clearInterval(timer);
  }, [busy, hasRealEvents, openId, reload, tab]);

  const design = (goal: string, price: number, arms: number) =>
    act("design", async () => {
      const created = await api.createExperiment(goal, price, arms);
      await refreshSidebar();
      await open(created.id, "arms");
    });

  const runDemo = (scenario: Scenario) => {
    if (!exp) return;
    const realEvents = exp.events.real;
    if (realEvents > 0 && !window.confirm(
      `This experiment has ${realEvents} real PayPal events. Running a simulation replaces them. Continue?`)) return;
    const id = exp.id;
    return act("demo", async () => {
      setPlaying(false);
      frames.current.clear();
      await api.runDemo(id, scenario, realEvents > 0);
      const animate = !reducedMotion();
      // Fetch everything first and apply it together, so the old day never flashes on screen.
      const [detail, snapshot] = await Promise.all([
        api.getExperiment(id), api.getMetrics(id, animate ? 0 : undefined), refreshSidebar(),
      ]);
      const startDay = animate ? 0 : snapshot.window_days;
      frames.current.set(`${id}:${startDay}`, snapshot);
      setExp(detail);
      setMetrics(snapshot);
      setDay(startDay);
      setMetricsDay(startDay);
      setTab("results");
      setPlaying(animate);
    });
  };

  const analyze = () => {
    if (!exp || !metrics) return;
    const id = exp.id;
    const simulated = exp.events.real === 0;
    const asOf = simulated && day < metrics.window_days ? day : undefined;
    return act("analyze", async () => {
      setPlaying(false);
      await api.decideExperiment(id, asOf);
      await reload(id);
      verdictRef.current?.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
    });
  };

  const rollout = (step: "propose" | "approve") => {
    if (!exp) return;
    const id = exp.id;
    return act("rollout", async () => {
      await (step === "propose" ? api.proposeRollout(id) : api.approveRollout(id));
      await reload(id);    // an approved rollout changes the traffic split, not just its own status
    });
  };

  const resume = (armId: string) => {
    if (!exp) return;
    const id = exp.id;
    return act("resume", async () => {
      setPlaying(false);
      await api.resumeArm(id, armId);
      await reload(id);
    });
  };

  const removeExperiment = () => {
    if (!exp) return;
    const id = exp.id;
    if (!window.confirm("Delete this experiment and its results? This can't be undone. PayPal plans it published stay in PayPal.")) return;
    return act("delete", async () => {
      await api.deleteExperiment(id);
      startNew();
      await refreshSidebar();
    });
  };

  function onTabKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const move = event.key === "ArrowRight" ? 1 : -1;
    const next = TABS[(TABS.findIndex((t) => t.id === tab) + move + TABS.length) % TABS.length];
    setTab(next.id);
    event.currentTarget.querySelector<HTMLButtonElement>(`[data-tab="${next.id}"]`)?.focus();
  }

  return (
    <div className="app">
      <Rail experiments={list} activeId={exp?.id ?? null} onSelect={(id) => act("open", () => open(id))}
        onNew={startNew} apiOk={!apiDown} paypal={paypal} gemini={gemini} />

      <main className="main">
        {notice && (
          <div className={`notice notice-${notice.kind}`} role="alert">
            <Icon name={notice.kind === "error" ? "alert" : "check"} />
            <span>{notice.text}</span>
            <button className="icon-btn" onClick={() => setNotice(null)} aria-label="Dismiss"><Icon name="close" /></button>
          </div>
        )}

        {apiDown ? (
          <section className="api-down">
            <h1>Can&rsquo;t reach the PlanLab API</h1>
            <p>The workspace is looking for it at <code>{API_URL}</code> and will reconnect by itself once it&rsquo;s up. Start it from the <code>backend</code> folder:</p>
            <pre>python -m uvicorn app.main:app --reload</pre>
            <button className="btn" onClick={() => void boot()}><Icon name="refresh" /> Try again</button>
          </section>
        ) : !ready ? (
          <p className="loading" role="status">Loading workspace…</p>
        ) : !exp || !metrics ? (
          <Composer busy={busy === "design"} gemini={gemini} memory={memory} onDesign={design} />
        ) : (
          <>
            <ExperimentHeader exp={exp} busy={busy !== null} onDelete={removeExperiment} />
            {tab === "results" && (
              <div ref={verdictRef}>
                <AutoStops exp={exp} stops={metrics.stops} day={metricsDay} busy={busy !== null} onResume={resume} />
                <Verdict exp={exp} busy={busy !== null} onAnalyze={analyze}
                  onPropose={() => rollout("propose")} onApprove={() => rollout("approve")} />
              </div>
            )}
            <div className="tabs" role="tablist" aria-label="Experiment sections" onKeyDown={onTabKey}>
              {TABS.map((t) => (
                <button key={t.id} id={`tab-${t.id}`} data-tab={t.id} role="tab" aria-selected={tab === t.id}
                  aria-controls="tab-panel" tabIndex={tab === t.id ? 0 : -1} onClick={() => setTab(t.id)}>
                  {t.label}
                </button>
              ))}
            </div>
            <div id="tab-panel" role="tabpanel" aria-labelledby={`tab-${tab}`} className="tab-panel">
              {tab === "results" && (
                <ResultsTab key={`${exp.id}-${exp.scenario}`} exp={exp} metrics={metrics} metricsDay={metricsDay} day={day}
                  playing={playing} refetching={refetching} busy={busy}
                  onScrub={(next) => { setPlaying(false); setDay(next); }}
                  onTogglePlay={() => setPlaying((p) => !p)} onAnalyze={analyze} onDemo={runDemo}
                  onError={fail} />
              )}
              {tab === "arms" && (
                <ArmsTab exp={exp} busy={busy !== null} onDemo={runDemo} onOpenPayPal={() => setTab("paypal")} />
              )}
              {tab === "guardrails" && (
                <GuardrailsTab expId={exp.id} guardrails={metrics.guardrails} rows={metrics.rows}
                  stoppedArms={metrics.stopped_arms} stops={metrics.stops} autoChecks={metrics.auto_checks}
                  simulated={exp.events.real === 0} rolledOut={exp.rollout?.status === "approved"} busy={busy !== null}
                  nameOfArm={(id) => exp.arms.find((a) => a.arm_id === id)?.name ?? id}
                  explain={(text) => armText(exp, text)} onResume={resume}
                  onSaved={() => reload(exp.id)} onError={fail} />
              )}
              {tab === "save" && <SaveTab key={exp.id} expId={exp.id} onError={fail} />}
              {tab === "paypal" && (
                <PayPalTab exp={exp} onCheckHealth={checkPayPal} onDataChanged={() => reload(exp.id)} onError={fail} />
              )}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
