"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { LAST_EXP_KEY, syncSubscription } from "../../api";
import { Icon } from "../../icons";

type State =
  | { kind: "syncing" }
  | { kind: "synced"; status: string; id: string }
  | { kind: "unlinked"; id: string | null }
  | { kind: "failed"; message: string };

/** PayPal sends the buyer here after approving, with ?subscription_id=I-... in the URL. */
export default function SubscribeSuccess() {
  const [state, setState] = useState<State>({ kind: "syncing" });
  const [expId, setExpId] = useState<string | null>(null);

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("subscription_id");
    let stored: string | null = null;
    try { stored = localStorage.getItem(LAST_EXP_KEY); } catch { /* treated as not linked */ }
    setExpId(stored);
    if (!id || !stored) {
      setState({ kind: "unlinked", id });
      return;
    }
    syncSubscription(stored, id)
      .then((result) => setState({ kind: "synced", status: result.status, id }))
      .catch((error: Error) => setState({ kind: "failed", message: error.message }));
  }, []);

  return (
    <main className="return-page">
      <div className="return-card">
        <span className="return-mark good"><Icon name="check" size={22} /></span>
        <h1>Subscription approved</h1>
        {state.kind === "syncing" && <p role="status">Recording it in PlanLab…</p>}
        {state.kind === "synced" && <p>PayPal reports <strong>{state.status}</strong> for <code>{state.id}</code>, and the sign-up now counts toward this experiment.</p>}
        {state.kind === "unlinked" && <p>PayPal finished the checkout{state.id ? <> for <code>{state.id}</code></> : ""}, but this browser doesn&rsquo;t know which experiment it belongs to. Use Sync on the PayPal tab, or wait for the webhook.</p>}
        {state.kind === "failed" && <p className="field-error" role="alert">The checkout worked, but PlanLab couldn&rsquo;t record it: {state.message}</p>}
        <Link className="btn primary" href={expId ? `/?exp=${expId}` : "/"}>Back to the experiment</Link>
      </div>
    </main>
  );
}
