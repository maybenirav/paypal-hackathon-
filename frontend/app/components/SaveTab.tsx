"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef, ICellRendererParams } from "ag-grid-community";
import * as api from "../api";
import type { OfferKind, SaveAction, SaveLog, SaveRules } from "../api";
import { Icon, Pill } from "../icons";
import { gridTheme } from "./gridSetup";

const OFFERS: Record<OfferKind, string> = { pause: "Pause", downgrade: "Downgrade", discount: "Discount", let_go: "Let go" };
const TRIGGERS: Record<string, string> = { PAYMENT_FAILED: "Failed payment", CANCEL_INTENT: "Tried to cancel" };

type Context = { busy: boolean; canDowngrade: boolean; onOverride: (id: string, kind: OfferKind) => void };
type Cell = ICellRendererParams<SaveAction, unknown, Context>;

function WrapCell({ value }: Cell) {
  return <div className="wrap-cell">{String(value ?? "")}</div>;
}

const LINE_HEIGHT = 18.2;
const ROW_PADDING = 20;

const requestText = (action: SaveAction) => {
  const { method, path } = action.paypal_request;
  return method === "NONE" ? "No PayPal call. PayPal keeps retrying the payment." : `${method} ${path}`;
};

/** Row height from the text itself, using characters per line at each column's narrowest width. */
function rowHeight(action: SaveAction | undefined) {
  if (!action) return 46;
  const lines = Math.max(
    Math.ceil(action.subscriber_id.length / 17),
    Math.ceil(action.rationale.length / 30),
    Math.ceil(requestText(action).length / 28),
  );
  return Math.ceil(lines * LINE_HEIGHT + ROW_PADDING);
}

function StatusCell({ data }: Cell) {
  if (!data) return null;
  if (data.status === "failed") return <Pill kind="crit" icon="alert">Failed</Pill>;
  if (data.status === "overridden") return <Pill kind="neutral">Overridden</Pill>;
  if (data.status === "dry_run") return <Pill kind="warn">Dry run</Pill>;
  if (data.status === "no_call") return <Pill kind="neutral">No call</Pill>;
  return <Pill kind="good">Sent</Pill>;
}

function OverrideCell({ data, context }: Cell) {
  if (!data) return null;
  if (data.overridden_by) return <span className="muted">Your change</span>;
  if (data.status === "overridden") return null;
  return (
    <select className="input small" disabled={context.busy} value="" aria-label={`Change the offer for ${data.subscriber_id}`}
      onChange={(e) => e.target.value && context.onOverride(data.id, e.target.value as OfferKind)}>
      <option value="">Change offer…</option>
      {(Object.keys(OFFERS) as OfferKind[])
        .filter((kind) => kind !== data.kind && (kind !== "downgrade" || context.canDowngrade))
        .map((kind) => <option key={kind} value={kind}>{OFFERS[kind]}</option>)}
    </select>
  );
}

