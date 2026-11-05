"use client";

import { useState } from "react";
import {
  askAssistant, createExperiment, decideExperiment, getMetrics, runDemo,
  type AskResponse, type DecisionResponse, type Experiment, type Metrics,
} from "./api";
import MrrChart from "./MrrChart";

const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

export default function Home() {
  const [goal, setGoal] = useState("Raise annual-plan uptake without hurting 60-day retention.");
  const [price, setPrice] = useState(10);
  const [numArms, setNumArms] = useState(3);
  const [exp, setExp] = useState<Experiment | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [decision, setDecision] = useState<DecisionResponse | null>(null);
  const [question, setQuestion] = useState("show arms with churn above baseline");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function run<T>(fn: () => Promise<T>) {
    setBusy(true); setError("");
    try { return await fn(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  async function onGenerate() {
    const res = await run(() => createExperiment(goal, price, numArms));
    if (res) { setExp(res); setMetrics(null); setDecision(null); setAnswer(null); }
  }

  async function onDemo(scenario: "winner" | "guardrail") {
    if (!exp) return;
    const m = await run(async () => { await runDemo(exp.id, scenario); return getMetrics(exp.id); });
    if (m) { setMetrics(m); setDecision(null); setAnswer(null); }
  }

  async function onDecide() {
    if (!exp) return;
    const d = await run(() => decideExperiment(exp.id));
    if (!d) return;
    setDecision(d);
    const m = await run(() => getMetrics(exp.id));
    if (m) setMetrics(m);
  }

  async function onAsk() {
    if (!exp) return;
    const a = await run(() => askAssistant(exp.id, question));
    if (a) setAnswer(a);
  }

  const rows = answer?.rows ?? metrics?.rows ?? [];

  return (
    <main>
      <h1>PlanLab</h1>
      <p className="sub">Describe a goal. The agent designs pricing arms, validates them for PayPal, and decides.</p>

      <section className="card">
        <h2>1. Goal</h2>
        <textarea value={goal} onChange={(e) => setGoal(e.target.value)} />
        <div className="row">
          <label>Current monthly price ($)
            <input type="number" min={1} value={price} onChange={(e) => setPrice(+e.target.value)} />
          </label>
          <label>Arms
            <input type="number" min={2} max={4} value={numArms} onChange={(e) => setNumArms(+e.target.value)} />
          </label>
          <button disabled={busy || goal.length < 3} onClick={onGenerate}>Design experiment</button>
        </div>
        {error && <p className="err">{error}</p>}
      </section>

      {exp && (
        <section className="card">
          <h2>2. Proposed arms <span className="sub">({exp.source === "gemini" ? "Gemini" : "offline fallback"})</span></h2>
          <table>
            <thead><tr><th>Arm</th><th>Price</th><th>Trial</th><th>Why</th><th>PayPal check</th></tr></thead>
            <tbody>
              {exp.arms.map((a) => {
                const v = exp.validation.find((x) => x.arm_id === a.arm_id);
                return (
                  <tr key={a.arm_id}>
                    <td>{a.name}{a.is_baseline && " (baseline)"}</td>
                    <td>{a.currency} {a.price}/{a.interval === "YEAR" ? "yr" : "mo"}</td>
                    <td>{a.trial_days ? `${a.trial_days}d` : "-"}</td>
                    <td>{a.reason}</td>
                    <td><span className={`badge ${v?.valid ? "ok" : "bad"}`}>{v?.valid ? "valid" : "invalid"}</span>
                      {v?.errors.map((e) => <div key={e} className="err">{e}</div>)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="row">
            <button disabled={busy} onClick={() => onDemo("winner")}>Run demo: winner</button>
            <button disabled={busy} onClick={() => onDemo("guardrail")}>Run demo: guardrail</button>
          </div>
        </section>
      )}

      {metrics && (
        <section className="card">
          <h2>3. Live results <span className="sub">(60 simulated days; events use the shared event format)</span></h2>
          <table>
            <thead><tr><th>Arm</th><th>Visitors</th><th>Sign-ups</th><th>Conv.</th><th>Churn</th><th>MRR</th><th>LTV</th><th>Z</th><th>Status</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.arm_id}>
                  <td>{r.name}{r.is_baseline && " (baseline)"}</td><td>{r.visitors}</td><td>{r.signups}</td>
                  <td>{pct(r.conversion)}</td><td>{pct(r.churn)}</td><td>${r.mrr.toFixed(0)}</td>
                  <td>${r.ltv.toFixed(2)}</td><td>{r.z ?? "-"}</td>
                  <td><span className={`badge ${r.status === "stopped" ? "bad" : r.status === "winner" ? "ok" : ""}`}>{r.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
          <MrrChart series={metrics.mrr_series} arms={metrics.rows.map((r) => r.arm_id)} />
          <div className="row">
            <button disabled={busy} onClick={onDecide}>Run analysis</button>
            <input style={{ flex: 1, minWidth: 240 }} value={question} onChange={(e) => setQuestion(e.target.value)} />
            <button disabled={busy} onClick={onAsk}>Ask assistant</button>
          </div>
          {answer && <p>{answer.answer}</p>}
        </section>
      )}

      {decision && (
        <section className={`card decision ${decision.action}`}>
          <h2>{{ stop_arm: "Guardrail fired", winner: "Winner chosen", keep_testing: "Keep testing" }[decision.action]}
            {decision.arm_id && `: ${decision.arm_id}`}</h2>
          <p>{decision.explanation}</p>
        </section>
      )}
    </main>
  );
}
