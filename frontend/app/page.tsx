"use client";

import { useState } from "react";
import { decide, generateArms, type Arm, type ArmInput, type ArmsResponse, type DecisionResponse } from "./api";

const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
const monthly = (a: Arm) => (a.interval === "YEAR" ? a.price / 12 : a.price);

export default function Home() {
  const [goal, setGoal] = useState("Raise annual-plan uptake without hurting 60-day retention.");
  const [price, setPrice] = useState(10);
  const [numArms, setNumArms] = useState(3);
  const [arms, setArms] = useState<ArmsResponse | null>(null);
  const [stats, setStats] = useState<Record<string, ArmInput>>({});
  const [decision, setDecision] = useState<DecisionResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function run<T>(fn: () => Promise<T>) {
    setBusy(true); setError("");
    try { return await fn(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  async function onGenerate() {
    const res = await run(() => generateArms(goal, price, numArms));
    if (!res) return;
    setArms(res); setDecision(null);
    const s: Record<string, ArmInput> = {};
    res.arms.forEach((a) => {
      s[a.arm_id] = { arm_id: a.arm_id, price: +monthly(a).toFixed(2), visitors: 1000, signups: 100, churned: 10 };
    });
    setStats(s);
  }

  async function onDecide() {
    if (!arms) return;
    const base = arms.arms.find((a) => a.is_baseline) ?? arms.arms[0];
    const others = arms.arms.filter((a) => a.arm_id !== base.arm_id).map((a) => stats[a.arm_id]);
    const res = await run(() => decide(stats[base.arm_id], others, goal));
    if (res) setDecision(res);
  }

  const setStat = (id: string, k: keyof ArmInput, v: number) =>
    setStats((s) => ({ ...s, [id]: { ...s[id], [k]: v } }));

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

      {arms && (
        <section className="card">
          <h2>2. Proposed arms <span className="sub">({arms.source === "gemini" ? "Gemini" : "offline fallback"})</span></h2>
          <table>
            <thead><tr><th>Arm</th><th>Price</th><th>Trial</th><th>Why</th><th>PayPal check</th></tr></thead>
            <tbody>
              {arms.arms.map((a) => {
                const v = arms.validation.find((x) => x.arm_id === a.arm_id);
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
        </section>
      )}

      {arms && (
        <section className="card">
          <h2>3. Results so far <span className="sub">(sample numbers, edit them; the live API feeds this later)</span></h2>
          <table className="num">
            <thead><tr><th>Arm</th><th>Visitors</th><th>Sign-ups</th><th>Churned</th></tr></thead>
            <tbody>
              {arms.arms.map((a) => (
                <tr key={a.arm_id}>
                  <td>{a.name}</td>
                  {(["visitors", "signups", "churned"] as const).map((k) => (
                    <td key={k}><input type="number" min={0} value={stats[a.arm_id]?.[k] ?? 0}
                      onChange={(e) => setStat(a.arm_id, k, +e.target.value)} /></td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="row"><button disabled={busy} onClick={onDecide}>Run analysis</button></div>
        </section>
      )}

      {decision && (
        <section className={`card decision ${decision.action}`}>
          <h2>{{ stop_arm: "Guardrail fired", winner: "Winner chosen", keep_testing: "Keep testing" }[decision.action]}
            {decision.arm_id && `: ${decision.arm_id}`}</h2>
          <p>{decision.explanation}</p>
          <table>
            <thead><tr><th>Arm</th><th>Conversion</th><th>Churn</th><th>12-mo LTV</th><th>Z</th><th>Churn ratio</th></tr></thead>
            <tbody>
              {decision.arms.map((a) => (
                <tr key={a.arm_id}>
                  <td>{a.arm_id}</td><td>{pct(a.conversion)}</td><td>{pct(a.churn)}</td>
                  <td>${a.ltv.toFixed(2)}</td>
                  <td>{a.ztest ? a.ztest.z.toFixed(2) : "-"}</td>
                  <td>{a.guardrail ? (a.guardrail.ratio == null ? "inf" : a.guardrail.ratio.toFixed(2) + "x") : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </main>
  );
}
