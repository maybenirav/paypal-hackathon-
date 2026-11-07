const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const API_URL = BASE;
/** Remembers which experiment a PayPal checkout belongs to, so the return page can sync it. */
export const LAST_EXP_KEY = "planlab-last-exp";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const TOKEN = process.env.NEXT_PUBLIC_API_TOKEN;
const TIMEOUT_MS = 90_000;   // AI calls can take a while, but a hung backend shouldn't hang the page

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      signal: AbortSignal.timeout(TIMEOUT_MS),
      headers: { ...init?.headers, ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}) },
    });
  } catch {
    throw new ApiError(0, `Can't reach the API at ${BASE}. Is the backend running?`);
  }
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      const parsed = JSON.parse(text);
      detail = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
    } catch { /* not JSON: keep the raw text */ }
    throw new ApiError(response.status, detail || `Request failed (${response.status})`);
  }
  return response.json();
}

const get = <T>(path: string) => request<T>(path);
const send = <T>(method: string, path: string, body?: unknown) =>
  request<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export type Arm = {
  arm_id: string; name: string; price: number; currency: string;
  interval: "MONTH" | "YEAR"; trial_days: number; setup_fee: number;
  is_baseline: boolean; reason: string; plan_id: string | null;
};
export type ArmValidation = {
  arm_id: string; valid: boolean; errors: string[]; paypal_payload: Record<string, unknown> | null;
};
export type Row = {
  arm_id: string; name: string; price: number; interval: "MONTH" | "YEAR"; is_baseline: boolean;
  visitors: number; signups: number; churned: number; active: number; conversion: number;
  churn: number; monthly_churn: number; retention_60: number; mrr: number; revenue: number;
  ltv: number; value_per_visitor: number; value_ci: [number, number]; z: number | null; status: string;
  retention: (number | null)[]; conv_ci: [number, number]; prob_best: number; allocation: number;
  prob_beats_baseline: number | null; lift: number | null; refund_rate: number; dispute_rate: number;
};
export type MrrPoint = { day: number } & Record<string, number>;
export type GuardrailConfig = {
  retention_floor: number; refund_ceiling: number; dispute_ceiling: number; min_signups: number;
  churn_ratio: number; auto_stop: boolean;
};
export type StopAlert = {
  status: "pending" | "sent" | "failed"; attempts: number; sent: string[]; sent_at: string | null;
  next_try: string | null; error: string | null;
};
export type Stop = {
  arm_id: string; day: number | null; at: string; reasons: string; automatic: boolean; alert?: StopAlert | null;
};
export type AutoChecks = { enabled: boolean; every_minutes: number; last_checked: string | null; alerts: string[] };
export type GuardrailArm = {
  arm_id: string; ok: boolean; retention: number; retention_so_far: number; refund_rate: number;
  dispute_rate: number; breaches: string[];
};
export type Guardrails = { config: GuardrailConfig; arms: GuardrailArm[] };
export type Metrics = {
  id: string; status: string; winner: string | null; stopped_arms: string[]; rows: Row[];
  mrr_series: MrrPoint[]; window_days: number; guardrails: Guardrails; stops: Stop[]; auto_checks: AutoChecks;
};
export type Decision = {
  action: "stop_arm" | "winner" | "keep_testing"; arm_id: string | null;
  reasons: Record<string, string>; explanation: string; note: string; as_of_day: number | null;
};
export type Rollout = {
  kind: string; status: "proposed" | "approved" | "failed"; winner: string;
  requests: { method: string; path: string; note?: string }[];
  results: { status?: string }[];
  existing_subscribers: Record<string, number>;
};
export type Scenario = "winner" | "guardrail";
export type Experiment = {
  id: string; goal: string; source: "gemini" | "fallback"; status: string; winner: string | null;
  stopped_arms: string[]; scenario: Scenario | null; arms: Arm[]; validation: ArmValidation[];
  events: { real: number; simulated: number }; rollout: Rollout | null; last_decision: Decision | null;
  stops: Stop[];
};
export type ExperimentSummary = { id: string; goal: string; status: string; events: number };
export type AskResponse = {
  answer: string; rows: Row[];
  filter: { metric: string; op: string; value: number } | null;
  sort: { metric: string; desc: boolean } | null;
  chart: { metric: string } | null;
};
export type MemoryRecord = {
  experiment_id: string; goal: string; winner: string | null; stopped: string[];
  winner_name: string | null; stopped_names: string[]; simulated: boolean;
};
export type OfferKind = "pause" | "downgrade" | "discount" | "let_go";
export type SaveAction = {
  id: string; ts: string; status: string; kind: OfferKind; rationale: string; subscriber_id: string;
  trigger: string; arm_id: string | null; overridden_by: string | null; override_note: string;
  paypal_request: { method: string; path: string };
};
export type SaveLog = {
  summary: { total: number; saves: number; let_go: number; overridden: number };
  actions: SaveAction[];
};
export type SaveRules = {
  allow_pause: boolean; allow_downgrade: boolean; allow_discount: boolean; max_discount_pct: number;
  discount_cycles: number; pause_days: number; max_saves_per_subscriber: number;
  downgrade_plan_id: string | null;
};
export type PayPalHealth =
  | { paypal: "connected"; env: string; product_id_set: boolean }
  | { paypal: "error"; detail: string };
