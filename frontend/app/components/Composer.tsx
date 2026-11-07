"use client";

import { useState, type FormEvent } from "react";
import type { MemoryRecord } from "../api";
import { Icon } from "../icons";

const EXAMPLES = [
  "Raise annual-plan uptake without hurting 60-day retention.",
  "Increase trial sign-ups without raising refunds.",
  "Find out if we can charge 30% more without losing subscribers.",
];

type Props = {
  busy: boolean;
  gemini: boolean | null;
  memory: MemoryRecord[];
  onDesign: (goal: string, price: number, arms: number) => void;
};

export default function Composer({ busy, gemini, memory, onDesign }: Props) {
  const [goal, setGoal] = useState(EXAMPLES[0]);
  const [price, setPrice] = useState("10");
  const [arms, setArms] = useState(3);

  const priceValue = Number(price);
  const priceOk = Number.isFinite(priceValue) && priceValue > 0;
  const goalOk = goal.trim().length >= 3;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (goalOk && priceOk && !busy) onDesign(goal.trim(), priceValue, arms);
  }

  return (
    <div className="composer">
      <form className="composer-form" onSubmit={submit}>
        <h1>Design a pricing experiment</h1>
        <p className="lede">Describe the outcome you want. PlanLab proposes the prices to test, checks them against PayPal&rsquo;s plan format, and watches the guardrails while the test runs.</p>

        <label className="field">
          <span>What should this test achieve?</span>
          <textarea className="input" rows={3} value={goal} onChange={(e) => setGoal(e.target.value)} />
        </label>
        <div className="chips" aria-label="Example goals">
          {EXAMPLES.map((example) => (
            <button type="button" key={example} className="chip" aria-pressed={goal === example}
              onClick={() => setGoal(example)}>{example}</button>
          ))}
        </div>

        <div className="composer-row">
          <label className="field">
            <span>Current monthly price (USD)</span>
            <input className="input mono" type="number" min="0.01" step="any" inputMode="decimal"
              value={price} onChange={(e) => setPrice(e.target.value)} aria-invalid={!priceOk} />
          </label>
          <fieldset className="field">
            <legend>Price options to test</legend>
            <div className="segmented">
              {[2, 3, 4].map((n) => (
                <label key={n} className={arms === n ? "on" : ""}>
                  <input type="radio" name="arms" checked={arms === n} onChange={() => setArms(n)} />{n}
                </label>
              ))}
            </div>
          </fieldset>
          <button className="btn primary large" disabled={busy || !goalOk || !priceOk}>
            {busy ? "Designing…" : <>Design experiment <Icon name="send" /></>}
          </button>
        </div>
        {!priceOk && <p className="field-error" role="alert">Enter a price above zero.</p>}
        {!goalOk && <p className="field-error" role="alert">Describe the goal in a few words.</p>}
      </form>

      <aside className="composer-side">
        <h2>How options get designed</h2>
        <p>{gemini
          ? "Gemini proposes the price options from your goal."
          : "No Gemini key is set, so options come from built-in templates. Add GEMINI_API_KEY to the backend to use the AI agent."}</p>

        <h2>What past tests taught PlanLab</h2>
        {memory.length === 0 ? (
          <p>Nothing yet. Once a test settles, its result shapes the next design: past winners are re-tested first and stopped prices are dropped.</p>
        ) : (
          <ul className="memory">
            {[...memory].reverse().slice(0, 5).map((record) => (
              <li key={record.experiment_id}>
                <span>{record.goal}</span>
                <small>
                  {record.winner_name ? `Winner: ${record.winner_name}` : "No winner"}
                  {record.stopped_names.length > 0 && ` · Stopped: ${record.stopped_names.join(", ")}`}
                  {record.simulated && " · Simulated data"}
                </small>
              </li>
            ))}
          </ul>
        )}
      </aside>
    </div>
  );
}
