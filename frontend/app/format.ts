import type { Experiment, Row } from "./api";

export const pct = (x: number, digits = 1) => `${(x * 100).toFixed(digits)}%`;
export const int = (x: number) => x.toLocaleString("en-US");

export function money(x: number, currency = "USD", digits = 0) {
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency, minimumFractionDigits: digits, maximumFractionDigits: digits,
  }).format(x);
}

export function priceLabel(price: number, interval: "MONTH" | "YEAR", currency = "USD") {
  return `${money(price, currency, price % 1 ? 2 : 0)}/${interval === "YEAR" ? "yr" : "mo"}`;
}

/** Fixed categorical slots, validated for colour-blind separation in both themes. */
export const seriesColor = (index: number) => `var(--series-${(index % 4) + 1})`;

export function colorOf(exp: Experiment) {
  const order = new Map(exp.arms.map((a, i) => [a.arm_id, i]));
  return (armId: string) => seriesColor(order.get(armId) ?? 0);
}

export function nameOf(exp: Experiment) {
  const names = new Map(exp.arms.map((a) => [a.arm_id, a.name]));
  return (armId: string) => names.get(armId) ?? armId;
}

/** The engine's text quotes arm ids; this swaps in the names merchants actually see. */
export function armText(exp: Experiment, text: string) {
  const name = nameOf(exp);
  let result = text.replace(/\b[Aa]rm '([^']+)'/g, (_, id: string) => name(id));
  for (const arm of exp.arms) result = result.replaceAll(arm.arm_id, arm.name);
  return result;
}

/** The API stores times as UTC without marking them, so mark them before showing local time. */
export function localTime(iso: string) {
  const utc = /(Z|[+-]\d\d:\d\d)$/.test(iso) ? iso : `${iso}Z`;
  return new Date(utc).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export type StatusKind = "neutral" | "good" | "crit" | "warn" | "note";

export function statusInfo(status: string, events: number): { label: string; kind: StatusKind } {
  if (status === "winner") return { label: "Winner chosen", kind: "good" };
  if (status === "stopped_arm") return { label: "Option stopped", kind: "crit" };
  if (events === 0) return { label: "Draft", kind: "neutral" };
  return { label: "Running", kind: "note" };
}

const METRIC_LABELS: Record<string, string> = {
  churn: "Churn", conversion: "Conversion", mrr: "MRR", revenue: "Revenue", signups: "Sign-ups",
  visitors: "Visitors", ltv: "12-month value", value_per_visitor: "Value per visitor", retention: "Retention",
  refund_rate: "Refund rate", dispute_rate: "Dispute rate",
};
const RATE_METRICS = new Set(["churn", "conversion", "retention", "refund_rate", "dispute_rate"]);
const MONEY_METRICS = new Set(["mrr", "revenue", "ltv", "value_per_visitor"]);
const CENTS_METRICS = new Set(["ltv", "value_per_visitor"]);

export const metricLabel = (metric: string) => METRIC_LABELS[metric] ?? metric;

export function metricValue(row: Row, metric: string): number {
  const value = (row as unknown as Record<string, number | (number | null)[]>)[metric];
  if (Array.isArray(value)) return value.filter((v) => v !== null).at(-1) ?? 0;
  return value ?? 0;
}

export function formatMetric(metric: string, value: number) {
  if (RATE_METRICS.has(metric)) return pct(value);
  if (MONEY_METRICS.has(metric)) return money(value, "USD", CENTS_METRICS.has(metric) ? 2 : 0);
  return int(value);
}
