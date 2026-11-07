"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef, ICellRendererParams, IRowNode } from "ag-grid-community";
import type { AskResponse, Row } from "./api";
import { gridTheme, money, pct } from "./grid";

const ROLLOUT: Record<string, string> = {
  baseline: "baseline", in_place: "can change in place", new_signups_only: "new sign-ups only",
};

export default function ArmsGrid({ rows, answer }: { rows: Row[]; answer: AskResponse | null }) {
  const ref = useRef<AgGridReact<Row>>(null);
  const filter = answer?.filter ?? null;

  const cols = useMemo<ColDef<Row>[]>(() => [
    { field: "name", headerName: "Arm", pinned: "left", width: 190,
      valueFormatter: (p) => `${p.value}${p.data?.is_baseline ? " (baseline)" : ""}` },
    { headerName: "Price", width: 110,
      valueGetter: (p) => p.data && `${money(p.data.price)}/${p.data.interval === "YEAR" ? "yr" : "mo"}` },
    { field: "status", width: 100,
      cellRenderer: (p: ICellRendererParams<Row>) =>
        <span className={`badge ${p.value === "stopped" ? "bad" : p.value === "winner" ? "ok" : ""}`}>{p.value}</span> },
    { field: "prob_beats_baseline", headerName: "Chance it earns more", width: 165,
      headerTooltip: "Probability this arm brings more new monthly revenue per visitor than the baseline",
      valueFormatter: (p) => (p.data?.is_baseline ? "–" : pct(p.value, 0)),
      cellClassRules: { "cell-good": (p) => (p.value ?? 0) >= 0.95, "cell-bad": (p) => p.value !== null && p.value < 0.05 } },
    { field: "mrr_per_visitor", headerName: "New MRR / visitor", width: 145, valueFormatter: (p) => money(p.value),
      headerTooltip: "Decision metric: paid conversion × monthly price. Observable at checkout, not in a year." },
    { field: "paid_conversion", headerName: "Paid conv.", width: 150,
      valueFormatter: (p) => `${pct(p.value)} (${pct(p.data?.conv_ci[0])}–${pct(p.data?.conv_ci[1])})` },
    { field: "trial_to_paid", headerName: "Trial → paid", width: 115, valueFormatter: (p) => pct(p.value, 0) },
    { field: "allocation", headerName: "Traffic share", width: 120, valueFormatter: (p) => pct(p.value, 0) },
    { field: "churn", headerName: "Churn", width: 90, valueFormatter: (p) => pct(p.value) },
    { field: "refund_rate", headerName: "Refunds", width: 95, valueFormatter: (p) => pct(p.value) },
    { field: "visitors", width: 95 }, { field: "paid", headerName: "Paying", width: 90 },
    { field: "mrr", headerName: "MRR", width: 95, valueFormatter: (p) => money(p.value) },
    { field: "projected_ltv", headerName: "12-mo LTV (projected)", width: 170, valueFormatter: (p) => money(p.value),
      headerTooltip: "A projection from churn so far. Shown for context, never used to decide." },
    { field: "rollout", headerName: "If it wins", width: 170, valueFormatter: (p) => ROLLOUT[p.value] ?? p.value },
    { field: "real_events", headerName: "Real events", width: 110 },
  ], []);

  const isExternalFilterPresent = useCallback(() => filter !== null, [filter]);
  const doesExternalFilterPass = useCallback((node: IRowNode<Row>) => {
    if (!filter || !node.data || node.data.is_baseline) return !filter;
    const v = node.data[filter.metric] as number | null;
    return v !== null && (filter.op === ">" ? v > filter.value : v < filter.value);
  }, [filter]);

  // AG Grid only re-runs an external filter when told to.
  useEffect(() => { ref.current?.api?.onFilterChanged(); }, [filter]);

  const sorted = useMemo(() => {
    if (!answer?.sort) return rows;
    const { metric, desc } = answer.sort;
    return [...rows].sort((a, b) => ((b[metric] as number) - (a[metric] as number)) * (desc ? 1 : -1));
  }, [rows, answer]);

  return (
    <div style={{ height: 64 + 42 * Math.max(rows.length, 2) }}>
      <AgGridReact<Row> ref={ref} theme={gridTheme} rowData={sorted} columnDefs={cols}
        getRowId={(p) => p.data.arm_id} isExternalFilterPresent={isExternalFilterPresent}
        doesExternalFilterPass={doesExternalFilterPass} tooltipShowDelay={200}
        defaultColDef={{ sortable: true, resizable: true }} />
    </div>
  );
}
