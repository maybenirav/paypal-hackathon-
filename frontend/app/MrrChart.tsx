const COLORS = ["#2647d8", "#1fae5b", "#ff4d94", "#e8673b"];

export default function MrrChart({ series, arms }: { series: Record<string, number>[]; arms: string[] }) {
  const W = 700, H = 200, P = 30;
  const max = Math.max(1, ...series.flatMap((p) => arms.map((a) => p[a] ?? 0)));
  const maxDay = Math.max(1, ...series.map((p) => p.day));
  const x = (d: number) => P + (d / maxDay) * (W - 2 * P);
  const y = (v: number) => H - P - (v / max) * (H - 2 * P);
  return (
    <div style={{ marginTop: 16 }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="MRR over time per arm">
        <line x1={P} y1={H - P} x2={W - P} y2={H - P} stroke="#151515" strokeWidth="1.5" />
        <line x1={P} y1={P} x2={P} y2={H - P} stroke="#151515" strokeWidth="1.5" />
        <text x={P} y={P - 8} fill="#6b675f" fontSize="11" fontFamily="var(--font-mono-stack)">MRR (max ${max.toFixed(0)})</text>
        <text x={W - P} y={H - 8} fill="#6b675f" fontSize="11" fontFamily="var(--font-mono-stack)" textAnchor="end">day {maxDay}</text>
        {arms.map((a, i) => (
          <polyline key={a} fill="none" stroke={COLORS[i % COLORS.length]} strokeWidth="2.5" strokeLinejoin="round"
            points={series.map((p) => `${x(p.day)},${y(p[a] ?? 0)}`).join(" ")} />
        ))}
      </svg>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 12, fontFamily: "var(--font-mono-stack)", fontWeight: 700 }}>
        {arms.map((a, i) => <span key={a} style={{ color: COLORS[i % COLORS.length] }}>● {a}</span>)}
      </div>
    </div>
  );
}
