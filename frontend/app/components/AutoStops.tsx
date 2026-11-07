import type { Experiment, Stop } from "../api";
import { armText, localTime, nameOf } from "../format";
import { Icon } from "../icons";

type Props = {
  exp: Experiment;
  stops: Stop[];
  day: number;
  busy: boolean;
  onResume: (armId: string) => void;
};

/** Options PlanLab stopped by itself. The timeline shows each one only from the day it happened. */
export default function AutoStops({ exp, stops, day, busy, onResume }: Props) {
  const name = nameOf(exp);
  const shown = stops.filter((stop) => stop.automatic && (stop.day === null || stop.day <= day));
  const rolledOut = exp.rollout?.status === "approved";

  return (
    <div aria-live="polite">
      {shown.map((stop) => (
        <section className="verdict verdict-crit" key={stop.arm_id}>
          <div className="stamp"><Icon name="stop" size={14} /> Auto-stopped</div>
          <div className="verdict-body">
            <h2>{name(stop.arm_id)} was stopped automatically {stop.day === null ? `on ${localTime(stop.at)}` : `on day ${stop.day}`}</h2>
            <p>{armText(exp, stop.reasons)} New customers now get the other options. Existing subscribers keep their plan.</p>
            {!rolledOut && (
              <div className="verdict-actions">
                <button className="btn" disabled={busy} onClick={() => onResume(stop.arm_id)}>Resume this option</button>
                <span className="hint">Automatic checks then leave it alone, but running the analysis can still stop it.</span>
              </div>
            )}
          </div>
        </section>
      ))}
    </div>
  );
}
