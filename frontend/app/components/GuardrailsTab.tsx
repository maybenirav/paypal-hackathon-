"use client";

import { useEffect, useState, type FormEvent } from "react";
import * as api from "../api";
import type { AutoChecks, GuardrailArm, GuardrailConfig, Guardrails, Row, Stop, StopAlert } from "../api";
import { localTime, pct } from "../format";
import { Icon, Pill } from "../icons";

type Props = {
  expId: string;
  guardrails: Guardrails;
  rows: Row[];
  stoppedArms: string[];
  stops: Stop[];
  autoChecks: AutoChecks;
  simulated: boolean;
  rolledOut: boolean;
  busy: boolean;
  nameOfArm: (armId: string) => string;
  explain: (text: string) => string;
  onResume: (armId: string) => void;
  onSaved: () => Promise<void>;
  onError: (error: unknown) => void;
};

const DEFAULTS = {
  retention_floor: 0.8, refund_ceiling: 0.08, dispute_ceiling: 0.02, min_signups: 30, churn_ratio: 1.3,
};

const CHANNEL_NAMES: Record<string, string> = { webhook: "Slack", email: "email" };
const channelName = (channel: string) => CHANNEL_NAMES[channel] ?? channel;
const channelList = (channels: string[]) => channels.map(channelName).join(" and ");
/** The API reports errors as "webhook: why"; show the name people know it by. */
const channelText = (error: string) => error.replace(/\bwebhook:/g, "Slack:").replace(/\bemail:/g, "Email:");

function AlertLine({ alert }: { alert: StopAlert }) {
  if (alert.status === "sent") {
    return <p className="stop-alert"><Icon name="check" size={13} /> Alert sent to {channelList(alert.sent)}{alert.sent_at ? ` on ${localTime(alert.sent_at)}` : ""}</p>;
  }
  if (alert.status === "failed") {
    return <p className="stop-alert is-bad"><Icon name="alert" size={13} /> Alert failed. {alert.error ? channelText(alert.error) : ""}</p>;
  }
  return (
    <p className="stop-alert">
      {alert.attempts === 0 ? "Sending alert…"
        : `Alert not delivered yet, trying again${alert.next_try ? ` at ${localTime(alert.next_try)}` : ""}. ${alert.error ? channelText(alert.error) : ""}`}
    </p>
  );
}

