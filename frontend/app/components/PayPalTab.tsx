"use client";

import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import { LAST_EXP_KEY, type Experiment, type PayPalHealth, type Subscription, type Transaction } from "../api";

import { money, priceLabel } from "../format";
import { Icon, Pill } from "../icons";

type Props = {
  exp: Experiment;
  onCheckHealth: () => Promise<PayPalHealth | null>;
  onDataChanged: () => Promise<void>;
  onError: (error: unknown) => void;
};

const TRANSACTION_STATUS: Record<string, string> = { S: "Completed", P: "Pending", D: "Denied", V: "Reversed" };
const ENDED = new Set(["CANCELLED", "EXPIRED"]);

export default function PayPalTab({ exp, onCheckHealth, onDataChanged, onError }: Props) {
  const [health, setHealth] = useState<PayPalHealth | null>(null);
  const [checking, setChecking] = useState(true);
  const [busy, setBusy] = useState(false);
  const [armChoice, setArmChoice] = useState("");
  const [customer, setCustomer] = useState("");
  const [subs, setSubs] = useState<Subscription[]>([]);
  const [transactions, setTransactions] = useState<Transaction[] | null>(null);

  const check = useCallback(async () => {
    setChecking(true);
    setHealth(await onCheckHealth());
    setChecking(false);
  }, [onCheckHealth]);

  const loadSubs = useCallback(async () => {
    setSubs((await api.listSubscriptions(exp.id)).subscriptions);
  }, [exp.id]);

  useEffect(() => {
    void check();
    loadSubs().catch(onError);
  }, [check, loadSubs, onError]);

  const connected = health?.paypal === "connected";
  const published = exp.arms.every((a) => a.plan_id);
  const invalidPlans = exp.validation.filter((v) => !v.valid).map((v) => v.arm_id);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    try {
      await action();
    } catch (error) {
      onError(error);
    } finally {
      setBusy(false);
    }
  }

  const publish = () => run(async () => {
    await api.publishPlans(exp.id);
    await onDataChanged();
  });

  const createSubscription = () => run(async () => {
    await api.subscribe(exp.id, armChoice || undefined, customer.trim() || undefined);
    // Lets the page PayPal returns the buyer to find this experiment
    try { localStorage.setItem(LAST_EXP_KEY, exp.id); } catch { /* that page then offers Sync instead */ }
    await Promise.all([loadSubs(), onDataChanged()]);
  });

  const sync = (id: string) => run(async () => {
    await api.syncSubscription(exp.id, id);
    await Promise.all([loadSubs(), onDataChanged()]);
  });

  const cancel = (id: string) => {
    if (!window.confirm(`Cancel sandbox subscription ${id}? This can't be undone.`)) return;
    return run(async () => {
      await api.cancelSubscription(id);
      await api.syncSubscription(exp.id, id);
      await Promise.all([loadSubs(), onDataChanged()]);
    });
  };

  const loadTransactions = () => run(async () => {
    setTransactions((await api.getTransactions(exp.id)).transactions);
  });

  return (
    <div className="stack">
      <section className="panel paypal-status">
        <div className="paypal-status-head">
          <h3>Connection</h3>
          {checking ? <Pill kind="neutral">Checking…</Pill>
            : connected ? <Pill kind="good">Connected to {health.env}</Pill>
              : <Pill kind="warn" icon="alert">Not connected</Pill>}
          <button className="btn small" disabled={checking} onClick={() => void check()}><Icon name="refresh" size={14} /> Check again</button>
        </div>

        {!checking && !connected && (
          <div className="setup">
            <p>Simulations work without PayPal. To run real sandbox sign-ups:</p>
            <ol>
              <li>Create a sandbox app at <a href="https://developer.paypal.com/dashboard/applications/sandbox" target="_blank" rel="noreferrer">developer.paypal.com</a>, then copy its keys into <code>backend/.env</code> as <code>PAYPAL_CLIENT_ID</code> and <code>PAYPAL_CLIENT_SECRET</code>.</li>
              <li>From <code>backend</code>, run <code>python scripts/create_product.py</code> and add the <code>PAYPAL_PRODUCT_ID</code> it prints.</li>
              <li>Restart the backend and check again.</li>
            </ol>
            {health?.paypal === "error" && <p className="setup-detail">Backend said: {health.detail}</p>}
          </div>
        )}
        {connected && !health.product_id_set && (
          <p className="field-error">PAYPAL_PRODUCT_ID is missing. From <code>backend</code>, run <code>python scripts/create_product.py</code> and add the id it prints to <code>.env</code>.</p>
        )}
      </section>

      <section className="panel panel-flush">
        <div className="panel-head">
          <h3>Billing plans</h3>
          <button className="btn primary" disabled={!connected || busy || published || invalidPlans.length > 0} onClick={() => void publish()}>
            {published ? "All plans published" : busy ? "Working…" : `Publish ${exp.arms.length} plans to PayPal`}
          </button>
        </div>
        {invalidPlans.length > 0 && <p className="field-error panel-pad">Fix these plans before publishing: {invalidPlans.join(", ")}.</p>}
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th scope="col">Price option</th><th scope="col" className="num">Price</th><th scope="col">PayPal plan</th></tr></thead>
            <tbody>
              {exp.arms.map((arm) => (
                <tr key={arm.arm_id}>
                  <td>{arm.name}{arm.is_baseline && <em className="tag">Baseline</em>}</td>
                  <td className="num">{priceLabel(arm.price, arm.interval, arm.currency)}</td>
                  <td>{arm.plan_id ? <code>{arm.plan_id}</code> : <span className="muted">Not published</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel panel-flush">
        <div className="panel-head">
          <h3>Sandbox sign-ups</h3>
          <div className="btn-row">
            <input className="input signup-customer" value={customer} maxLength={128} onChange={(e) => setCustomer(e.target.value)}
              placeholder="Customer ID (optional)" aria-label="Customer ID, so a returning customer keeps the same offer"
              title="A returning customer with the same ID keeps the same offer" />
            <select className="input" value={armChoice} aria-label="Which price option to sign up to" onChange={(e) => setArmChoice(e.target.value)}>
              <option value="">Let PlanLab choose the option</option>
              {exp.arms.filter((arm) => !exp.stopped_arms.includes(arm.arm_id)).map((arm) => <option key={arm.arm_id} value={arm.arm_id}>{arm.name}</option>)}
            </select>
            <button className="btn" disabled={!connected || !published || busy} onClick={() => void createSubscription()}>Create subscription</button>
          </div>
        </div>
        {subs.length === 0 ? (
          <p className="muted panel-pad">{published ? "No sign-ups yet. Create a subscription, then finish it in PayPal's checkout with a sandbox buyer." : "Publish the plans first."}</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th scope="col">Subscription</th><th scope="col">Price option</th><th scope="col">Status</th><th scope="col">Actions</th></tr></thead>
              <tbody>
                {subs.map((sub) => (
                  <tr key={sub.subscription_id}>
                    <td><code>{sub.subscription_id}</code></td>
                    <td>{exp.arms.find((a) => a.arm_id === sub.arm_id)?.name ?? sub.arm_id}</td>
                    <td>{sub.status}</td>
                    <td className="row-actions">
                      {sub.approve_url && sub.status !== "ACTIVE" && !ENDED.has(sub.status) && (
                        <a className="btn small" href={sub.approve_url} target="_blank" rel="noreferrer">Open checkout <Icon name="external" size={14} /></a>
                      )}
                      <button className="btn small" disabled={busy} onClick={() => void sync(sub.subscription_id)}>Sync</button>
                      {!ENDED.has(sub.status) && (
                        <button className="btn small" disabled={busy} onClick={() => void cancel(sub.subscription_id)}>Cancel</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel panel-flush">
        <div className="panel-head">
          <h3>PayPal transactions, last 30 days</h3>
          <button className="btn" disabled={!connected || busy} onClick={() => void loadTransactions()}>{transactions ? "Refresh" : "Load transactions"}</button>
        </div>
        {transactions === null ? (
          <p className="muted panel-pad">PayPal can take a few hours to list new transactions here.</p>
        ) : transactions.length === 0 ? (
          <p className="muted panel-pad">No transactions for this experiment yet.</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th scope="col">Date</th><th scope="col">Price option</th><th scope="col">Status</th><th scope="col" className="num">Amount</th><th scope="col" className="num">Fee</th></tr></thead>
              <tbody>
                {transactions.map((t) => (
                  <tr key={t.transaction_id}>
                    <td>{t.date?.slice(0, 10)}</td>
                    <td>{exp.arms.find((a) => a.arm_id === t.arm_id)?.name ?? t.arm_id}</td>
                    <td>{TRANSACTION_STATUS[t.status] ?? t.status}</td>
                    <td className="num">{money(t.amount, t.currency, 2)}</td>
                    <td className="num">{money(t.fee, t.currency, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
