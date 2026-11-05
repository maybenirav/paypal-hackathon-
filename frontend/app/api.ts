const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Arm = {
  arm_id: string; name: string; price: number; currency: string;
  interval: "MONTH" | "YEAR"; trial_days: number; setup_fee: number;
  is_baseline: boolean; reason: string;
};
export type ArmValidation = { arm_id: string; valid: boolean; errors: string[] };
export type ArmsResponse = { arms: Arm[]; source: "gemini" | "fallback"; validation: ArmValidation[] };

export type ArmInput = { arm_id: string; price: number; visitors: number; signups: number; churned: number };
export type ArmAnalysis = {
  arm_id: string; conversion: number; churn: number; ltv: number;
  ztest: { z: number; significant: boolean } | null;
  guardrail: { ratio: number | null; p_churn: number; triggered: boolean } | null;
};
export type DecisionResponse = {
  action: "stop_arm" | "winner" | "keep_testing"; arm_id: string | null;
  reasons: Record<string, string>; explanation: string; arms: ArmAnalysis[];
};

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} failed: ${r.status} ${await r.text()}`);
  return r.json();
}

export const generateArms = (goal: string, current_price: number, num_arms: number) =>
  post<ArmsResponse>("/agent/arms", { goal, current_price, num_arms });

export const decide = (baseline: ArmInput, arms: ArmInput[], goal?: string) =>
  post<DecisionResponse>("/engine/decide", { baseline, arms, goal });

export type Row = {
  arm_id: string; name: string; is_baseline: boolean; visitors: number; signups: number;
  churned: number; active: number; conversion: number; churn: number; mrr: number;
  revenue: number; ltv: number; z: number | null; status: string; retention: number[];
  conv_ci: [number, number]; prob_best: number; allocation: number;
  refund_rate: number; dispute_rate: number;
};
export type Metrics = {
  id: string; status: string; winner: string | null; stopped_arms: string[];
  rows: Row[]; mrr_series: ({ day: number } & Record<string, number>)[];
};
export type Experiment = { id: string; source: "gemini" | "fallback"; arms: Arm[]; validation: ArmValidation[] };
export type AskResponse = { answer: string; rows: Row[]; filter: unknown; chart: { metric: string } | null };

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) throw new Error(`${path} failed: ${r.status} ${await r.text()}`);
  return r.json();
}

export const createExperiment = (goal: string, current_price: number, num_arms: number) =>
  post<Experiment>("/experiments", { goal, current_price, num_arms });
export const runDemo = (id: string, scenario: "winner" | "guardrail") =>
  post<{ events: number }>(`/experiments/${id}/demo`, { scenario });
export const getMetrics = (id: string) => get<Metrics>(`/experiments/${id}/metrics`);
export const decideExperiment = (id: string) => post<DecisionResponse>(`/experiments/${id}/decide`, {});
export const askAssistant = (id: string, question: string) =>
  post<AskResponse>(`/experiments/${id}/ask`, { question });

export type GuardrailArm = { arm_id: string; ok: boolean; retention: number; refund_rate: number; dispute_rate: number; breaches: string[] };
export type Rollout = { kind: string; status: "proposed" | "approved"; winner: string; requests: { method: string; path: string }[] };
export type SaveAction = {
  id: string; ts: string; status: string; kind: "pause" | "downgrade" | "discount" | "let_go";
  rationale: string; subscriber_id: string; trigger: string; arm_id: string | null;
  overridden_by: string | null; override_note: string; paypal_request: { method: string; path: string };
};
export type SaveLog = { summary: { total: number; saves: number; let_go: number; overridden: number }; actions: SaveAction[] };

export const getGuardrails = (id: string) => get<{ arms: GuardrailArm[] }>(`/experiments/${id}/guardrails`);
export const proposeRollout = (id: string) => post<Rollout>(`/experiments/${id}/rollout/propose`, {});
export const approveRollout = (id: string) => post<Rollout>(`/experiments/${id}/rollout/approve`, {});
export const saveSweep = () => post<{ handled: number }>("/save/sweep?limit=25", {});
export const getSaveLog = () => get<SaveLog>("/save/log");
export const overrideSave = (id: string, kind: string, note: string) =>
  post<SaveAction>(`/save/log/${id}/override`, { kind, note });
