"use client";

import { useMemo } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef, ICellRendererParams, RowClassParams, ValueFormatterParams } from "ag-grid-community";
import type { Arm, Guardrails, Row } from "../api";
import { int, money, pct, priceLabel } from "../format";
import { useWidth } from "../hooks";
import { Pill } from "../icons";
import { gridTheme } from "./gridSetup";

type Context = { colorOf: (armId: string) => string; breached: Set<string> };
type Cell = ICellRendererParams<Row, unknown, Context>;

function NameCell({ data, context }: Cell) {
  if (!data) return null;
  return (
    <span className="grid-name">
      <i className="dot" style={{ background: context.colorOf(data.arm_id) }} />
      <span className="grid-name-text">{data.name}</span>
      {data.is_baseline && <em className="tag">Baseline</em>}
    </span>
  );
}

function ConversionCell({ data }: Cell) {
  if (!data) return null;
  return <span>{pct(data.conversion)} <small className="grid-sub">{pct(data.conv_ci[0], 0)}–{pct(data.conv_ci[1], 0)}</small></span>;
}

function StatusCell({ data, context }: Cell) {
  if (!data) return null;
  if (data.status === "winner") return <Pill kind="good">Winner</Pill>;
  if (data.status === "stopped") return <Pill kind="crit">Stopped</Pill>;
  if (context.breached.has(data.arm_id)) return <Pill kind="warn">Breached</Pill>;
  return <Pill kind="note">Running</Pill>;
}

const number = (p: ValueFormatterParams<Row, number>) => (p.value == null ? "–" : int(p.value));
const percent = (digits: number) => (p: ValueFormatterParams<Row, number>) => (p.value == null ? "–" : pct(p.value, digits));

type Props = {
  rows: Row[];
  arms: Arm[];
  guardrails: Guardrails;
  colorOf: (armId: string) => string;
};

export default function ArmsGrid({ rows, arms, guardrails, colorOf }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const currency = arms[0]?.currency ?? "USD";
  const compact = width > 0 && width < 760;
  const breached = useMemo(() => new Set(guardrails.arms.filter((a) => !a.ok).map((a) => a.arm_id)), [guardrails]);

  const columns = useMemo<ColDef<Row>[]>(() => [
    { headerName: "Price option", field: "name", cellRenderer: NameCell, minWidth: compact ? 170 : 214, pinned: compact ? undefined : "left" },
    { headerName: "Status", field: "status", cellRenderer: StatusCell, minWidth: 106 },
    { headerName: "Price", minWidth: 80, valueGetter: (p) => (p.data ? priceLabel(p.data.price, p.data.interval, currency) : ""), cellClass: "cell-num" },
    { headerName: "Visitors", field: "visitors", minWidth: 66, valueFormatter: number, type: "rightAligned", cellClass: "cell-num" },
    { headerName: "Sign-ups", field: "signups", minWidth: 70, valueFormatter: number, type: "rightAligned", cellClass: "cell-num" },
    { headerName: "Conversion", field: "conversion", cellRenderer: ConversionCell, minWidth: 126, cellClass: "cell-num", headerTooltip: "Sign-ups divided by visitors, with the 95% range for the true rate" },
    { headerName: "Churn / mo", field: "monthly_churn", minWidth: 62, valueFormatter: percent(1), type: "rightAligned", cellClass: "cell-num", headerTooltip: "Cancellations per month of subscriber time, so recent sign-ups don't flatter the number" },
    { headerName: "12-mo value", field: "ltv", minWidth: 70, valueFormatter: (p) => (p.value == null ? "–" : money(p.value, currency, 2)), type: "rightAligned", cellClass: "cell-num", headerTooltip: "Expected discounted revenue per subscriber over 12 months, following the plan's payment schedule" },
    { headerName: "Value / visitor", field: "value_per_visitor", minWidth: 70, valueFormatter: (p) => (p.value == null ? "–" : money(p.value, currency, 2)), type: "rightAligned", cellClass: "cell-num cell-strong", headerTooltip: "Conversion × 12-month value. This is what decides the test" },
    { headerName: "Chance best", field: "prob_best", minWidth: 66, valueFormatter: percent(1), type: "rightAligned", cellClass: "cell-num", headerTooltip: "Probability this option is worth the most per visitor" },
    { headerName: "Traffic", field: "allocation", minWidth: 58, valueFormatter: percent(0), type: "rightAligned", cellClass: "cell-num", headerTooltip: "Share of new customers this option gets now. Follows the chance of being best, and the current price always keeps at least 30%" },
    { headerName: "MRR", field: "mrr", minWidth: 62, valueFormatter: (p) => (p.value == null ? "–" : money(p.value, currency)), type: "rightAligned", cellClass: "cell-num", headerTooltip: "Monthly recurring revenue from subscribers who have paid, so unpaid free trials don't count" },
    { headerName: "Z", field: "z", minWidth: 48, valueFormatter: (p) => (p.value == null ? "–" : p.value.toFixed(2)), type: "rightAligned", cellClass: "cell-num", headerTooltip: "Z-score against the baseline. 1.96 or more is statistically significant" },
  ], [compact, currency]);

  return (
    <div className="grid-wrap" ref={ref}>
      <AgGridReact<Row>
        theme={gridTheme}
        rowData={rows}
        columnDefs={columns}
        context={{ colorOf, breached } satisfies Context}
        getRowId={(p) => p.data.arm_id}
        getRowClass={(p: RowClassParams<Row>) => (p.data?.status === "stopped" ? "row-stopped" : undefined)}
        defaultColDef={{ sortable: true, resizable: true, suppressMovable: true, wrapHeaderText: true, autoHeaderHeight: true }}
        autoSizeStrategy={{ type: "fitGridWidth" }}
        domLayout="autoHeight"
        suppressCellFocus
        enableCellTextSelection
        tooltipShowDelay={200}
        overlayNoRowsTemplate="No price options match that filter."
      />
    </div>
  );
}
