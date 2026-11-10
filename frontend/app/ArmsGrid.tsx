"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type {
  ColDef, ColGroupDef, FilterChangedEvent, ICellRendererParams, ITooltipParams, RowClassRules,
  ValueFormatterParams, ValueGetterParams,
} from "ag-grid-community";
import { LLM_LABEL } from "./api";
import type { AskResponse, GuardrailArm, Row } from "./api";
import { gridTheme, money, pct } from "./grid";

const ROLLOUT: Record<string, string> = {
  baseline: "baseline", in_place: "can change in place", new_signups_only: "new sign-ups only",
};

// Columns stored as fractions. Their filters work in percent so the header filter reads "3" for 3%.
const PERCENT = new Set<keyof Row>([
  "conversion", "paid_conversion", "trial_to_paid", "first_renewal", "churn", "prob_beats_baseline",
  "allocation", "refund_rate", "dispute_rate",
]);
const r4 = (x: number) => Math.round(x * 1e4) / 1e4;

type Ctx = { base: Row | null; breaches: Record<string, string[]>; ciMax: number };
type P = ICellRendererParams<Row, number | null, Ctx>;

// ---------- cell renderers ----------

/** Value plus its change against the pinned baseline row (green when better). */
function Delta({ p, fmt, lowerIsBetter = false }: { p: P; fmt: (v: number) => string; lowerIsBetter?: boolean }) {
  const v = p.value, b = p.context.base?.[p.colDef!.field as keyof Row] as number | null | undefined;
  if (v === null || v === undefined) return <>–</>;
  if (p.data?.is_baseline || !b) return <>{fmt(v)}</>;
  const rel = (v - b) / b, good = lowerIsBetter ? rel < 0 : rel > 0;
  return (
    <span>{fmt(v)} <span className={`delta ${Math.abs(rel) < 0.005 ? "" : good ? "up" : "down"}`}>
      {rel >= 0 ? "+" : ""}{(rel * 100).toFixed(0)}%</span></span>
  );
}

/** Probability bar with a tick at the 95% decision threshold. */
function ProbBar(p: P) {
  if (p.data?.is_baseline || p.value === null || p.value === undefined) return <span className="sub">baseline</span>;
  const v = p.value, cls = v >= 0.95 ? "good" : v < 0.05 ? "bad" : "";
  return (
    <div className="meter" title="The agent declares a winner at 95%">
      <div className={`meter-fill ${cls}`} style={{ width: `${v * 100}%` }} />
      <div className="meter-tick" style={{ left: "95%" }} />
      <span className="meter-label">{pct(v, 0)}</span>
    </div>
  );
}

/** Paid conversion with its 95% credible interval drawn as a whisker on a shared scale. */
function CiBar(p: ICellRendererParams<Row, number, Ctx>) {
  const d = p.data;
  if (!d) return null;
  const [lo, hi] = d.conv_ci, max = p.context.ciMax || 1, x = (v: number) => `${(v / max) * 100}%`;
  return (
    <div className="ci" title={`95% credible interval ${pct(lo)}–${pct(hi)}`}>
      <span className="ci-label">{pct(d.paid_conversion)}</span>
      <div className="ci-track">
        <div className="ci-range" style={{ left: x(lo), width: `calc(${x(hi)} - ${x(lo)})` }} />
        <div className="ci-dot" style={{ left: x(d.paid_conversion) }} />
      </div>
    </div>
  );
}

/** Weekly retention curve (share of each cohort still subscribed), drawn as a sparkline. */
function Sparkline(p: ICellRendererParams<Row, number[], Ctx>) {
  const pts = p.value ?? [];
  if (pts.length < 2) return <span className="sub">no data</span>;
  const W = 80, H = 22, x = (i: number) => (i / (pts.length - 1)) * W, y = (v: number) => H - v * (H - 2) - 1;
  const last = pts[pts.length - 1];
  return (
    <span className="spark" title={`Still subscribed after ${pts.length - 1} weeks: ${pct(last, 0)}`}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden>
        <polyline fill="none" stroke={last < 0.8 ? "#ff4d94" : "#2647d8"} strokeWidth="1.6"
          points={pts.map((v, i) => `${x(i)},${y(v)}`).join(" ")} />
      </svg>
      <span>{pct(last, 0)}</span>
    </span>
  );
}

function ShareBar(p: P) {
  const v = p.value ?? 0;
  return <div className="meter thin"><div className="meter-fill" style={{ width: `${v * 100}%` }} /><span className="meter-label">{pct(v, 0)}</span></div>;
}

function Status(p: ICellRendererParams<Row, string, Ctx>) {
  return <span className={`badge ${p.value === "stopped" ? "bad" : p.value === "winner" ? "ok" : ""}`}>{p.value}</span>;
}

