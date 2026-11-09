"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { CellValueChangedEvent, ColDef, ICellRendererParams, IRowNode, RowClassRules } from "ag-grid-community";
import {
  LLM_LABEL, cancelIntent, getSaveLog, overrideSave, saveSweep, setupSavePlans,
  type SaveAction, type SaveLog,
} from "./api";
import { gridTheme, money } from "./grid";

const KINDS = ["pause", "downgrade", "discount", "let_go"] as const;
const LABEL: Record<string, string> = { pause: "Pause", downgrade: "Downgrade", discount: "Discount", let_go: "Let go" };

export default function SavePanel({ experimentId, live, onChange }: {
  experimentId: string | null; live: boolean; onChange?: () => void;
}) {
  const [log, setLog] = useState<SaveLog | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [subId, setSubId] = useState("");
  const [reason, setReason] = useState("Honestly it's a bit too expensive for me right now");
  const [last, setLast] = useState<SaveAction | null>(null);
  const [search, setSearch] = useState("");
  const [realOnly, setRealOnly] = useState(false);
  const grid = useRef<AgGridReact<SaveAction>>(null);

  const refresh = useCallback(async () => setLog(await getSaveLog()), []);
  useEffect(() => { refresh().catch(() => {}); }, [refresh]);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); await refresh(); onChange?.(); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  const cols = useMemo<ColDef<SaveAction>[]>(() => [
    { field: "ts", headerName: "When", width: 110, valueFormatter: (p) => p.value?.slice(0, 10) },
    { field: "simulated", headerName: "Source", width: 105,
      cellRenderer: (p: ICellRendererParams<SaveAction>) =>
        <span className={`badge ${p.value ? "sim" : "real"}`}>{p.value ? "simulated" : "real PayPal"}</span> },
    { field: "subscriber_id", headerName: "Subscriber", width: 150 },
    { field: "reason_text", headerName: "Customer said", flex: 1, minWidth: 170,
      valueFormatter: (p) => p.value ?? (p.data?.trigger === "PAYMENT_FAILED" ? "(card payment failed)" : "–") },
    { field: "reason_hint", headerName: "Understood as", width: 125,
      valueFormatter: (p) => `${p.value}${p.data && LLM_LABEL[p.data.reason_source] ? " · AI" : ""}` },
    { field: "kind", headerName: "Offer (click to override)", width: 175, refData: LABEL,
      cellEditor: "agSelectCellEditor", cellEditorParams: { values: [...KINDS] }, singleClickEdit: true,
      editable: (p) => !busy && p.data?.status !== "overridden",
      tooltipValueGetter: (p) => p.data?.status === "overridden"
        ? `Overridden by the merchant: ${p.data.override_note || "no note"}` : "Click to replace the agent's offer",
      cellRenderer: (p: ICellRendererParams<SaveAction>) => (
        <span className={p.data?.status === "overridden" ? "struck" : "editable-cell"}>
          {LABEL[p.value] ?? p.value}{p.data?.overridden_by && <span className="badge real small" style={{ marginLeft: 6 }}>merchant</span>}
        </span>) },
    { field: "message", headerName: "What the customer sees", flex: 1.4, minWidth: 220, wrapText: true, autoHeight: true,
      cellStyle: { lineHeight: "1.45", paddingTop: 8, paddingBottom: 8 } },
    { field: "status", headerName: "PayPal", width: 150,
      cellRenderer: (p: ICellRendererParams<SaveAction>) => p.data?.approve_url
        ? <a href={p.data.approve_url} target="_blank" rel="noreferrer">awaiting buyer ✓</a>
        : <span className={`badge ${["failed", "overridden"].includes(p.value) ? "bad" : "ok"}`}>{p.value}</span> },
  ], [busy]); // eslint-disable-line react-hooks/exhaustive-deps

  // The merchant overrides the agent by editing the Offer cell; the backend logs a new action
  // (and makes the PayPal call) and marks the original as overridden.
  const onCellValueChanged = (e: CellValueChangedEvent<SaveAction>) => {
    if (e.colDef.field !== "kind" || !e.data || e.newValue === e.oldValue) return;
    run(() => overrideSave(e.data!.id, e.newValue, "merchant override (edited in grid)"));
  };

  const rowClassRules = useMemo<RowClassRules<SaveAction>>(() => ({
    "row-stopped": (p) => p.data?.status === "overridden",
    "row-real": (p) => p.data?.simulated === false,
  }), []);
  const isExternalFilterPresent = useCallback(() => realOnly, [realOnly]);
  const doesExternalFilterPass = useCallback((n: IRowNode<SaveAction>) => !n.data?.simulated, []);
  useEffect(() => { grid.current?.api?.onFilterChanged(); }, [realOnly]);
  const realCount = log?.actions.filter((a) => !a.simulated).length ?? 0;

  return (
    <section className="card hero">
      <h2>Save Agent <span className="sub">keeps customers who try to cancel · works from the first customer, no sample size needed</span></h2>
      <div className="kpis">
        <div><b>{log?.summary.saves ?? 0}</b><span>customers kept</span></div>
        <div><b>{money(log?.summary.mrr_protected ?? 0)}</b><span>monthly revenue protected</span></div>
        <div><b>{log?.summary.let_go ?? 0}</b><span>let go</span></div>
        <div><b>{log?.summary.overridden ?? 0}</b><span>merchant overrides</span></div>
      </div>
      <p className="note">
        The customer&apos;s reason is read by AI. The offer is picked by your rules. Every offer is a real PayPal call: a pause
        suspends now and reactivates later, and a downgrade or discount switches to a plan the customer approves on PayPal.
        {live ? " Live mode: requests go to the PayPal sandbox." : " Dry-run mode: requests are logged, not sent."}
      </p>

      <div className="row">
        <label style={{ flex: "0 0 220px" }}>Real sandbox subscription id
          <input placeholder="I-XXXXXXXXXXXX" value={subId} onChange={(e) => setSubId(e.target.value.trim())} />
        </label>
        <label style={{ flex: 1, minWidth: 220 }}>Why are you cancelling?
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        <button disabled={busy || !subId} onClick={() => run(async () => setLast(await cancelIntent(subId, reason)))}>
          Customer clicks cancel
        </button>
      </div>
      {last && (
        <div className="callout">
          <b>{LABEL[last.kind]}</b> · understood as “{last.reason_hint}”{LLM_LABEL[last.reason_source] && ` by ${LLM_LABEL[last.reason_source]}`} ·{" "}
          {last.message}{" "}
          {last.approve_url && <a href={last.approve_url} target="_blank" rel="noreferrer">Customer confirms on PayPal →</a>}
        </div>
      )}
      <div className="row">
        <button className="ghost" disabled={busy} onClick={() => run(saveSweep)}>Run nightly sweep</button>
        <button className="ghost" disabled={busy || !experimentId}
          onClick={() => run(() => setupSavePlans(experimentId!))}>Create Lite + discount plans in PayPal</button>
      </div>
      {error && <p className="err">{error}</p>}
      <div className="grid-bar" style={{ marginTop: 12 }}>
        <input className="grid-search" placeholder="Search the log: subscriber, reason, offer…" value={search}
          onChange={(e) => setSearch(e.target.value)} />
        <span className="grid-actions">
          <label className="toggle"><input type="checkbox" checked={realOnly} onChange={(e) => setRealOnly(e.target.checked)} />
            Real PayPal only ({realCount})</label>
          <button className="ghost small" onClick={() => grid.current?.api?.exportDataAsCsv({ fileName: "save-agent-log.csv" })}>Export CSV</button>
        </span>
      </div>
      <div style={{ height: 340 }}>
        <AgGridReact<SaveAction> ref={grid} theme={gridTheme} rowData={log?.actions ?? []} columnDefs={cols}
          getRowId={(p) => p.data.id} pagination paginationPageSize={20} paginationPageSizeSelector={false}
          quickFilterText={search} isExternalFilterPresent={isExternalFilterPresent}
          doesExternalFilterPass={doesExternalFilterPass} rowClassRules={rowClassRules}
          onCellValueChanged={onCellValueChanged} tooltipShowDelay={200}
          initialState={{ sort: { sortModel: [{ colId: "ts", sort: "desc" }] } }}
          defaultColDef={{ sortable: true, resizable: true, filter: true }}
          overlayNoRowsTemplate="No cancel attempts yet. Run the sweep or click cancel on a real subscription." />
      </div>
    </section>
  );
}
