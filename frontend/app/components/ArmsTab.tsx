import type { Experiment, Scenario } from "../api";
import { colorOf, money, priceLabel } from "../format";
import { Pill } from "../icons";

type Props = {
  exp: Experiment;
  busy: boolean;
  onDemo: (scenario: Scenario) => void;
  onOpenPayPal: () => void;
};

export default function ArmsTab({ exp, busy, onDemo, onOpenPayPal }: Props) {
  const color = colorOf(exp);
  const hasData = exp.events.real + exp.events.simulated > 0;

  return (
    <div className="stack">
      <section className="panel source-panel">
        <div>
          <h3>{hasData ? "Replace this experiment's data" : "Next, get data for this test"}</h3>
          <p className="muted">Simulations run 60 days of made-up sign-ups in seconds and are always labelled as simulated. Real sign-ups go through your PayPal sandbox.</p>
        </div>
        <div className="btn-row">
          <button className="btn primary" disabled={busy} onClick={() => onDemo("winner")}>Simulate a clear winner</button>
          <button className="btn" disabled={busy} onClick={() => onDemo("guardrail")}>Simulate a risky price</button>
          <button className="btn" onClick={onOpenPayPal}>Use real PayPal sign-ups</button>
        </div>
      </section>

      <section className="panel options" aria-label="Proposed price options">
        {exp.arms.map((arm) => {
          const check = exp.validation.find((v) => v.arm_id === arm.arm_id);
          return (
            <article className="option" key={arm.arm_id}>
              <div className="option-price">
                <h4><i className="dot" style={{ background: color(arm.arm_id) }} />{arm.name}{arm.is_baseline && <em className="tag">Baseline</em>}</h4>
                <p className="option-figure">{priceLabel(arm.price, arm.interval, arm.currency)}</p>
                <p className="option-terms">
                  {arm.interval === "YEAR" && `${money(arm.price / 12, arm.currency, 2)}/mo equivalent · `}
                  {arm.trial_days ? `${arm.trial_days}-day trial` : "No trial"}
                  {arm.setup_fee > 0 && ` · ${money(arm.setup_fee, arm.currency, 2)} setup fee`}
                </p>
              </div>
              <p className="option-why">{arm.reason}</p>
              <div className="option-check">
                {check?.valid
                  ? <Pill kind="good">Valid PayPal plan</Pill>
                  : <Pill kind="crit" icon="alert">Invalid plan</Pill>}
                {check?.errors.map((error) => <p className="field-error" key={error}>{error}</p>)}
                {arm.plan_id && <code className="plan-id">{arm.plan_id}</code>}
                {check?.paypal_payload && (
                  <details className="payload">
                    <summary>View plan request</summary>
                    <pre>{JSON.stringify(check.paypal_payload, null, 2)}</pre>
                  </details>
                )}
              </div>
            </article>
          );
        })}
      </section>
    </div>
  );
}