// ---------- column definitions ----------

const pctF = (p: ValueFormatterParams<Row>) => pct(p.value);
const moneyF = (p: ValueFormatterParams<Row>) => money(p.value);
const num = (field: keyof Row, extra: ColDef<Row> = {}): ColDef<Row> => ({
  field, filter: "agNumberColumnFilter",
  ...(PERCENT.has(field) ? {
    filterValueGetter: (p: ValueGetterParams<Row>) => {
      const v = p.data?.[field] as number | null | undefined;
      return v === null || v === undefined ? null : r4(v * 100);
    },
    headerTooltip: extra.headerTooltip ?? "Filter values are in percent",
  } : {}),
  ...extra,
});

export default function ArmsGrid({ rows, answer, guards = [], onReset }: {
  rows: Row[]; answer: AskResponse | null; guards?: GuardrailArm[]; onReset?: () => void;
}) {
  const ref = useRef<AgGridReact<Row>>(null);
  const [shown, setShown] = useState<number | null>(null);

  const base = useMemo(() => rows.find((r) => r.is_baseline) ?? null, [rows]);
  const challengers = useMemo(() => rows.filter((r) => !r.is_baseline), [rows]);
  const pinned = useMemo(() => (base ? [base] : []), [base]);
  const context = useMemo(() => ({
    base,
    breaches: Object.fromEntries(guards.map((g) => [g.arm_id, g.breaches])),
    ciMax: Math.max(0.01, ...rows.map((r) => r.conv_ci?.[1] ?? 0)) * 1.1,
  }), [base, guards, rows]);

  const cols = useMemo<(ColDef<Row> | ColGroupDef<Row>)[]>(() => [
    { headerName: "Arm", children: [
      { field: "name", headerName: "Arm", pinned: "left", width: 170, filter: "agTextColumnFilter",
        valueFormatter: (p) => `${p.value}${p.data?.is_baseline ? " (baseline)" : ""}` },
      num("price", { headerName: "Price", width: 105, pinned: "left",
        valueFormatter: (p) => p.data ? `${money(p.data.price)}/${p.data.interval === "YEAR" ? "yr" : "mo"}` : "" }),
      { field: "status", width: 100, cellRenderer: Status, filter: "agTextColumnFilter", pinned: "left",
        tooltipValueGetter: (p: ITooltipParams<Row, string, Ctx>) =>
          (p.context.breaches[p.data?.arm_id ?? ""] ?? []).join("; ") || undefined },
    ] },
    { headerName: "Decision (Bayesian)", children: [
      num("prob_beats_baseline", { headerName: "Chance it earns more", width: 170, cellRenderer: ProbBar, valueFormatter: pctF,
        headerTooltip: "Probability this arm brings more new monthly revenue per visitor than the baseline. Winner at 95%." }),
      num("mrr_per_visitor", { headerName: "New MRR / visitor", width: 165, valueFormatter: moneyF,
        cellRenderer: (p: P) => <Delta p={p} fmt={(v) => money(v)} />,
        headerTooltip: "Decision metric: paid conversion × monthly price. Observable at checkout, not in a year." }),
      num("allocation", { headerName: "Traffic share", width: 125, cellRenderer: ShareBar, valueFormatter: pctF,
        headerTooltip: "Thompson sampling sends more visitors to arms that are likely to earn more" }),
    ] },
    { headerName: "Funnel", children: [
      num("visitors", { width: 100 }),
      num("signups", { headerName: "Sign-ups", width: 100, hide: true }),
      num("conversion", { headerName: "Sign-up conv.", width: 120, valueFormatter: pctF, hide: true }),
      num("paid_conversion", { headerName: "Paid conv. (95% CI)", width: 175, cellRenderer: CiBar, valueFormatter: pctF }),
      num("trial_to_paid", { headerName: "Trial → paid", width: 115, valueFormatter: (p) => pct(p.value, 0) }),
      num("paid", { headerName: "Paying", width: 90 }),
    ] },
    { headerName: "Revenue", children: [
      num("mrr", { headerName: "MRR", width: 100, valueFormatter: moneyF }),
      num("revenue", { headerName: "Collected", width: 110, valueFormatter: moneyF, hide: true }),
      num("projected_ltv", { headerName: "12-mo LTV (proj.)", width: 145, valueFormatter: moneyF,
        headerTooltip: "A projection from churn so far. Shown for context, never used to decide." }),
    ] },
    { headerName: "Retention & risk", children: [
      { field: "retention", headerName: "Retention by week", width: 165, cellRenderer: Sparkline, sortable: false,
        valueFormatter: (p) => (p.value?.length ? pct(p.value[p.value.length - 1], 0) : "–") },
      num("churn", { headerName: "Churn", width: 110, valueFormatter: pctF,
        cellRenderer: (p: P) => <Delta p={p} fmt={(v) => pct(v)} lowerIsBetter /> }),
      num("first_renewal", { headerName: "1st renewal", width: 110, valueFormatter: pctF, hide: true }),
      num("refund_rate", { headerName: "Refunds", width: 95, valueFormatter: pctF }),
      num("dispute_rate", { headerName: "Disputes", width: 95, valueFormatter: pctF, hide: true }),
    ] },
    { headerName: "Rollout & proof", children: [
      { field: "rollout", headerName: "If it wins", width: 160, valueFormatter: (p) => ROLLOUT[p.value] ?? p.value },
      num("real_events", { headerName: "Real PayPal events", width: 150,
        headerTooltip: "Events that came from the PayPal sandbox (KERNEL buyers, webhooks), not the simulator" }),
    ] },
  ], []);

  const rowClassRules = useMemo<RowClassRules<Row>>(() => ({
    "row-stopped": (p) => p.data?.status === "stopped",
    "row-winner": (p) => p.data?.status === "winner",
    "row-baseline": (p) => !!p.data?.is_baseline,
  }), []);

  // Apply the assistant's answer as the grid's own column filters and sort, so the merchant
  // can see in the headers exactly what the AI did and edit it like any filter.
  useEffect(() => {
    const api = ref.current?.api;
    if (!api || api.isDestroyed()) return;
    if (!answer) {
      api.setFilterModel(null);
      api.applyColumnState({ defaultState: { sort: null } });
      return;
    }
    const filters = answer.filters ?? (answer.filter ? [answer.filter] : []);
    const byCol: Record<string, { filterType: "number"; type: string; filter: number }[]> = {};
    for (const f of filters) {
      const value = PERCENT.has(f.metric) ? r4(f.value * 100) : f.value;
      (byCol[f.metric] ??= []).push({ filterType: "number", type: f.op === ">" ? "greaterThan" : "lessThan", filter: value });
    }
    api.setFilterModel(Object.fromEntries(Object.entries(byCol).map(([col, conds]) =>
      [col, conds.length === 1 ? conds[0] : { filterType: "number", operator: "AND", conditions: conds }])));
    api.applyColumnState({
      state: answer.sort ? [{ colId: answer.sort.metric, sort: answer.sort.desc ? "desc" : "asc" }] : [],
      defaultState: { sort: null },
    });
    const touched = [...Object.keys(byCol), ...(answer.sort ? [answer.sort.metric] : [])]
      .filter((c) => api.getColumn(c));
    if (touched.length) {
      api.setColumnsVisible(touched, true);  // the AI may filter on a column that is hidden by default
      api.ensureColumnVisible(touched[0], "middle");
      setTimeout(() => api.flashCells({ columns: touched }), 50);
    }
  }, [answer]);

  const onFilterChanged = useCallback((e: FilterChangedEvent<Row>) => {
    setShown(e.api.isAnyFilterPresent() ? e.api.getDisplayedRowCount() : null);
  }, []);

  const resetView = () => {
    const api = ref.current?.api;
    api?.setFilterModel(null);
    api?.resetColumnState();
    onReset?.();
  };

  const exportCsv = () => ref.current?.api?.exportDataAsCsv({
    fileName: "planlab-arms.csv",
    columnKeys: ref.current.api.getColumns()?.map((c) => c.getColId()).filter((c) => c !== "retention"),
  });

  return (
    <>
      <div className="grid-bar">
        <span className="sub small">
          {shown === null ? `${challengers.length} challenger arm(s) vs the pinned baseline`
            : `${shown} of ${challengers.length} challenger arm(s) match the filter`}
          {answer && (answer.filters?.length || answer.sort) ? (
            <span className="badge real" style={{ marginLeft: 8 }}>
              {LLM_LABEL[answer.source] ?? "rules"} set these filters · edit them in the headers
            </span>) : null}
        </span>
        <span className="grid-actions">
          <button className="ghost small" onClick={resetView}>Reset view</button>
          <button className="ghost small" onClick={exportCsv}>Export CSV</button>
        </span>
      </div>
      <div className="arms-grid">
      <AgGridReact<Row> ref={ref} theme={gridTheme} rowData={challengers} pinnedTopRowData={pinned}
        columnDefs={cols} context={context} getRowId={(p) => p.data.arm_id} domLayout="autoHeight"
        rowClassRules={rowClassRules} onFilterChanged={onFilterChanged} tooltipShowDelay={200}
        defaultColDef={{ sortable: true, resizable: true, enableCellChangeFlash: true }}
        overlayNoRowsTemplate="No challenger arm matches. Reset the view to see all arms." />
      </div>
    </>
  );
}
