const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Arm = {
  arm_id: string; name: string; price: number; currency: string;
  interval: "MONTH" | "YEAR"; trial_days: number; setup_fee: number;
  is_baseline: boolean; reason: string; plan_id: string | null;
};
export type ArmValidation = {
  arm_id: string; valid: boolean; errors: string[]; warnings: string[];
  rollout: "baseline" | "in_place" | "new_signups_only" | null;
};
export type Readiness = {
  needed_per_arm: number; have_per_arm: number; progress: number; arms: number;
  monthly_visitors: number | null; days_needed: number | null; feasible: boolean | null; message: string;
};
export type Rejected = { arm: Arm; errors: string[] };
export type Experiment = {
  id: string; source: "gemini" | "fallback"; arms: Arm[]; validation: ArmValidation[];
  rejected: Rejected[]; readiness: Readiness;
};
export type ExperimentSummary = { id: string; goal: string; status: string; events: number; created_at: string };

export type Row = {
  arm_id: string; name: string; is_baseline: boolean; price: number; currency: string;
  interval: "MONTH" | "YEAR"; trial_days: number;
  visitors: number; signups: number; paid: number; churned: number; active: number;
  conversion: number; paid_conversion: number; mrr_per_visitor: number;
  trial_to_paid: number | null; first_renewal: number | null;
  churn: number; mrr: number; revenue: number; projected_ltv: number; retention: number[];
  conv_ci: [number, number]; prob_best: number; prob_beats_baseline: number | null; allocation: number;
  refund_rate: number; dispute_rate: number; rollout: string; plan_id: string | null;
  real_events: number; status: string;
};
export type Metrics = {
  id: string; goal: string; status: string; winner: string | null; stopped_arms: string[];
  rows: Row[]; readiness: Readiness; rejected_arms: Rejected[];
  events: { real: number; simulated: number };
  mrr_series: ({ day: number } & Record<string, number>)[];
};
export type DecisionResponse = {
  action: "stop_arm" | "winner" | "keep_testing"; arm_id: string | null;
  reasons: Record<string, string>; explanation: string;
};
export type AskResponse = {
  answer: string; rows: Row[];
  filter: { metric: keyof Row; op: ">" | "<"; value: number } | null;
  sort: { metric: keyof Row; desc: boolean } | null; chart: { metric: string } | null;
};
export type GuardrailArm = {
  arm_id: string; ok: boolean; retention: number; refund_rate: number; dispute_rate: number; breaches: string[];
};
export type Rollout = {
  kind: string; status: "proposed" | "approved"; winner: string; grandfathered: boolean;
  requests: { method: string; path: string; note?: string }[]; results: { status: string }[];
};
export type SaveAction = {
  id: string; ts: string; status: string; kind: "pause" | "downgrade" | "discount" | "let_go";
  rationale: string; message: string; subscriber_id: string; trigger: string; arm_id: string | null;
  reason_text: string | null; reason_hint: string; reason_source: string; simulated: boolean; price: number;
  approve_url: string | null; resume_at: string | null; resumed: boolean;
  overridden_by: string | null; override_note: string; paypal_request: { method: string; path: string };
};
export type SaveLog = {
  summary: { total: number; saves: number; let_go: number; overridden: number; mrr_protected: number };
  actions: SaveAction[];
};
export type Timeline = {
  project: { startDate: string };
  tasks: { rows: { id: string; name: string; startDate: string; duration: number; percentDone?: number }[] };
  dependencies: { rows: { id: string; fromTask: string; toTask: string }[] };
};
export type CheckoutJob = {
  id: string; status: "running" | "done"; count: number;
  results: { subscription_id?: string; arm_id?: string; status?: string; approved?: boolean; error?: string }[];
};

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method, headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    const text = await r.text();
    let msg = text;
    try { const j = JSON.parse(text); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* plain text */ }
    throw new Error(`${r.status}: ${msg}`);
  }
  return r.json();
}
const get = <T,>(p: string) => req<T>("GET", p);
const post = <T,>(p: string, b: unknown = {}) => req<T>("POST", p, b);

export const listExperiments = () => get<ExperimentSummary[]>("/experiments");
export const createExperiment = (goal: string, current_price: number, num_arms: number, monthly_visitors?: number) =>
  post<Experiment>("/experiments", { goal, current_price, num_arms, monthly_visitors: monthly_visitors || null });
export const runDemo = (id: string, scenario: "winner" | "guardrail") =>
  post<{ events: number }>(`/experiments/${id}/demo`, { scenario });
export const getMetrics = (id: string) => get<Metrics>(`/experiments/${id}/metrics`);
export const decideExperiment = (id: string) => post<DecisionResponse>(`/experiments/${id}/decide`);
export const askAssistant = (id: string, question: string) => post<AskResponse>(`/experiments/${id}/ask`, { question });
export const getGuardrails = (id: string) => get<{ arms: GuardrailArm[] }>(`/experiments/${id}/guardrails`);
export const proposeRollout = (id: string) => post<Rollout>(`/experiments/${id}/rollout/propose`);
export const approveRollout = (id: string) => post<Rollout>(`/experiments/${id}/rollout/approve`);
export const getTimeline = (id: string) => get<Timeline>(`/experiments/${id}/timeline`);
export const publishPlans = (id: string) =>
  post<{ plans: { arm_id: string; plan_id: string; created: boolean }[]; sdk: string }>(`/experiments/${id}/paypal/publish`);
export const kernelCheckout = (id: string, count: number) => post<CheckoutJob>(`/experiments/${id}/kernel/checkout`, { count });
export const kernelJob = (id: string, job: string) => get<CheckoutJob>(`/experiments/${id}/kernel/checkout/${job}`);
export const resetDemo = () => post<{ id: string }>("/admin/reset-demo");

export const saveSweep = () => post<{ handled: number; resumed: number }>("/save/sweep?limit=25");
export const getSaveLog = () => get<SaveLog>("/save/log");
export const overrideSave = (id: string, kind: string, note: string) => post<SaveAction>(`/save/log/${id}/override`, { kind, note });
export const cancelIntent = (subscription_id: string, reason_text: string) =>
  post<SaveAction>("/save/cancel-intent", { subscription_id, reason_text });
export const setupSavePlans = (experiment_id: string) =>
  post<{ downgrade_plan_id: string; discount_plan_id: string }>("/save/plans/setup", { experiment_id });
export const health = () => get<{ ok: boolean; save_agent_live: boolean }>("/health");