export type PublishResult = { plans: { arm_id: string; plan_id: string; created: boolean }[] };
export type Subscription = {
  subscription_id: string; arm_id: string; plan_id: string; approve_url: string | null; status: string;
  visitor_id: string | null;
};
export type SyncResult = { subscription_id: string; arm_id: string; status: string; added: string[] };
export type Transaction = {
  transaction_id: string; date: string; status: string; amount: number; currency: string;
  fee: number; arm_id: string;
};

export const health = () => get<{ ok: boolean; gemini: boolean }>("/health");
export const listExperiments = () => get<ExperimentSummary[]>("/experiments");
export const getExperiment = (id: string) => get<Experiment>(`/experiments/${id}`);
export const createExperiment = (goal: string, current_price: number, num_arms: number) =>
  send<{ id: string }>("POST", "/experiments", { goal, current_price, num_arms });
export const deleteExperiment = (id: string) => send<{ deleted: string }>("DELETE", `/experiments/${id}`);
export const runDemo = (id: string, scenario: Scenario, force = false) =>
  send<{ events: number }>("POST", `/experiments/${id}/demo`, { scenario, force });
export const getMetrics = (id: string, days?: number) =>
  get<Metrics>(`/experiments/${id}/metrics${days === undefined ? "" : `?days=${days}`}`);
export const decideExperiment = (id: string, days?: number) =>
  send<Decision>("POST", `/experiments/${id}/decide${days === undefined ? "" : `?days=${days}`}`);
export const askAssistant = (id: string, question: string, days?: number) =>
  send<AskResponse>("POST", `/experiments/${id}/ask${days === undefined ? "" : `?days=${days}`}`, { question });
export const getMemory = () => get<{ records: MemoryRecord[] }>("/memory");

export const saveGuardrails = (id: string, config: GuardrailConfig) =>
  send<GuardrailConfig>("PUT", `/experiments/${id}/guardrails`, config);
export const resumeArm = (id: string, armId: string) =>
  send<{ resumed: string; status: string }>("POST", `/experiments/${id}/arms/${armId}/resume`);
export const testAlert = () => send<{ sent: string[]; failed: Record<string, string> }>("POST", "/alerts/test");
export const proposeRollout = (id: string) => send<Rollout>("POST", `/experiments/${id}/rollout/propose`);
export const approveRollout = (id: string) => send<Rollout>("POST", `/experiments/${id}/rollout/approve`);

export const saveSweep = (id: string) =>
  send<{ handled: number }>("POST", `/save/sweep?limit=100&experiment_id=${id}`);
export const getSaveLog = (id: string) => get<SaveLog>(`/save/log?experiment_id=${id}`);
export const overrideSave = (id: string, kind: OfferKind) =>
  send<SaveAction>("POST", `/save/log/${id}/override`, { kind, note: "Changed by the merchant" });
export const getSaveRules = () => get<SaveRules>("/save/rules");
export const putSaveRules = (rules: SaveRules) => send<SaveRules>("PUT", "/save/rules", rules);

export const paypalHealth = () => get<PayPalHealth>("/paypal/health");
export const publishPlans = (id: string) => send<PublishResult>("POST", `/experiments/${id}/paypal/publish`);
export const subscribe = (id: string, armId?: string, visitorId?: string) =>
  send<Subscription>("POST", `/experiments/${id}/paypal/subscribe`, {
    ...(armId ? { arm_id: armId } : {}), ...(visitorId ? { visitor_id: visitorId } : {}),
  });
export const listSubscriptions = (id: string) =>
  get<{ subscriptions: Subscription[] }>(`/experiments/${id}/paypal/subscriptions`);
export const syncSubscription = (id: string, subscriptionId: string) =>
  send<SyncResult>("POST", `/experiments/${id}/paypal/sync/${subscriptionId}`);
export const cancelSubscription = (subscriptionId: string) =>
  send<{ status: string }>("POST", `/paypal/subscriptions/${subscriptionId}/cancel`, {});
export const getTransactions = (id: string) =>
  get<{ count: number; total_amount: number; transactions: Transaction[] }>(`/experiments/${id}/paypal/transactions?days=30`);
