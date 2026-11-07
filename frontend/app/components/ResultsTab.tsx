"use client";

import dynamic from "next/dynamic";
import { useMemo, useState } from "react";
import type { Experiment, Metrics, MrrPoint, Scenario } from "../api";
import { colorOf, money, pct } from "../format";
import { Icon, Pill } from "../icons";
import Assistant from "./Assistant";
import MrrChart from "./MrrChart";
import { ConversionRanges, RetentionHeat } from "./SmallCharts";

type Props = {
  exp: Experiment;
  metrics: Metrics;
  metricsDay: number;
  day: number;
  playing: boolean;
  refetching: boolean;
  busy: string | null;
  onScrub: (day: number) => void;
  onTogglePlay: () => void;
  onAnalyze: () => void;
  onDemo: (scenario: Scenario) => void;
  onError: (error: unknown) => void;
};

// AG Grid is the heaviest dependency, so it only loads once there are results to show.
const ArmsGrid = dynamic(() => import("./ArmsGrid"), {
  ssr: false,
  loading: () => <p className="grid-loading" role="status">Loading grid…</p>,
});

const CHECKPOINT_DAYS = 14;

function Timeline({ exp, metrics, day, playing, busy, onScrub, onTogglePlay, onAnalyze, onDemo }: Props) {
  const windowDays = metrics.window_days;
  const checkpoints: number[] = [];
  for (let d = 0; d <= windowDays; d += CHECKPOINT_DAYS) checkpoints.push(d);
  const progress = windowDays ? (day / windowDays) * 100 : 0;
  const analyzing = busy === "analyze";
  const rolledOut = exp.rollout?.status === "approved";

  return (
    <section className="timeline" aria-label="Simulated timeline">
      <div className="timeline-read">
        <span className="timeline-label">Simulated day</span>
        <output className="timeline-day" aria-live="off">{day}</output>
        <span className="timeline-of">of {windowDays}</span>
      </div>
      <button className="play" onClick={onTogglePlay} aria-label={playing ? "Pause replay" : "Replay the experiment"}
        title={playing ? "Pause" : "Replay"}>
        <Icon name={playing ? "pause" : "play"} size={18} />
      </button>
      <div className="scrub">
        <input type="range" min={0} max={windowDays} step={1} value={day} aria-label="Experiment day"
          aria-valuetext={`Day ${day} of ${windowDays}`} style={{ "--progress": `${progress}%` } as React.CSSProperties}
          onChange={(e) => onScrub(Number(e.target.value))} />
        <div className="ticks" aria-hidden="true">
          {checkpoints.map((d) => (
            <span key={d} style={{ left: `calc(9px + (100% - 18px) * ${d / windowDays})` }}>{d}</span>
          ))}
          {exp.stops.map((stop) => stop.day !== null && (
            <i key={stop.arm_id} className="tick-stop" style={{ left: `calc(9px + (100% - 18px) * ${stop.day / windowDays})` }} />
          ))}
        </div>
      </div>
      <div className="timeline-actions">
        <div className="segmented small" role="group" aria-label="Replay a scenario">
          {(["winner", "guardrail"] as const).map((scenario) => (
            <button key={scenario} className={exp.scenario === scenario ? "on" : ""} aria-pressed={exp.scenario === scenario}
              disabled={busy !== null} onClick={() => onDemo(scenario)}>
              {scenario === "winner" ? "Winner" : "Risky price"}
            </button>
          ))}
        </div>
        <button className="btn primary" disabled={busy !== null || rolledOut} onClick={onAnalyze}>
          {rolledOut ? "Rolled out" : analyzing ? "Analyzing…" : day < windowDays ? `Run analysis at day ${day}` : "Run analysis"}
        </button>
      </div>
    </section>
  );
}

