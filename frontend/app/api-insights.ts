// Day-zero import, decision memory and agent tools (kept apart from api.ts on purpose).
const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Cohort = { cohort: string; size: number; retention: number[] };
export type Backfill = {
  source: "paypal" | "simulated"; months: number; imported_at: string; transactions: number;
  customers: number; months_list?: string[]; cohorts: Cohort[];
  revenue: { month: string; revenue: number }[];
  plans: { id: string; name: string; status: string }[];
  periods?: number; periods_failed?: number; partial?: boolean; warning?: string;
  summary: {
    payments: number; refunds: number; disputes: number; refund_rate?: number;
    avg_monthly_churn?: number | null; active_last_month?: number; arpu_last_month?: number | null;
  };
};
export type DecisionDoc = {
  id: string; ts: string; kind: string; action: string; experiment_id: string | null;
  arm_id: string | null; subject: string | null; summary: string; reason: string;
};
export type WhyAnswer = { question: string; answer: string; matches: DecisionDoc[]; engine: "elastic" | "local" };
export type AgentTool = { name: string; description: string; inputSchema: Record<string, unknown>; readOnly: boolean };

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method, headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = await r.text();
    try { detail = JSON.parse(detail).detail ?? detail; } catch { /* plain text */ }
    throw new Error(`${r.status}: ${detail}`);
  }
  return r.json();
}

export const getCohorts = () => call<Backfill>("GET", "/connect/cohorts");
export const backfillLive = (months = 12) => call<Backfill>("POST", `/connect/backfill?months=${months}`);
export const backfillSample = (months = 12) => call<Backfill>("POST", `/connect/backfill/sample?months=${months}`);
export const askWhy = (question: string, experiment_id?: string | null) =>
  call<WhyAnswer>("POST", "/decisions/why", { question, experiment_id: experiment_id ?? null });
export const listTools = () => call<{ tools: AgentTool[] }>("GET", "/agent/tools");
export const runTool = (name: string, args: Record<string, unknown>) =>
  call<{ tool: string; result: unknown }>("POST", `/agent/tools/${name}`, args);
