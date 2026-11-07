import type { Experiment } from "../api";
import { armText, nameOf } from "../format";
import { Icon, Pill } from "../icons";

type Props = {
  exp: Experiment;
  busy: boolean;
  onAnalyze: () => void;
  onPropose: () => void;
  onApprove: () => void;
};

const STAMP = { winner: "Winner", stop_arm: "Stopped", keep_testing: "Keep testing" };
const TONE = { winner: "good", stop_arm: "crit", keep_testing: "neutral" };

export default function Verdict({ exp, busy, onAnalyze, onPropose, onApprove }: Props) {
  const decision = exp.last_decision;
  if (!decision) return null;

  const name = nameOf(exp);
  const when = decision.as_of_day === null ? "" : ` at day ${decision.as_of_day}`;
  const armName = decision.arm_id ? name(decision.arm_id) : "";
  const baselineWon = decision.action === "winner" && exp.arms.find((a) => a.arm_id === decision.arm_id)?.is_baseline;
  const headline = {
    winner: baselineWon ? `Keep your current price${when}` : `${armName} wins${when}`,
    stop_arm: `${armName} was stopped${when}`,
    keep_testing: `No clear result yet${when}`,
  }[decision.action];
  const explanation = armText(exp, decision.explanation);

  const rollout = exp.rollout;
  const dryRun = rollout?.results.some((r) => r.status === "dry_run");
  const placeholderPlans = rollout?.requests.some((r) => r.path.includes("PLAN_ID_"));
  const staying = Object.entries(rollout?.existing_subscribers ?? {}).filter(([, count]) => count > 0);
  const rolloutTitle = rollout?.kind === "update_pricing_scheme" ? "Rollout: update the live price"
    : rollout?.kind === "keep_baseline" ? "Wrap-up: keep the current price and retire the other options"
      : "Rollout: make this the default plan";

  return (
    <section className={`verdict verdict-${TONE[decision.action]}`} aria-live="polite">
      <div className="stamp" key={`${decision.action}-${decision.arm_id}-${decision.as_of_day}`}>
        <Icon name={decision.action === "winner" ? "check" : decision.action === "stop_arm" ? "stop" : "dot"} size={14} />
        {STAMP[decision.action]}
      </div>
      <div className="verdict-body">
        <h2>{headline}</h2>
        <p>{explanation}</p>

        {decision.action === "stop_arm" && (
          <div className="verdict-actions">
            <button className="btn" disabled={busy} onClick={onAnalyze}>Re-run analysis on the remaining options</button>
          </div>
        )}

        {decision.action === "winner" && !rollout && (
          <div className="verdict-actions">
            <button className="btn primary" disabled={busy} onClick={onPropose}>Propose rollout</button>
          </div>
        )}

        {rollout && (
          <div className="rollout">
            <h3>{rolloutTitle}</h3>
            <ul>
              {rollout.requests.map((request) => (
                <li key={`${request.method}-${request.path}`}>
                  <code>{request.method === "INTERNAL" ? `PlanLab: ${request.path}` : `${request.method} ${request.path}`}</code>
                </li>
              ))}
            </ul>
            {placeholderPlans && <p className="hint">PLAN_ID_ values are placeholders until the plans are published from the PayPal tab.</p>}
            {staying.length > 0 && (
              <p className="rollout-existing">
                Existing subscribers keep the price they signed up for: {staying.map(([id, count]) => `${count} on ${name(id)}`).join(", ")}. New customers get {name(rollout.winner)}.
              </p>
            )}
            {rollout.status === "proposed" ? (
              <div className="verdict-actions">
                <button className="btn primary" disabled={busy} onClick={onApprove}>Approve rollout</button>
                <span className="hint">Nothing changes until you approve.</span>
              </div>
            ) : rollout.status === "failed" ? (
              <div className="verdict-actions">
                <Pill kind="crit" icon="alert">Some PayPal requests failed</Pill>
                <button className="btn" disabled={busy} onClick={onApprove}>Try again</button>
              </div>
            ) : dryRun ? (
              <p className="rollout-state"><Pill kind="warn">Approved as a dry run</Pill> These requests were logged, not sent to PayPal.</p>
            ) : (
              <p className="rollout-state"><Pill kind="good">Rolled out</Pill> These requests were sent to PayPal.</p>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
