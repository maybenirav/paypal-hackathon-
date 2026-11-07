"use client";

import { useMemo, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { MrrPoint, Row } from "../api";
import { money } from "../format";
import { useWidth } from "../hooks";

type Series = { id: string; name: string; color: string; stopped: boolean };
type Props = {
  series: Series[];
  /** Points drawn so far. */
  points: MrrPoint[];
  /** The whole run, so the y-axis stays still while the timeline replays. */
  scale: MrrPoint[];
  rows: Row[];
  domain: number;
  currency: string;
};

const HEIGHT = 268;
const MARGIN = { top: 10, right: 62, bottom: 26, left: 50 };
const LABEL_GAP = 15;

/** A round step (1, 2 or 5 times a power of ten) that fits about four intervals, so tick labels are exact. */
function niceAxis(maxValue: number) {
  const max = maxValue > 0 ? maxValue : 100;
  const rough = max / 4;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const leading = rough / magnitude;
  const step = (leading <= 1 ? 1 : leading <= 2 ? 2 : leading <= 5 ? 5 : 10) * magnitude;
  const count = Math.ceil(max / step);
  return { top: step * count, ticks: Array.from({ length: count + 1 }, (_, i) => i * step), digits: step < 1 ? 2 : 0 };
}

export default function MrrChart({ series, points, scale, rows, domain, currency }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);

  const plotWidth = Math.max(0, width - MARGIN.left - MARGIN.right);
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;

  const axis = useMemo(() => {
    const seen = [...scale, ...points].flatMap((p) => series.map((s) => p[s.id] ?? 0));
    return niceAxis(Math.max(...seen, ...rows.map((r) => r.mrr)) * 1.04);
  }, [points, rows, scale, series]);
  const yMax = axis.top;

  const x = (day: number) => MARGIN.left + (domain ? (day / domain) * plotWidth : 0);
  const y = (value: number) => MARGIN.top + plotHeight - (value / yMax) * plotHeight;

  const xStep = domain > 28 ? 14 : domain > 14 ? 7 : domain > 7 ? 2 : 1;
  const xTicks: number[] = [];
  for (let d = 0; d <= domain; d += xStep) xTicks.push(d);

  const lastPoint = points[points.length - 1];
  const endLabels = useMemo(() => {
    if (!lastPoint || points.length < 2) return [];
    const placed: { id: string; value: number; py: number }[] = [];
    const byValue = series.map((s) => ({ id: s.id, value: lastPoint[s.id] ?? 0 })).sort((a, b) => b.value - a.value);
    for (const item of byValue) {
      const py = MARGIN.top + plotHeight - (item.value / yMax) * plotHeight;
      // Labels that would collide are dropped, not nudged; the legend and tooltip still carry them.
      if (placed.every((p) => Math.abs(p.py - py) >= LABEL_GAP)) placed.push({ ...item, py });
    }
    return placed;
  }, [lastPoint, plotHeight, points.length, series, yMax]);

  function onPointer(event: PointerEvent<SVGRectElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    const day = ((event.clientX - box.left) / box.width) * domain;
    let nearest = 0;
    points.forEach((p, i) => { if (Math.abs(p.day - day) < Math.abs(points[nearest].day - day)) nearest = i; });
    setHover(nearest);
  }

  function onKey(event: KeyboardEvent<SVGSVGElement>) {
    if (event.key === "Escape") return setHover(null);
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const move = event.key === "ArrowRight" ? 1 : -1;
    setHover((h) => Math.max(0, Math.min(points.length - 1, (h ?? points.length - 1) + move)));
  }

  const active = hover !== null ? points[hover] : null;
  const tipLeft = active ? x(active.day) + 14 : 0;
  const tipFlipped = tipLeft + 190 > width;
  const leader = lastPoint ? [...series].sort((a, b) => (lastPoint[b.id] ?? 0) - (lastPoint[a.id] ?? 0))[0] : null;

  return (
    <div className="chart" ref={ref}>
      <ul className="legend">
        {series.map((s) => (
          <li key={s.id} className={s.stopped ? "is-stopped" : ""}>
            <i className="key-line" style={{ background: s.color }} />{s.name}{s.stopped && " (stopped)"}
          </li>
        ))}
      </ul>

      {/* The space is reserved from the start, so drawing the chart doesn't push the page down */}
      <div className="chart-plot" style={{ height: HEIGHT }}>
        {width > 0 && <>
          <svg width={width} height={HEIGHT} role="img" tabIndex={0} onKeyDown={onKey} onBlur={() => setHover(null)}
            aria-label={`Monthly recurring revenue by price option, day 0 to ${lastPoint?.day ?? 0}. ${leader && lastPoint ? `Highest: ${leader.name} at ${money(lastPoint[leader.id] ?? 0, currency)}.` : ""} Use the left and right arrow keys to read each point.`}>
            {axis.ticks.map((tick) => (
              <g key={tick}>
                <line x1={MARGIN.left} x2={MARGIN.left + plotWidth} y1={y(tick)} y2={y(tick)}
                  className={tick === 0 ? "axis-base" : "axis-grid"} />
                <text x={MARGIN.left - 8} y={y(tick)} dy="0.32em" textAnchor="end" className="axis-text">{money(tick, currency, axis.digits)}</text>
              </g>
            ))}
            {xTicks.map((tick) => (
              <text key={tick} x={x(tick)} y={HEIGHT - 6} textAnchor="middle" className="axis-text">Day {tick}</text>
            ))}

            {series.map((s) => (
              <path key={s.id} d={points.map((p, i) => `${i ? "L" : "M"}${x(p.day)} ${y(p[s.id] ?? 0)}`).join(" ")}
                fill="none" stroke={s.color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                opacity={s.stopped ? 0.45 : 1} />
            ))}
            {lastPoint && series.map((s) => (
              <circle key={s.id} cx={x(lastPoint.day)} cy={y(lastPoint[s.id] ?? 0)} r="4" fill={s.color}
                stroke="var(--surface)" strokeWidth="2" opacity={s.stopped ? 0.45 : 1} />
            ))}
            {lastPoint && endLabels.map((label) => (
              <text key={label.id} x={x(lastPoint.day) + 9} y={label.py} dy="0.32em" className="end-label">
                {money(label.value, currency)}
              </text>
            ))}

            {active && (
              <g>
                <line x1={x(active.day)} x2={x(active.day)} y1={MARGIN.top} y2={MARGIN.top + plotHeight} className="crosshair" />
                {series.map((s) => (
                  <circle key={s.id} cx={x(active.day)} cy={y(active[s.id] ?? 0)} r="4" fill={s.color} stroke="var(--surface)" strokeWidth="2" />
                ))}
              </g>
            )}
            <rect x={MARGIN.left} y={MARGIN.top} width={plotWidth} height={plotHeight} fill="transparent"
              onPointerMove={onPointer} onPointerLeave={() => setHover(null)} />
          </svg>

          {active && (
            <div className="chart-tip" style={tipFlipped ? { right: width - x(active.day) + 14 } : { left: tipLeft }}>
              <span className="chart-tip-day">Day {active.day}</span>
              {[...series].sort((a, b) => (active[b.id] ?? 0) - (active[a.id] ?? 0)).map((s) => (
                <div key={s.id} className="chart-tip-row">
                  <i className="key-line" style={{ background: s.color }} />
                  <strong>{money(active[s.id] ?? 0, currency)}</strong>
                  <span>{s.name}</span>
                </div>
              ))}
            </div>
          )}
        </>}
      </div>

      <details className="table-view">
        <summary>View as table</summary>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th scope="col">Day</th>{series.map((s) => <th scope="col" key={s.id} className="num">{s.name}</th>)}</tr></thead>
            <tbody>
              {points.map((p) => (
                <tr key={p.day}>
                  <td>{p.day}</td>
                  {series.map((s) => <td key={s.id} className="num">{money(p[s.id] ?? 0, currency)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
