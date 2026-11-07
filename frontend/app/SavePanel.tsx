"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef, ICellRendererParams } from "ag-grid-community";
import {
  cancelIntent, getSaveLog, overrideSave, saveSweep, setupSavePlans,
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
      valueFormatter: (p) => `${p.value}${p.data?.reason_source === "gemini" ? " · AI" : ""}` },
    { field: "kind", headerName: "Offer", width: 105, valueFormatter: (p) => LABEL[p.value] ?? p.value },
    { field: "message", headerName: "What the customer sees", flex: 1.4, minWidth: 220, wrapText: true, autoHeight: true },
    { field: "status", headerName: "PayPal", width: 150,
      cellRenderer: (p: ICellRendererParams<SaveAction>) => p.data?.approve_url
        ? <a href={p.data.approve_url} target="_blank" rel="noreferrer">awaiting buyer ✓</a>
        : <span className={`badge ${["failed", "overridden"].includes(p.value) ? "bad" : "ok"}`}>{p.value}</span> },
    { headerName: "Override", width: 130, sortable: false,
      cellRenderer: (p: ICellRendererParams<SaveAction>) => {
        const a = p.data!;
        if (a.status === "overridden") return <span className="sub">overridden</span>;
        return (
          <select disabled={busy} defaultValue="" onChange={(e) => e.target.value &&
            run(() => overrideSave(a.id, e.target.value, "merchant override"))}>
            <option value="">override…</option>
            {KINDS.filter((k) => k !== a.kind).map((k) => <option key={k} value={k}>{LABEL[k]}</option>)}
          </select>
        );
      } },
  ], [busy]); // eslint-disable-line react-hooks/exhaustive-deps

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
          <b>{LABEL[last.kind]}</b> · understood as “{last.reason_hint}”{last.reason_source === "gemini" && " by Gemini"} ·{" "}
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
      <div style={{ height: 320, marginTop: 12 }}>
        <AgGridReact<SaveAction> theme={gridTheme} rowData={log?.actions ?? []} columnDefs={cols}
          getRowId={(p) => p.data.id} pagination paginationPageSize={20} paginationPageSizeSelector={false}
          overlayNoRowsTemplate="No cancel attempts yet. Run the sweep or click cancel on a real subscription." />
      </div>
    </section>
  );
}