function AlertSetup({ channels, simulated, onError }: { channels: string[]; simulated: boolean; onError: (error: unknown) => void }) {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  async function sendTest() {
    setTesting(true);
    setResult(null);
    try {
      const { sent, failed } = await api.testAlert();
      const problems = Object.entries(failed).map(([channel, why]) => `${channelName(channel)} failed: ${why}`);
      setResult(problems.length
        ? { ok: false, text: `${sent.length ? `Sent to ${channelList(sent)}. ` : ""}${problems.join("; ")}` }
        : { ok: true, text: `Test alert sent to ${channelList(sent)}.` });
    } catch (error) {
      onError(error);
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="alert-setup">
      <h4>Alerts</h4>
      {channels.length > 0 ? (
        <p className="muted">When real data gets an option stopped automatically, PlanLab tells {channelList(channels)}.{simulated && " Simulated runs don't send alerts."}</p>
      ) : (
        <p className="muted">Not set up, so stops only show here. Add <code>ALERT_WEBHOOK_URL</code> for Slack, or the email settings, to <code>backend/.env</code> and restart the API.</p>
      )}
      {channels.length > 0 && (
        <div className="btn-row">
          <button className="btn" type="button" disabled={testing} onClick={() => void sendTest()}>{testing ? "Sending…" : "Send test alert"}</button>
          {result && <span className={result.ok ? "hint" : "field-error"} role="status">{result.ok && <Icon name="check" size={14} />} {result.text}</span>}
        </div>
      )}
    </div>
  );
}

function checksStatus({ enabled, every_minutes, last_checked }: AutoChecks, simulated: boolean) {
  if (!enabled) return "Off. Options are only stopped when you run the analysis.";
  if (simulated) return "This simulation was checked day by day as it ran.";
  if (!every_minutes) return "Background checks are switched off on this server, so new data is only checked when you change a limit.";
  const often = every_minutes < 1 ? `every ${Math.round(every_minutes * 60)} seconds`
    : every_minutes === 1 ? "every minute" : `every ${+every_minutes.toFixed(1)} minutes`;
  return `Real data is checked ${often}. ${last_checked ? `Last check: ${localTime(last_checked)}.` : "It hasn't been checked yet."}`;
}

const toForm = (c: GuardrailConfig) => ({
  retention: String(+(c.retention_floor * 100).toFixed(2)),
  refund: String(+(c.refund_ceiling * 100).toFixed(2)),
  dispute: String(+(c.dispute_ceiling * 100).toFixed(2)),
  signups: String(c.min_signups),
  ratio: String(c.churn_ratio),
});

function Meter({ value, limit, mode, breached }: { value: number; limit: number; mode: "floor" | "ceiling"; breached: boolean }) {
  const max = mode === "floor" ? 1 : Math.max(limit * 2, value * 1.1, 0.01);
  return (
    <div className={breached ? "meter is-bad" : "meter"}>
      <span className="meter-value">{breached && <Icon name="alert" size={13} />}{pct(value)}</span>
      <span className="meter-track">
        <i style={{ width: `${Math.min(100, (value / max) * 100)}%` }} />
        <b style={{ left: `${Math.min(100, (limit / max) * 100)}%` }} />
      </span>
    </div>
  );
}

const breachedOn = (arm: GuardrailArm, word: string) => arm.breaches.some((b) => b.includes(word));

export default function GuardrailsTab({
  expId, guardrails, rows, stoppedArms, stops, autoChecks, simulated, rolledOut, busy, nameOfArm, explain, onResume, onSaved, onError,
}: Props) {
  const config = guardrails.config;
  const [form, setForm] = useState(() => toForm(config));
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const signupsOf = (armId: string) => rows.find((r) => r.arm_id === armId)?.signups ?? 0;
  const baselineOf = (armId: string) => rows.find((r) => r.arm_id === armId)?.is_baseline ?? false;

  useEffect(() => { setForm(toForm(config)); }, [config.retention_floor, config.refund_ceiling, config.dispute_ceiling, config.min_signups, config.churn_ratio]); // eslint-disable-line react-hooks/exhaustive-deps

  const next: GuardrailConfig = {
    retention_floor: Number(form.retention) / 100,
    refund_ceiling: Number(form.refund) / 100,
    dispute_ceiling: Number(form.dispute) / 100,
    min_signups: Number(form.signups),
    churn_ratio: Number(form.ratio),
    auto_stop: config.auto_stop,
  };
  const inRange = (value: string, min: number, max: number) =>
    value.trim() !== "" && Number(value) >= min && Number(value) <= max;
  const valid = inRange(form.retention, 0, 100) && inRange(form.refund, 0, 100) && inRange(form.dispute, 0, 100)
    && inRange(form.signups, 0, 1_000_000) && Number.isInteger(next.min_signups) && inRange(form.ratio, 1, 100);
  const dirty = JSON.stringify(form) !== JSON.stringify(toForm(config));

  async function save(values: GuardrailConfig, confirm = true) {
    setSaving(true);
    setJustSaved(false);
    try {
      await api.saveGuardrails(expId, values);
      await onSaved();
      setJustSaved(confirm);
    } catch (error) {
      onError(error);
    } finally {
      setSaving(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (valid) void save(next);
  }

  const field = (key: keyof typeof form, label: string, suffix: string, hint: string, step = "any") => (
    <label className="field">
      <span>{label}</span>
      <span className="input-suffix">
        <input className="input mono" type="number" min="0" step={step} inputMode="decimal" value={form[key]}
          onChange={(e) => { setJustSaved(false); setForm({ ...form, [key]: e.target.value }); }} />
        <em>{suffix}</em>
      </span>
      <small>{hint}</small>
    </label>
  );

  return (
    <div className="stack">
      <section className="panel panel-flush">
        <div className="panel-head">
          <h3>Status at this point in the test</h3>
          <p className="muted panel-note">The bar shows the value and the tick shows your limit. A limit counts as breached once the data makes that at least 90% likely, not on a brief wobble. The baseline is monitored but never stopped.</p>
        </div>
        <div className="table-wrap">
          <table className="table guard-table">
            <thead>
              <tr>
                <th scope="col">Price option</th>
                <th scope="col" title="Share of subscribers expected to remain after 60 days, projected from monthly churn">Projected 60-day retention</th>
                <th scope="col">Refund rate</th>
                <th scope="col">Dispute rate</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {guardrails.arms.map((arm) => {
                const tooFew = signupsOf(arm.arm_id) < config.min_signups;
                return (
                  <tr key={arm.arm_id}>
                    <td>{nameOfArm(arm.arm_id)}{baselineOf(arm.arm_id) && <em className="tag">Baseline</em>}</td>
                    <td><Meter value={arm.retention} limit={config.retention_floor} mode="floor" breached={breachedOn(arm, "retention")} /></td>
                    <td><Meter value={arm.refund_rate} limit={config.refund_ceiling} mode="ceiling" breached={breachedOn(arm, "Refund")} /></td>
                    <td><Meter value={arm.dispute_rate} limit={config.dispute_ceiling} mode="ceiling" breached={breachedOn(arm, "Dispute")} /></td>
                    <td>
                      {stoppedArms.includes(arm.arm_id) ? <Pill kind="crit">Stopped</Pill>
                        : tooFew ? <Pill kind="neutral">Too few sign-ups to judge</Pill>
                        : arm.ok ? <Pill kind="good">Within limits</Pill>
                          : <Pill kind="crit" icon="alert">Breached</Pill>}
                      {arm.breaches.map((b) => <p className="breach" key={b}>{b}</p>)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel auto-checks">
        <h3>Automatic checks</h3>
        <label className="toggle">
          <input type="checkbox" checked={config.auto_stop} disabled={saving || rolledOut}
            onChange={(e) => void save({ ...config, auto_stop: e.target.checked }, false)} />
          Stop options automatically
        </label>
        <p className="auto-status" role="status">{checksStatus(autoChecks, simulated)}</p>
        <p className="muted">PlanLab checks your limits without being asked, and stops an option once a breach is at least 99% likely. That&rsquo;s stricter than the 90% above because checking constantly would otherwise stop healthy prices by chance. So an option can show as breached for a while before it&rsquo;s stopped, and running the analysis stops it sooner.</p>
        {stops.length > 0 && (
          <ul className="stop-list">
            {stops.map((stop) => (
              <li key={stop.arm_id}>
                <div>
                  <strong>{nameOfArm(stop.arm_id)}</strong>
                  <span className="muted"> stopped {stop.automatic ? "automatically" : "by the analysis"} {stop.day === null ? `on ${localTime(stop.at)}` : `on day ${stop.day}`}</span>
                  <p className="muted">{explain(stop.reasons)}</p>
                  {stop.alert && <AlertLine alert={stop.alert} />}
                </div>
                {!rolledOut && <button className="btn" disabled={busy || saving} onClick={() => onResume(stop.arm_id)}>Resume</button>}
              </li>
            ))}
          </ul>
        )}
        <AlertSetup channels={autoChecks.alerts} simulated={simulated} onError={onError} />
      </section>

      <form className="panel guard-form" onSubmit={submit}>
        <h3>Limits the agent can&rsquo;t cross</h3>
        <div className="guard-fields">
          {field("retention", "Retention floor", "%", "Stop an option if fewer subscribers than this are projected to stay 60 days")}
          {field("refund", "Refund ceiling", "%", "Stop if refunds per payment go above this")}
          {field("dispute", "Dispute ceiling", "%", "Stop if disputes per sign-up go above this")}
          {field("signups", "Minimum sign-ups", "", "Don't judge or pick an option with fewer than this", "1")}
          {field("ratio", "Churn ratio limit", "× baseline", "Stop if monthly churn is this many times the baseline, and significant", "0.05")}
        </div>
        <div className="btn-row">
          <button className="btn primary" disabled={saving || !valid || !dirty}>{saving ? "Saving…" : "Save limits"}</button>
          <button type="button" className="btn" disabled={saving} onClick={() => void save({ ...DEFAULTS, auto_stop: config.auto_stop })}>Reset to defaults</button>
          {!valid && <span className="field-error" role="alert">Use percentages from 0 to 100, a whole number of sign-ups, and a churn ratio of at least 1.</span>}
          {justSaved && <span className="hint" role="status"><Icon name="check" size={14} /> {config.auto_stop ? "Saved and checked against the new limits." : "Saved. Run the analysis to apply them."}</span>}
        </div>
      </form>
    </div>
  );
}