function Tiles({ exp, metrics }: { exp: Experiment; metrics: Metrics }) {
  const currency = exp.arms[0]?.currency ?? "USD";
  const rows = metrics.rows;
  const base = rows.find((r) => r.is_baseline);
  const live = rows.filter((r) => r.status !== "stopped");
  const withData = live.filter((r) => r.signups > 0);
  const leader = [...withData].sort((a, b) => b.prob_best - a.prob_best)[0];
  const totalMrr = rows.reduce((sum, r) => sum + r.mrr, 0);
  const stoppedArms = new Set(metrics.stopped_arms);
  const breached = metrics.guardrails.arms.filter((a) => !a.ok && !stoppedArms.has(a.arm_id));
  const name = (id: string) => rows.find((r) => r.arm_id === id)?.name ?? id;
  const stoppedNames = metrics.stopped_arms.map(name).join(", ");
  // Only compare against a baseline that has real sign-ups behind its number.
  const lift = leader && !leader.is_baseline && base && base.signups > 0 ? leader.lift : null;

  return (
    <div className="tiles">
      <div className="tile">
        <span className="tile-label">Leading option</span>
        <strong className="tile-value tile-text">{leader ? leader.name : "–"}</strong>
        <span className="tile-sub">{leader ? `${pct(leader.prob_best, 0)} chance it's worth the most` : "Waiting for sign-ups"}</span>
      </div>
      <div className="tile">
        <span className="tile-label">Its value per visitor</span>
        <strong className="tile-value">{leader ? money(leader.value_per_visitor, currency, 2) : "–"}</strong>
        <span className="tile-sub">
          {leader ? `${money(leader.ltv, currency, 2)} a subscriber × ${pct(leader.conversion)} sign-ups` : "Expected 12-month revenue per visitor"}
          {lift != null && ` · ${lift >= 0 ? "+" : ""}${pct(lift, 0)} vs baseline`}
        </span>
      </div>
      <div className="tile">
        <span className="tile-label">MRR, all options</span>
        <strong className="tile-value">{money(totalMrr, currency)}</strong>
        <span className="tile-sub">{base ? `Baseline ${money(base.mrr, currency)}` : ""}</span>
      </div>
      <div className={breached.length ? "tile tile-alert" : "tile"}>
        <span className="tile-label">Guardrails</span>
        <div className="tile-value tile-status">
          {breached.length ? <Pill kind="crit" icon="alert">{breached.length} breached</Pill>
            : stoppedArms.size ? <Pill kind="crit">{stoppedArms.size} stopped</Pill>
              : <Pill kind="good">All clear</Pill>}
        </div>
        <span className="tile-sub">
          {breached.length ? breached.map((a) => name(a.arm_id)).join(", ")
            : stoppedNames ? stoppedNames
              : "Retention, refunds and disputes in limits"}
        </span>
      </div>
    </div>
  );
}

export default function ResultsTab(props: Props) {
  const { exp, metrics, metricsDay, refetching, busy, onAnalyze, onDemo, onError } = props;
  const [filter, setFilter] = useState<string[] | null>(null);

  const total = exp.events.real + exp.events.simulated;
  const simulated = exp.events.real === 0;
  const color = colorOf(exp);
  const stopped = new Set(metrics.stopped_arms);

  const series = useMemo(() => metrics.rows.map((r) => ({
    id: r.arm_id, name: r.name, color: color(r.arm_id), stopped: stopped.has(r.arm_id),
  // Rows only change identity on a new frame; names and colours are stable for an experiment.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  })), [metrics]);

  const points = useMemo(() => {
    const shown = metrics.mrr_series.filter((p) => p.day <= metricsDay);
    const last = shown[shown.length - 1];
    if (simulated && (!last || last.day < metricsDay)) {
      const current = { day: metricsDay } as MrrPoint;
      for (const row of metrics.rows) current[row.arm_id] = row.mrr;
      shown.push(current);
    }
    return shown;
  }, [metrics, metricsDay, simulated]);

  if (total === 0) {
    return (
      <section className="empty">
        <h2>No data yet</h2>
        <p>Simulate 60 days of sign-ups to see how PlanLab decides, or collect real sign-ups from the PayPal tab.</p>
        <div className="btn-row">
          <button className="btn primary" disabled={busy !== null} onClick={() => onDemo("winner")}>Simulate a clear winner</button>
          <button className="btn" disabled={busy !== null} onClick={() => onDemo("guardrail")}>Simulate a risky price</button>
        </div>
      </section>
    );
  }

  const visible = filter ? filter.map((id) => metrics.rows.find((r) => r.arm_id === id)!).filter(Boolean) : metrics.rows;

  return (
    <div className="results">
      {simulated ? <Timeline {...props} /> : (
        <section className="timeline timeline-live">
          <p><strong>{exp.events.real}</strong> real PayPal events so far. Updates by itself.</p>
          <button className="btn primary" disabled={busy !== null || exp.rollout?.status === "approved"} onClick={onAnalyze}>
            {exp.rollout?.status === "approved" ? "Rolled out" : busy === "analyze" ? "Analyzing…" : "Run analysis"}
          </button>
        </section>
      )}

      <div className={refetching ? "results-data is-refetching" : "results-data"}>
        <Tiles exp={exp} metrics={metrics} />

        <div className="chart-row">
          <section className="panel">
            <h3>Monthly recurring revenue</h3>
            <MrrChart series={series} points={points} scale={metrics.mrr_series} rows={metrics.rows}
              domain={metrics.window_days} currency={exp.arms[0]?.currency ?? "USD"} />
          </section>
          <section className="panel">
            <h3>Conversion, with 95% range</h3>
            <ConversionRanges rows={metrics.rows} colorOf={color} />
          </section>
        </div>

        <section className="panel panel-flush">
          <div className="panel-head">
            <h3>Price options</h3>
            <Assistant expId={exp.id} rows={metrics.rows} colorOf={color} onFilter={setFilter} onError={onError}
              asOfDay={simulated && metricsDay < metrics.window_days ? metricsDay : undefined} />
          </div>
          <ArmsGrid rows={visible} arms={exp.arms} guardrails={metrics.guardrails} colorOf={color} />
        </section>

        <section className="panel">
          <h3>Retention by week since sign-up</h3>
          <RetentionHeat rows={metrics.rows} colorOf={color} />
        </section>
      </div>
    </div>
  );
}
