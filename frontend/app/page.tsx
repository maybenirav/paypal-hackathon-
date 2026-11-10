"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useState } from "react";
import {
  LLM_LABEL, approveRollout, askAssistant, createExperiment, decideExperiment, getGuardrails, getMetrics, getTimeline,
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

const NAV = [
  { id: "top", label: "Home", icon: "★" },
  { id: "save", label: "Save Agent", icon: "♥" },
  { id: "experiment", label: "Experiment", icon: "▦" },
  { id: "results", label: "Results", icon: "◆" },
  { id: "history", label: "History", icon: "◷" },
];
const API_DOCS = `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/docs`;

// The two little hand-drawn marks from the theme: a target and a flower.
const Target = () => (
  <svg width="30" height="30" viewBox="0 0 30 30" aria-hidden><circle cx="15" cy="15" r="13" fill="#1fae5b" stroke="#151515" strokeWidth="2" />
    <circle cx="15" cy="15" r="7.5" fill="#fff" stroke="#151515" strokeWidth="2" /><circle cx="15" cy="15" r="3" fill="#151515" /></svg>
);
const Flower = () => (
  <svg width="30" height="30" viewBox="0 0 30 30" aria-hidden>
    {[0, 45, 90, 135, 180, 225, 270, 315].map((r) => (
      <ellipse key={r} cx="15" cy="7" rx="4" ry="6.5" fill="#ff4d94" stroke="#151515" strokeWidth="1.2" transform={`rotate(${r} 15 15)`} />
    ))}
    <circle cx="15" cy="15" r="4" fill="#f3c234" stroke="#151515" strokeWidth="1.2" /></svg>
);

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
  const [question, setQuestion] = useState("which arms earn more per visitor than baseline without more churn?");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [guards, setGuards] = useState<GuardrailArm[]>([]);
  const [rollout, setRollout] = useState<Rollout | null>(null);
  const [job, setJob] = useState<CheckoutJob | null>(null);
  const [live, setLive] = useState(false);
  const [llm, setLlm] = useState<string[]>([]);
  const [online, setOnline] = useState<boolean | null>(null);
  const [section, setSection] = useState("top");
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
        const h = await health();
        setLive(h.save_agent_live); setOnline(true);
        setLlm((h.llm ?? []).map((t) => `${LLM_LABEL[t.provider] ?? t.provider} · ${t.model}`));
        const list = await listExperiments();
        const latest = list[list.length - 1];
        if (latest) { setExpId(latest.id); setGoal(latest.goal); await load(latest.id); }
      } catch (e) { setOnline(false); setError(`Backend not reachable: ${(e as Error).message}`); }
    })();
  }, [load]);

  // Highlight the nav tab of the section in view.
  useEffect(() => {
    const els = NAV.map((n) => document.getElementById(n.id)).filter(Boolean) as HTMLElement[];
    const io = new IntersectionObserver((entries) => {
      const seen = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (seen[0]) setSection(seen[0].target.id);
    }, { rootMargin: "-45% 0px -50% 0px" });
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [metrics, timeline]);

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

  const best = rows.filter((r) => !r.is_baseline && r.prob_beats_baseline !== null)
    .sort((a, b) => (b.prob_beats_baseline ?? 0) - (a.prob_beats_baseline ?? 0))[0];

  return (
    <div className="sheet">
      <nav className="nav">
        <div className="nav-left">
          <span className="logo-dot">P</span>
          {NAV.map((n) => (
            <a key={n.id} href={`#${n.id}`} className={`tab-link ${section === n.id ? "active" : ""}`}>
              <span aria-hidden>{n.icon}</span><span className="txt">{n.label}</span>
            </a>
          ))}
        </div>
        <div className="nav-right">
          <span className={`status-dot ${online ? "green" : online === false ? "off" : "yellow"}`}
            title={online ? "API online" : online === false ? "API offline" : "Connecting…"}>{online ? "✓" : "…"}</span>
          <span className={`status-dot ${llm.length ? "yellow" : "off"}`}
            title={llm.length ? `AI models, in failover order:\n${llm.join("\n")}` : "No LLM key: deterministic fallbacks"}>AI</span>
          <span className={`status-dot ${live ? "pink" : "off"}`}
            title={live ? "Save Agent sends real calls to the PayPal sandbox" : "Save Agent dry run: requests are logged, not sent"}>$</span>
          <a className="nav-cta" href={API_DOCS} target="_blank" rel="noreferrer">♥ API docs</a>
        </div>
      </nav>

      <main>
        <section className="intro" id="top">
          <div className="polaroid left"><span className="tape" />
            <div className="shot yellow"><b>{metrics?.events.real ?? "–"}</b></div>
            <div className="cap">real PayPal events</div>
          </div>
          <div className="polaroid right"><span className="tape yellow" />
            <div className="shot blue"><b>{best?.prob_beats_baseline != null ? pct(best.prob_beats_baseline, 0) : "–"}</b></div>
            <div className="cap">{best ? `${best.arm_id} vs baseline` : "chance to win"}</div>
          </div>

          <span className="hand-note above">say hi to</span>
          <div className="name-box">
            <span className="label mint l1">Pricing agent</span>
            <span className="label peach l2">Save agent</span>
            <h1>PLANLAB</h1>
            <span className="label hand yellow l3">PayPal Subscriptions</span>
            <span className="label hand mint l4">AG Grid · Bryntum</span>
          </div>
          <div>
            <span className="status-line"><i className={live ? "live" : ""} />
              {live ? "Save Agent live on the PayPal sandbox" : "Save Agent in dry run · nothing is charged"}</span>
          </div>
          <p className="headline"><span className="keep">I keep your subscribers <Target /></span><br />and test which price <span className="keep">earns more. <Flower /></span></p>
          <a href="#experiment"><button>Start an experiment</button></a>
          {metrics && (
            <div className="sources">
              <span className="badge real">Real PayPal sandbox · {metrics.events.real} events</span>
              <span className="badge sim">Simulated time · {metrics.events.simulated} events</span>
            </div>
          )}
          {error && <p className="err">{error}</p>}
        </section>

        <svg className="divider" viewBox="0 0 1000 40" preserveAspectRatio="none" aria-hidden>
          <path d="M0 34 Q500 -8 1000 34" fill="none" stroke="#151515" strokeWidth="1.2" />
        </svg>

        <section className="about">
          <span className="hand-note corner-note">about me!</span>
          <span className="boxed">what&apos;s up</span>
          <p className="handwritten">I&apos;m a copilot for PayPal subscription merchants. When a customer clicks cancel, I work out
            why and make an offer within your rules. When you have the traffic, I run pricing experiments on real PayPal
            plans and only call a winner when it&apos;s 95% likely to earn more. ✍️</p>
          <div className="stickers">
            <span className="sticker yellow">Save Agent</span><span className="sticker sq yellow">♥</span>
            <span className="sticker green">Thompson sampling</span><span className="sticker sq green">%</span>
            <span className="sticker pink">Guardrails</span><span className="sticker sq pink">!</span>
            <span className="sticker blue">Real PayPal plans</span><span className="sticker sq blue">$</span>
          </div>
        </section>

        <section className="step" id="save">
          <div className="tabs"><span className="tab blue">Step 01</span><span className="tab black">Save Agent</span></div>
          <div className="block yellow">
            <span className="date">Works from the first customer</span>
            <h2 className="big">Keep the customer</h2>
            <p className="lede">A customer clicks cancel and says why in their own words. The agent understands the reason, picks an
              offer within your rules and makes the PayPal call. Override any decision by editing its Offer cell.</p>
            <SavePanel experimentId={expId} live={live} onChange={after} />
          </div>
        </section>

        <section className="step" id="experiment">
          <div className="tabs"><span className="tab black">Step 02</span><span className="tab pink">Pricing experiment</span></div>
          <div className="card">
            <h2>What&apos;s the goal? <span className="sub">in your own words</span></h2>
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
                <h3>Arms the agent proposed <span className="sub">({LLM_LABEL[created.source] ?? "offline fallback"}, checked against PayPal&apos;s rules)</span></h3>
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
                            {v?.warnings.map((w) => <div key={w} className="note small">{w}</div>)}</td>
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
              <p className="note small" style={{ marginTop: 10 }}>KERNEL job {job.status}: {job.results.map((r) => r.error ? `error (${r.error.slice(0, 60)})`
                : `${r.subscription_id} → ${r.arm_id} ${r.status}`).join(" · ") || "starting cloud browser…"}</p>
            )}
          </div>
        </section>

        {metrics && rows.length > 0 && (
          <section className="step" id="results">
            <div className="tabs"><span className="tab blue">Step 03</span><span className="tab black">Results</span><span className="tab yellow">Decide</span></div>
            <div className="block dark">
              <span className="date">Decided on new MRR per visitor, observable at checkout</span>
              <h2 className="big">Which price earns more?</h2>
              <p className="lede">Each arm is compared with the baseline pinned on top. Ask the grid in plain English and the AI sets the
                grid&apos;s own filters, so you can see and edit exactly what it did.</p>
              <div className="screen"><span className="tape" /><span className="tape yellow l" />
                <ArmsGrid rows={rows} answer={answer} guards={guards} onReset={() => setAnswer(null)} />
              </div>
              <div className="row">
                <button className="ghost" disabled={busy} onClick={onDecide}>Run analysis</button>
                <input style={{ flex: 1, minWidth: 240 }} value={question} onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && onAsk()} />
                <button className="ghost" disabled={busy} onClick={onAsk}>Ask the grid</button>
                {answer && <button className="ghost" onClick={() => setAnswer(null)}>Clear filter</button>}
              </div>
              {answer && <p className="sub" style={{ marginTop: 10 }}>{answer.answer}</p>}
              <div className="screen" style={{ marginTop: 18 }}><MrrChart series={metrics.mrr_series} arms={rows.map((r) => r.arm_id)} /></div>
            </div>

            {decision && (
              <div className={`decision-note ${decision.action}`}><span className="tape" />
                <h2>{{ stop_arm: "Guardrail fired!", winner: "We have a winner!", keep_testing: "Keep testing…" }[decision.action]}
                  {decision.arm_id && ` ${decision.arm_id}`}</h2>
                <p>{decision.explanation}</p>
                {decision.action === "winner" && (
                  <div className="row">
                    <button disabled={busy || rollout?.status === "approved"} onClick={onRollout}>
                      {rollout?.status === "approved" ? "Rolled out" : rollout ? "Approve rollout" : "Propose rollout"}
                    </button>
                    {rollout && <span className="note">{rollout.grandfathered
                      ? "New sign-ups get the winner; existing subscribers keep their price."
                      : "Lower price applied to the existing plan, so current subscribers pay less too."} {rollout.requests.length} PayPal request(s) {rollout.status}.</span>}
                  </div>
                )}
              </div>
            )}
          </section>
        )}

        {guards.length > 0 && (
          <section className="step">
            <div className="tabs"><span className="tab pink">Guardrails</span></div>
            <div className="card">
              <h2>Lines the agent can&apos;t cross <span className="sub">a breach pauses the arm</span></h2>
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
            </div>
          </section>
        )}

        {timeline && (
          <section className="step">
            <div className="tabs"><span className="tab green">Timeline</span></div>
            <div className="card">
              <h2>Experiment timeline <span className="sub">guardrail checkpoints every 14 days</span></h2>
              <Timeline key={JSON.stringify(timeline)} data={timeline} />
            </div>
          </section>
        )}

        <section className="step" id="history">
          <div className="tabs"><span className="tab yellow">Step 04</span><span className="tab black">History &amp; why</span></div>
          <InsightsPanel experimentId={expId} />
        </section>
      </main>

      <footer className="foot">
        <span>PlanLab · AI copilot for PayPal subscription merchants</span>
        <span>Real sign-ups · simulated time · every decision logged</span>
      </footer>
    </div>
  );
}
