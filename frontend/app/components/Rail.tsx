"use client";

import { useEffect, useState } from "react";
import type { ExperimentSummary } from "../api";
import { statusInfo } from "../format";
import { Icon, Pill } from "../icons";

function ThemeToggle() {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const saved = document.documentElement.dataset.theme;
    setDark(saved ? saved === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches);
  }, []);
  function toggle() {
    const theme = dark ? "light" : "dark";
    setDark(!dark);
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("planlab-theme", theme); } catch { /* the toggle still works for this page */ }
  }
  const label = dark ? "Switch to light theme" : "Switch to dark theme";
  return (
    <button className="rail-icon" onClick={toggle} aria-label={label} title={label}>
      <Icon name={dark ? "sun" : "moon"} />
    </button>
  );
}

type Props = {
  experiments: ExperimentSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  apiOk: boolean;
  paypal: "connected" | "off" | "unknown";
  gemini: boolean | null;
};

export default function Rail({ experiments, activeId, onSelect, onNew, apiOk, paypal, gemini }: Props) {
  const newestFirst = [...experiments].reverse();
  return (
    <aside className="rail">
      <div className="rail-top">
        <button className="brand" onClick={onNew} aria-label="PlanLab, start a new experiment">
          <Icon name="flask" size={18} /> PlanLab
        </button>
        <button className="btn rail-new" onClick={onNew}><Icon name="plus" /> <span>New experiment</span></button>
      </div>

      <select className="rail-select" aria-label="Open an experiment" value={activeId ?? ""}
        disabled={newestFirst.length === 0} onChange={(e) => e.target.value && onSelect(e.target.value)}>
        <option value="">{newestFirst.length ? "Open an experiment…" : "No experiments yet"}</option>
        {newestFirst.map((e) => <option key={e.id} value={e.id}>{e.goal}</option>)}
      </select>

      <nav className="rail-list" aria-label="Experiments">
        <h2>Experiments</h2>
        {newestFirst.length === 0 && <p className="rail-empty">None yet. Designs you create appear here.</p>}
        <ul>
          {newestFirst.map((e) => {
            const status = statusInfo(e.status, e.events);
            return (
              <li key={e.id}>
                <button className={e.id === activeId ? "rail-item active" : "rail-item"}
                  aria-current={e.id === activeId ? "true" : undefined} onClick={() => onSelect(e.id)}>
                  <span className="rail-goal">{e.goal}</span>
                  <Pill kind={status.kind}>{status.label}</Pill>
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="rail-foot">
        <ul className="services" aria-label="Connections">
          <li className={apiOk ? "on" : "off"}><i />API {apiOk ? "connected" : "offline"}</li>
          <li className={paypal === "connected" ? "on" : "off"}><i />PayPal {paypal === "connected" ? "sandbox" : "not connected"}</li>
          <li className={gemini ? "on" : "off"}><i />Gemini {gemini ? "on" : "off"}</li>
        </ul>
        <ThemeToggle />
      </div>
    </aside>
  );
}