export default function SaveTab({ expId, onError }: { expId: string; onError: (error: unknown) => void }) {
  const [log, setLog] = useState<SaveLog | null>(null);
  const [rules, setRules] = useState<SaveRules | null>(null);
  const [saved, setSaved] = useState<SaveRules | null>(null);
  const [busy, setBusy] = useState(false);
  const [sweepResult, setSweepResult] = useState<number | null>(null);

  const load = useCallback(async () => setLog(await api.getSaveLog(expId)), [expId]);

  useEffect(() => {
    (async () => {
      try {
        const [nextLog, nextRules] = await Promise.all([api.getSaveLog(expId), api.getSaveRules()]);
        setLog(nextLog);
        setRules(nextRules);
        setSaved(nextRules);
      } catch (error) {
        onError(error);
      }
    })();
  }, [expId, onError]);

  const run = useCallback(async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
    } catch (error) {
      onError(error);
    } finally {
      setBusy(false);
    }
  }, [onError]);

  const sweep = () => run(async () => {
    const result = await api.saveSweep(expId);
    setSweepResult(result.handled);
    await load();
  });

  const onOverride = useCallback((id: string, kind: OfferKind) => run(async () => {
    await api.overrideSave(id, kind);
    await load();
  }), [load, run]);

  function submitRules(event: FormEvent) {
    event.preventDefault();
    if (!rules) return;
    void run(async () => {
      const stored = await api.putSaveRules({ ...rules, downgrade_plan_id: rules.downgrade_plan_id?.trim() || null });
      setRules(stored);
      setSaved(stored);
    });
  }

  const columns = useMemo<ColDef<SaveAction>[]>(() => [
    { headerName: "Subscriber", field: "subscriber_id", minWidth: 150, cellRenderer: WrapCell, cellClass: "cell-mono" },
    { headerName: "Trigger", field: "trigger", minWidth: 124, valueFormatter: (p) => TRIGGERS[p.value as string] ?? String(p.value) },
    { headerName: "Offer", field: "kind", minWidth: 84, valueFormatter: (p) => OFFERS[p.value as OfferKind] ?? String(p.value) },
    { headerName: "Why", field: "rationale", minWidth: 250, cellRenderer: WrapCell, sortable: false },
    { headerName: "PayPal request", minWidth: 240, cellRenderer: WrapCell, cellClass: "cell-mono", sortable: false,
      valueGetter: (p) => (p.data ? requestText(p.data) : "") },
    { headerName: "Status", field: "status", cellRenderer: StatusCell, minWidth: 104 },
    { headerName: "Override", cellRenderer: OverrideCell, minWidth: 146, sortable: false },
  ], []);

  const summary = log?.summary;
  const rulesDirty = JSON.stringify(rules) !== JSON.stringify(saved);

  return (
    <div className="stack">
      <section className="panel source-panel">
        <div>
          <h3>Keep subscribers who are about to leave</h3>
          <p className="muted">The Save Agent picks an offer by fixed rules whenever a payment fails or someone tries to cancel. This is a dry run: requests are logged, not sent to PayPal.</p>
        </div>
        <div className="btn-row">
          <button className="btn primary" disabled={busy} onClick={() => void sweep()}>{busy ? "Working…" : "Sweep for at-risk subscribers"}</button>
          {sweepResult !== null && <span className="hint" role="status">{sweepResult === 0 ? "Nothing new to handle." : `Handled ${sweepResult} subscriber${sweepResult === 1 ? "" : "s"}.`}</span>}
        </div>
      </section>

      {summary && (
        <div className="tiles">
          <div className="tile"><span className="tile-label">Interventions</span><strong className="tile-value">{summary.total}</strong></div>
          <div className="tile"><span className="tile-label">Offers made</span><strong className="tile-value">{summary.saves}</strong></div>
          <div className="tile"><span className="tile-label">Let go</span><strong className="tile-value">{summary.let_go}</strong></div>
          <div className="tile"><span className="tile-label">Overridden by you</span><strong className="tile-value">{summary.overridden}</strong></div>
        </div>
      )}

      <section className="panel panel-flush">
        <div className="panel-head"><h3>Audit log</h3></div>
        {log && log.actions.length > 0 ? (
          <div className="grid-wrap">
            <AgGridReact<SaveAction>
              theme={gridTheme}
              rowData={log.actions}
              columnDefs={columns}
              context={{ busy, canDowngrade: !!saved?.downgrade_plan_id, onOverride } satisfies Context}
              getRowId={(p) => p.data.id}
              getRowHeight={(p) => rowHeight(p.data)}
              defaultColDef={{ resizable: true, suppressMovable: true, sortable: true }}
              autoSizeStrategy={{ type: "fitGridWidth" }}
              domLayout="autoHeight"
              pagination={log.actions.length > 10}
              paginationPageSize={10}
              paginationPageSizeSelector={false}
              suppressCellFocus
              enableCellTextSelection
            />
          </div>
        ) : (
          <p className="muted panel-pad">No interventions yet. Simulate an experiment, then sweep for at-risk subscribers.</p>
        )}
      </section>

      {rules && (
        <form className="panel guard-form" onSubmit={submitRules}>
          <h3>What the agent may offer</h3>
          <div className="toggles">
            {([["allow_pause", "Pause the subscription"], ["allow_discount", "Offer a discount"], ["allow_downgrade", "Move to a cheaper plan"]] as const).map(([key, label]) => (
              <label className="toggle" key={key}>
                <input type="checkbox" checked={rules[key]} onChange={(e) => setRules({ ...rules, [key]: e.target.checked })} />
                {label}
              </label>
            ))}
          </div>
          <div className="guard-fields">
            <label className="field"><span>Largest discount</span>
              <span className="input-suffix"><input className="input mono" type="number" min="1" max="50" value={rules.max_discount_pct} onChange={(e) => setRules({ ...rules, max_discount_pct: Number(e.target.value) })} /><em>%</em></span></label>
            <label className="field"><span>Discount lasts</span>
              <span className="input-suffix"><input className="input mono" type="number" min="1" max="12" value={rules.discount_cycles} onChange={(e) => setRules({ ...rules, discount_cycles: Number(e.target.value) })} /><em>cycles</em></span></label>
            <label className="field"><span>Pause length</span>
              <span className="input-suffix"><input className="input mono" type="number" min="1" max="90" value={rules.pause_days} onChange={(e) => setRules({ ...rules, pause_days: Number(e.target.value) })} /><em>days</em></span></label>
            <label className="field"><span>Offers per subscriber</span>
              <input className="input mono" type="number" min="0" max="5" value={rules.max_saves_per_subscriber} onChange={(e) => setRules({ ...rules, max_saves_per_subscriber: Number(e.target.value) })} /></label>
            <label className="field"><span>Plan ID for downgrades</span>
              <input className="input mono" placeholder="P-…" value={rules.downgrade_plan_id ?? ""} onChange={(e) => setRules({ ...rules, downgrade_plan_id: e.target.value })} />
              <small>Downgrades stay off until this is set</small></label>
          </div>
          <div className="btn-row">
            <button className="btn primary" disabled={busy || !rulesDirty}>Save rules</button>
            {!rulesDirty && saved && <span className="hint"><Icon name="check" size={14} /> Rules in effect</span>}
          </div>
        </form>
      )}
    </div>
  );
}
