import type { Experiment } from "../api";
import { statusInfo } from "../format";
import { Icon, Pill } from "../icons";

type Props = { exp: Experiment; busy: boolean; onDelete: () => void };

export default function ExperimentHeader({ exp, busy, onDelete }: Props) {
  const total = exp.events.real + exp.events.simulated;
  const status = statusInfo(exp.status, total);
  const steps = [
    { label: "Design", done: true },
    { label: "Collect data", done: total > 0 },
    { label: "Decide", done: exp.winner !== null },
    { label: "Roll out", done: exp.rollout?.status === "approved" },
  ];
  const current = steps.findIndex((s) => !s.done);

  return (
    <header className="exp-head">
      <div className="exp-meta">
        <Pill kind={status.kind}>{status.label}</Pill>
        {total > 0 && (exp.events.real > 0
          ? <Pill kind="good">Real PayPal data</Pill>
          : <Pill kind="warn">Simulated data</Pill>)}
        <span className="exp-source">{exp.source === "gemini" ? "Designed by Gemini" : "Designed from templates"}</span>
        <code className="exp-id">{exp.id}</code>
        <button className="link exp-delete" disabled={busy} onClick={onDelete}>Delete</button>
      </div>
      <h1>{exp.goal}</h1>
      <ol className="steps" aria-label="Experiment progress">
        {steps.map((step, i) => (
          <li key={step.label} className={step.done ? "done" : i === current ? "current" : ""}
            aria-current={i === current ? "step" : undefined}>
            <span className="step-mark">{step.done ? <Icon name="check" size={12} /> : i + 1}</span>
            {step.label}
          </li>
        ))}
      </ol>
    </header>
  );
}
