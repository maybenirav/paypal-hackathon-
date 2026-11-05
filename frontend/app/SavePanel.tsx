"use client";

import { useState } from "react";
import { getSaveLog, overrideSave, saveSweep, type SaveLog } from "./api";

const KINDS = ["pause", "downgrade", "discount", "let_go"] as const;

export default function SavePanel() {
  const [log, setLog] = useState<SaveLog | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function run(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); setLog(await getSaveLog()); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <section className="card">
      <h2>Save Agent <span className="sub">(dry run: requests are logged, not sent to PayPal)</span></h2>
      <div className="row">
        <button disabled={busy} onClick={() => run(saveSweep)}>Sweep failed payments</button>
        <button disabled={busy} onClick={() => run(async () => {})}>Refresh log</button>
        {log && <span className="sub">{log.summary.saves} saved · {log.summary.let_go} let go · {log.summary.overridden} overridden</span>}
      </div>
      {error && <p className="err">{error}</p>}
      {log && (
        <table>
          <thead><tr><th>Subscriber</th><th>Trigger</th><th>Offer</th><th>Why</th><th>Request</th><th>Status</th><th>Override</th></tr></thead>
          <tbody>
            {log.actions.slice(0, 15).map((a) => (
              <tr key={a.id}>
                <td>{a.subscriber_id}</td><td>{a.trigger}</td><td>{a.kind}</td><td>{a.rationale}</td>
                <td><code>{a.paypal_request.method} {a.paypal_request.path}</code></td>
                <td><span className={`badge ${a.status === "overridden" ? "bad" : a.status === "failed" ? "bad" : "ok"}`}>{a.status}</span></td>
                <td>{a.status !== "overridden" && !a.overridden_by && (
                  <select disabled={busy} defaultValue="" onChange={(e) => e.target.value && run(() => overrideSave(a.id, e.target.value, "merchant override"))}>
                    <option value="">override…</option>
                    {KINDS.filter((k) => k !== a.kind).map((k) => <option key={k} value={k}>{k}</option>)}
                  </select>
                )}{a.overridden_by && <span className="sub">override of {a.overridden_by}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
