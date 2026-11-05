const COLORS = ["#4f8cff", "#2fbf71", "#ffb020", "#ef5350"];

export default function MrrChart({ series, arms }: { series: Record<string, number>[]; arms: string[] }) {
  const W = 700, H = 200, P = 30;
  const max = Math.max(1, ...series.flatMap((p) => arms.map((a) => p[a] ?? 0)));
  const maxDay = Math.max(1, ...series.map((p) => p.day));
  const x = (d: number) => P + (d / maxDay) * (W - 2 * P);
  const y = (v: number) => H - P - (v / max) * (H - 2 * P);
  return (
    <div style={{ marginTop: 16 }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="MRR over time per arm">
        <line x1={P} y1={H - P} x2={W - P} y2={H - P} stroke="#2a3350" />
        <line x1={P} y1={P} x2={P} y2={H - P} stroke="#2a3350" />
        <text x={P} y={P - 8} fill="#8e99b8" fontSize="11">MRR (max ${max.toFixed(0)})</text>
        <text x={W - P} y={H - 8} fill="#8e99b8" fontSize="11" textAnchor="end">day {maxDay}</text>
        {arms.map((a, i) => (
          <polyline key={a} fill="none" stroke={COLORS[i % COLORS.length]} strokeWidth="2"
            points={series.map((p) => `${x(p.day)},${y(p[a] ?? 0)}`).join(" ")} />
        ))}
      </svg>
      <div style={{ display: "flex", gap: 16, fontSize: 13 }}>
        {arms.map((a, i) => <span key={a} style={{ color: COLORS[i % COLORS.length] }}>● {a}</span>)}
      </div>
    </div>
  );
}
