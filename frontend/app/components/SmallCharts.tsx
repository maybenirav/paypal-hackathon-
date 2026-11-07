import type { Row } from "../api";
import { formatMetric, metricValue, money, pct } from "../format";

type ColorOf = (armId: string) => string;

/** Dot-and-whisker: where each option's true conversion rate most likely sits. */
export function ConversionRanges({ rows, colorOf }: { rows: Row[]; colorOf: ColorOf }) {
  const max = Math.max(0.01, ...rows.map((r) => r.conv_ci[1])) * 1.06;
  const base = rows.find((r) => r.is_baseline);
  const at = (value: number) => `${(value / max) * 100}%`;

  return (
    <div className="ranges">
      {rows.map((r) => (
        <div key={r.arm_id} className={r.status === "stopped" ? "range is-stopped" : "range"}>
          <div className="range-head">
            <span className="range-name"><i className="dot" style={{ background: colorOf(r.arm_id) }} />{r.name}</span>
            <span className="range-value"><strong>{pct(r.conversion)}</strong></span>
          </div>
          <div className="range-track" title={`${r.name}: ${pct(r.conversion)}, likely between ${pct(r.conv_ci[0])} and ${pct(r.conv_ci[1])}`}>
            {base && <b className="range-base" style={{ left: at(base.conversion) }} />}
            <i className="range-bar" style={{ left: at(r.conv_ci[0]), width: `${((r.conv_ci[1] - r.conv_ci[0]) / max) * 100}%`, background: colorOf(r.arm_id) }} />
            <i className="range-dot" style={{ left: at(r.conversion), background: colorOf(r.arm_id) }} />
          </div>
          <div className="range-foot">
            <span>{pct(r.conv_ci[0])} to {pct(r.conv_ci[1])}</span>
            <span>{r.status === "stopped" ? "Stopped" : `${money(r.value_per_visitor, "USD", 2)} per visitor`}</span>
          </div>
        </div>
      ))}
      {base && <p className="range-key"><b /> Baseline conversion, {pct(base.conversion)}</p>}
    </div>
  );
}

const HEAT_LOW = 0.6;

/** Cohort table on one sequential hue. Ink stays readable because the darkest step is capped. */
export function RetentionHeat({ rows, colorOf }: { rows: Row[]; colorOf: ColorOf }) {
  const weeks = Math.max(0, ...rows.map((r) => r.retention.length));
  if (!weeks) return <p className="muted">No sign-ups yet.</p>;
  const shade = (value: number) => 4 + Math.max(0, Math.min(1, (value - HEAT_LOW) / (1 - HEAT_LOW))) * 52;

  return (
    <>
      <div className="table-wrap">
        <table className="heat">
          <thead>
            <tr>
              <th scope="col">Price option</th>
              {Array.from({ length: weeks }, (_, w) => <th scope="col" key={w} className="num">Wk {w}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.arm_id}>
                <th scope="row"><i className="dot" style={{ background: colorOf(r.arm_id) }} />{r.name}</th>
                {Array.from({ length: weeks }, (_, w) => {
                  const value = r.retention[w];
                  return value === undefined || value === null
                    ? <td key={w} className="num heat-empty" title="Too few subscribers have been around this long">–</td>
                    : <td key={w} className="num" style={{ background: `color-mix(in oklab, var(--heat) ${shade(value)}%, var(--surface))` }}>{pct(value, 0)}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="heat-scale"><span>{pct(HEAT_LOW, 0)} or less</span><i /><span>100% still subscribed</span></p>
    </>
  );
}

/** The bar chart the assistant answers with. Each bar keeps its option's colour. */
export function MetricBars({ metric, rows, colorOf }: { metric: string; rows: Row[]; colorOf: ColorOf }) {
  const values = rows.map((r) => metricValue(r, metric));
  const max = Math.max(...values, 0) || 1;
  return (
    <ul className="bars">
      {rows.map((r, i) => (
        <li key={r.arm_id}>
          <span className="bars-name">{r.name}</span>
          <span className="bars-track"><i style={{ width: `${(values[i] / max) * 100}%`, background: colorOf(r.arm_id) }} /></span>
          <span className="bars-value">{formatMetric(metric, values[i])}</span>
        </li>
      ))}
    </ul>
  );
}
