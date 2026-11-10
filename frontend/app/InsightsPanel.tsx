"use client";

import { useEffect, useMemo, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { CellStyle, ColDef } from "ag-grid-community";
import { askWhy, backfillLive, backfillSample, getCohorts, type Backfill, type Cohort, type WhyAnswer } from "./api-insights";
import { gridTheme, money, pct } from "./grid";

type CohortRow = Cohort & Record<string, unknown>;

// Retention heat: transparent at 0%, full accent at 100%, readable on the dark theme.
const heat = (v: number | undefined): CellStyle =>
  v === undefined ? {} : { backgroundColor: `rgba(38, 71, 216, ${0.06 + v * 0.7})`, ...(v > 0.5 ? { color: "#fff" } : {}) };

export default function InsightsPanel({ experimentId }: { experimentId: string | null }) {
  const [data, setData] = useState<Backfill | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [question, setQuestion] = useState("why did an arm pause?");
  const [why, setWhy] = useState<WhyAnswer | null>(null);

  useEffect(() => { getCohorts().then(setData).catch(() => { /* nothing imported yet */ }); }, []);

  async function run<T>(fn: () => Promise<T>, then: (v: T) => void) {
    setBusy(true); setError("");
    try { then(await fn()); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  const horizon = useMemo(() => Math.max(0, ...(data?.cohorts ?? []).map((c) => c.retention.length)), [data]);
  const cols = useMemo<ColDef<CohortRow>[]>(() => [
    { field: "cohort", headerName: "First paid", pinned: "left", width: 110 },
    { field: "size", headerName: "Customers", width: 105 },
    ...Array.from({ length: horizon }, (_, m): ColDef<CohortRow> => ({
      headerName: `M${m}`, width: 72, colId: `m${m}`,
      headerTooltip: `Share of the cohort still paying ${m} month(s) after their first payment`,
      valueGetter: (p) => p.data?.retention[m],
      valueFormatter: (p) => (p.value === undefined ? "" : pct(p.value, 0)),
      cellStyle: (p) => heat(p.value as number | undefined),
    })),
  ], [horizon]);

  const s = data?.summary;
  return (
    <>
      <section className="card">
        <h2>Merchant history <span className="sub">
          {data ? (data.source === "paypal" ? "imported from PayPal" : "synthetic sample merchant (labelled)") : "connect to fill this before the first experiment"}
        </span></h2>
        <div className="row">
          <button disabled={busy} onClick={() => run(() => backfillLive(12), setData)}>Import 12 months from PayPal</button>
          <button className="secondary" disabled={busy} onClick={() => run(() => backfillSample(12), setData)}>Load sample merchant</button>
        </div>
        {error && <p className="err">{error}</p>}
        {data && s && (
          <>
            <p className="sub" style={{ marginTop: 12 }}>
              {data.customers} customers · {s.payments} payments · avg monthly churn {pct(s.avg_monthly_churn ?? null)} ·
              {" "}refund rate {pct(s.refund_rate ?? null)} · ARPU last month {money(s.arpu_last_month ?? null)}
              {data.plans.length > 0 && ` · ${data.plans.length} PayPal plan(s)`}
            </p>
            <div style={{ height: Math.min(420, 56 + 42 * data.cohorts.length) }}>
              <AgGridReact<CohortRow> theme={gridTheme} rowData={data.cohorts as CohortRow[]} columnDefs={cols}
                defaultColDef={{ sortable: false, resizable: true }} />
            </div>
          </>
        )}
      </section>

      <section className="card">
        <h2>Ask why <span className="sub">every agent decision is logged and searchable{why ? ` (${why.engine === "elastic" ? "Elastic" : "local"} search)` : ""}</span></h2>
        <div className="row">
          <input style={{ flex: 1, minWidth: 240 }} value={question} onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run(() => askWhy(question, experimentId), setWhy)} />
          <button disabled={busy || question.trim().length < 3} onClick={() => run(() => askWhy(question, experimentId), setWhy)}>Ask</button>
        </div>
        {why && (
          <>
            <p>{why.answer}</p>
            {why.matches.length > 0 && (
              <table>
                <thead><tr><th>When</th><th>Agent</th><th>Decision</th><th>Reason</th></tr></thead>
                <tbody>
                  {why.matches.map((d) => (
                    <tr key={d.id}><td>{d.ts.slice(0, 10)}</td><td>{d.kind}</td><td>{d.summary}</td><td className="small">{d.reason}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </section>
    </>
  );
}
