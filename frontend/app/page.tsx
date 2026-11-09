"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useState } from "react";
import {
  approveRollout, askAssistant, createExperiment, decideExperiment, getGuardrails, getMetrics, getTimeline,
  health, kernelCheckout, kernelJob, listExperiments, proposeRollout, publishPlans, resetDemo, runDemo,
  type AskResponse, type CheckoutJob, type DecisionResponse, type Experiment, type GuardrailArm,
  type Metrics, type Rollout, type Timeline as TimelineData,
} from "./api";
import { pct } from "./grid";
import MrrChart from "./MrrChart";
import SavePanel from "./SavePanel";

// AG Grid and Bryntum touch the DOM, so they render on the client only.
const ArmsGrid = dynamic(() => import("./ArmsGrid"), { ssr: false });
const Timeline = dynamic(() => import("./Timeline"), { ssr: false });
const InsightsPanel = dynamic(() => import("./InsightsPanel"), { ssr: false });

const ROLLOUT_LABEL: Record<string, string> = {
  baseline: "baseline", in_place: "price can change in place", new_signups_only: "new sign-ups only",
};

export default function Home() {
  const [goal, setGoal] = useState("Raise annual-plan uptake without hurting 60-day retention.");
  const [price, setPrice] = useState(15);
  const [numArms, setNumArms] = useState(3);
  const [traffic, setTraffic] = useState(12000);
  const [expId, setExpId] = useState<string | null>(null);
  const [created, setCreated] = useState<Experiment | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [timeline, setTimeline] = useState<TimelineData | null>(null);
  const [decision, setDecision] = useState<DecisionResponse | null>(null);
  const [question, setQuestion] = useState("show arms with churn above baseline");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [guards, setGuards] = useState<GuardrailArm[]>([]);
  const [rollout, setRollout] = useState<Rollout | null>(null);
  const [job, setJob] = useState<CheckoutJob | null>(null);
  const [live, setLive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function run<T>(fn: () => Promise<T>) {
    setBusy(true); setError("");
    try { return await fn(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  const load = useCallback(async (id: string) => {
    const [m, g, t] = await Promise.all([getMetrics(id), getGuardrails(id), getTimeline(id)]);
    setMetrics(m); setGuards(g.arms); setTimeline(t);
  }, []);

  // Open the most recent experiment on first load, so the workspace is never empty.
  useEffect(() => {
    (async () => {
      try {
        setLive((await health()).save_agent_live);
        const list = await listExperiments();
        const latest = list[list.length - 1];
        if (latest) { setExpId(latest.id); setGoal(latest.goal); await load(latest.id); }
      } catch (e) { setError(`Backend not reachable: ${(e as Error).message}`); }
    })();
  }, [load]);

  // Poll a running KERNEL checkout job and refresh the grid as real sign-ups land.
  useEffect(() => {
    if (!job || job.status !== "running" || !expId) return;
    const t = setInterval(async () => {
      const j = await kernelJob(expId, job.id);
      setJob(j);
      await load(expId);
    }, 5000);
    return () => clearInterval(t);
  }, [job, expId, load]);

  async function onGenerate() {
    const res = await run(() => createExperiment(goal, price, numArms, traffic));
    if (res) {
      setCreated(res); setExpId(res.id); setDecision(null); setAnswer(null); setRollout(null); setJob(null);
      await load(res.id);
    }
  }
  const after = async () => { if (expId) await load(expId); };
  const onDemo = (s: "winner" | "guardrail") => run(async () => {
    await runDemo(expId!, s); setDecision(null); setAnswer(null); setRollout(null); await after();
  });
  const onDecide = () => run(async () => { setDecision(await decideExperiment(expId!)); await after(); });
  const onAsk = () => run(async () => setAnswer(await askAssistant(expId!, question)));
  const onRollout = () => run(async () => {
    setRollout(await (rollout?.status === "proposed" ? approveRollout(expId!) : proposeRollout(expId!))); await after();
  });
  const onPublish = () => run(async () => { await publishPlans(expId!); await after(); });
  const onKernel = () => run(async () => setJob(await kernelCheckout(expId!, 2)));
  const onReset = () => run(async () => {
    const r = await resetDemo(); setExpId(r.id); setCreated(null); setDecision(null); setRollout(null);
    setAnswer(null); setJob(null); await load(r.id);
  });

  const rows = metrics?.rows ?? [];
  const published = rows.length > 0 && rows.every((r) => r.plan_id);
  const ready = metrics?.readiness;
  const rejected = created?.rejected ?? metrics?.rejected_arms ?? [];

  return (
    <main>
      <header className="top">
        <div>
          <h1>PlanLab</h1>
          <p className="sub">AI copilot for PayPal subscription merchants: keep customers who try to cancel, and once you
            have the traffic, safely test which price earns more. All on real PayPal plans.</p>
        </div>
        {metrics && (
          <div className="sources">
            <span className="badge real">Real PayPal sandbox · {metrics.events.real} events</span>
            <span className="badge sim">Simulated time · {metrics.events.simulated} events</span>
          </div>
        )}
      </header>
      {error && <p className="err">{error}</p>}

      <SavePanel experimentId={expId} live={live} onChange={after} />

      <section className="card">
        <h2>Pricing experiment</h2>
        <textarea value={goal} onChange={(e) => setGoal(e.target.value)} />
        <div className="row">
          <label>Monthly price ($)<input type="number" min={1} value={price} onChange={(e) => setPrice(+e.target.value)} /></label>
          <label>Arms<input type="number" min={2} max={4} value={numArms} onChange={(e) => setNumArms(+e.target.value)} /></label>
          <label>Checkout visitors / month<input type="number" min={1} value={traffic} onChange={(e) => setTraffic(+e.target.value)} /></label>
          <button disabled={busy || goal.length < 3} onClick={onGenerate}>Design experiment</button>
          <button className="ghost" disabled={busy} onClick={onReset}>Reset demo merchant</button>
        </div>

        {ready && (
          <div className={`callout ${ready.feasible === false ? "warn" : ""}`}>
            <b>{ready.feasible === false ? "Not enough traffic yet." : "Sample size."}</b> {ready.message}
            <div className="bar"><span style={{ width: `${Math.round(ready.progress * 100)}%` }} /></div>
          </div>
        )}

        {created && (
          <>
            <h3>Arms the agent proposed <span className="sub">({created.source === "gemini" ? "Gemini" : "offline fallback"}, checked against PayPal&apos;s rules)</span></h3>
            <table>
              <thead><tr><th>Arm</th><th>Price</th><th>Trial</th><th>Why</th><th>If it wins</th></tr></thead>
              <tbody>
                {created.arms.map((a) => {
                  const v = created.validation.find((x) => x.arm_id === a.arm_id);
                  return (
                    <tr key={a.arm_id}>
                      <td>{a.name}{a.is_baseline && " (baseline)"}</td>
                      <td>{a.currency} {a.price}/{a.interval === "YEAR" ? "yr" : "mo"}</td>
                      <td>{a.trial_days ? `${a.trial_days}d` : "–"}</td>
                      <td>{a.reason}</td>
                      <td><span className={`badge ${v?.rollout === "new_signups_only" ? "sim" : "ok"}`}>{ROLLOUT_LABEL[v?.rollout ?? ""] ?? "–"}</span>
                        {v?.warnings.map((w) => <div key={w} className="sub small">{w}</div>)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </>
        )}
        {rejected.length > 0 && (
          <div className="callout warn">
            <b>Rejected by PlanLab before reaching PayPal:</b>
            {rejected.map((r) => <div key={r.arm.arm_id}>{r.arm.name} ({r.arm.price}/{r.arm.interval === "YEAR" ? "yr" : "mo"}): {r.errors.join("; ")}</div>)}
          </div>
        )}

        {expId && (
          <div className="row">
            <button disabled={busy || published} onClick={onPublish}>{published ? "Plans live in PayPal ✓" : "Publish arms to PayPal"}</button>
            <button disabled={busy || !published || job?.status === "running"} onClick={onKernel}>
              {job?.status === "running" ? "KERNEL buyers checking out…" : "2 real sign-ups (KERNEL buyers)"}
            </button>
            <button className="ghost" disabled={busy} onClick={() => onDemo("winner")}>Replay 60 days: winner</button>
            <button className="ghost" disabled={busy} onClick={() => onDemo("guardrail")}>Replay 60 days: risky arm</button>
          </div>
        )}
        {job && (
          <p className="sub small">KERNEL job {job.status}: {job.results.map((r) => r.error ? `error (${r.error.slice(0, 60)})`
            : `${r.subscription_id} → ${r.arm_id} ${r.status}`).join(" · ") || "starting cloud browser…"}</p>
        )}
      </section>

      {metrics && rows.length > 0 && (
        <section className="card">
          <h2>Results <span className="sub">decided on new monthly revenue per visitor, observable at checkout</span></h2>
          <ArmsGrid rows={rows} answer={answer} />
          <div className="row">
            <button disabled={busy} onClick={onDecide}>Run analysis</button>
            <input style={{ flex: 1, minWidth: 240 }} value={question} onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onAsk()} />
            <button className="ghost" disabled={busy} onClick={onAsk}>Ask the grid</button>
            {answer && <button className="ghost" onClick={() => setAnswer(null)}>Clear filter</button>}
          </div>
          {answer && <p className="sub">{answer.answer}</p>}
          <MrrChart series={metrics.mrr_series} arms={rows.map((r) => r.arm_id)} />
        </section>
      )}

      {decision && (
        <section className={`card decision ${decision.action}`}>
          <h2>{{ stop_arm: "Guardrail fired", winner: "Winner chosen", keep_testing: "Keep testing" }[decision.action]}
            {decision.arm_id && `: ${decision.arm_id}`}</h2>
          <p>{decision.explanation}</p>
          {decision.action === "winner" && (
            <div className="row">
              <button disabled={busy || rollout?.status === "approved"} onClick={onRollout}>
                {rollout?.status === "approved" ? "Rolled out" : rollout ? "Approve rollout" : "Propose rollout"}
              </button>
              {rollout && <span className="sub">{rollout.grandfathered
                ? "New sign-ups get the winner; existing subscribers keep their price."
                : "Lower price applied to the existing plan, so current subscribers pay less too."} {rollout.requests.length} PayPal request(s) {rollout.status}.</span>}
            </div>
          )}
        </section>
      )}

      {guards.length > 0 && (
        <section className="card">
          <h2>Guardrails <span className="sub">the agent can&apos;t cross these: a breach pauses the arm</span></h2>
          <table>
            <thead><tr><th>Arm</th><th>Retention</th><th>Refund rate</th><th>Dispute rate</th><th>Status</th></tr></thead>
            <tbody>
              {guards.map((g) => (
                <tr key={g.arm_id}>
                  <td>{g.arm_id}</td><td>{pct(g.retention)}</td><td>{pct(g.refund_rate)}</td><td>{pct(g.dispute_rate)}</td>
                  <td><span className={`badge ${g.ok ? "ok" : "bad"}`}>{g.ok ? "ok" : "breached"}</span>
                    {g.breaches.map((b) => <div key={b} className="err small">{b}</div>)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {timeline && (
        <section className="card">
          <h2>Experiment timeline <span className="sub">guardrail checkpoints every 14 days</span></h2>
          <Timeline key={JSON.stringify(timeline)} data={timeline} />
        </section>
      )}

      <InsightsPanel experimentId={expId} />
    </main>
  );
}
