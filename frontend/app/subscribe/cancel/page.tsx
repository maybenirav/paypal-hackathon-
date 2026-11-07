"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { LAST_EXP_KEY } from "../../api";
import { Icon } from "../../icons";

/** PayPal sends the buyer here if they back out of checkout. */
export default function SubscribeCancel() {
  const [expId, setExpId] = useState<string | null>(null);
  useEffect(() => {
    try { setExpId(localStorage.getItem(LAST_EXP_KEY)); } catch { /* the link falls back to home */ }
  }, []);

  return (
    <main className="return-page">
      <div className="return-card">
        <span className="return-mark"><Icon name="close" size={22} /></span>
        <h1>Checkout cancelled</h1>
        <p>No subscription was started and nothing was charged. The visit still counts toward this option&rsquo;s conversion rate.</p>
        <Link className="btn primary" href={expId ? `/?exp=${expId}` : "/"}>Back to the experiment</Link>
      </div>
    </main>
  );
}
