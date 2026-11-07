# Shared contracts (draft from Person B; agree as a team before changing)

## Arm JSON (agent output -> PayPal plan input)
`{arm_id, name, price, currency, interval: MONTH|YEAR, trial_days, setup_fee, is_baseline, reason}`
`POST /agent/arms` and `POST /experiments` also return `validation[]` with `paypal_payload`,
the ready-to-send body for `POST /v1/billing/plans` (replace `product_id`).

## Event format (real webhooks and simulated events are identical)
`{experiment_id, arm_id, subscriber_id, type, ts, amount, source: real|simulated}`

| type | PayPal webhook |
|---|---|
| VISIT | none (our checkout page view) |
| PAYMENT_FAILED | BILLING.SUBSCRIPTION.PAYMENT.FAILED |
| REFUND | PAYMENT.SALE.REFUNDED |
| DISPUTE | CUSTOMER.DISPUTE.CREATED |
| CANCEL_INTENT | none (our cancel-flow click) |
| SIGNUP | BILLING.SUBSCRIPTION.ACTIVATED |
| PAYMENT | PAYMENT.SALE.COMPLETED (`amount` = sale total) |
| CANCELLED | BILLING.SUBSCRIPTION.CANCELLED |
| DENIED | PAYMENT.SALE.DENIED |

Person A maps webhooks with `app.events.PAYPAL_EVENT_MAP` and posts them to `POST /experiments/{id}/events`.
Person D posts simulated events to the same route.

## API routes
| Route | Purpose |
|---|---|
| `POST /experiments` `{goal, current_price, num_arms}` | agent designs + validates arms, stores experiment |
| `GET /experiments` | list |
| `POST /experiments/{id}/events` `[Event]` | ingest events (422 on unknown arm_id) |
| `POST /experiments/{id}/demo` `{scenario: winner\|guardrail, seed}` | seeded 60-day scenario, replaces events |
| `GET /experiments/{id}/metrics?days=` | per-arm rows (visitors, signups, paid, paid_conversion, mrr_per_visitor, prob_beats_baseline, trial_to_paid, first_renewal, churn on paying subscribers, MRR, projected_ltv, rollout, real_events, status) + readiness + real/simulated event counts + weekly MRR series |
| `POST /experiments/{id}/decide?gamma=&days=` | run engine; stops an arm or picks a winner and records it |
| `POST /experiments/{id}/ask` `{question}` | assistant: returns `answer`, filtered/sorted `rows`, `filter`, `chart` |
| `GET /experiments/{id}/allocation`, `POST /experiments/{id}/assign` | Thompson traffic split / pick an arm for a new sign-up |
| `GET/PUT /experiments/{id}/guardrails` | retention floor, refund and dispute ceilings, status per arm |
| `POST /experiments/{id}/rollout/propose`, `.../approve` | winner rollout, merchant approves in one click |
| `GET /memory` | past settled experiments |
| `GET /experiments/{id}/timeline` | Bryntum Gantt project (tasks + dependencies) |
| `POST /experiments/{id}/paypal/publish`, `.../paypal/subscribe`, `.../paypal/sync/{sub}` | real PayPal plans and subscriptions (APIMatic SDK) |
| `POST /experiments/{id}/kernel/checkout` `{count}`, `GET .../kernel/checkout/{job}` | KERNEL sandbox buyers approve real checkouts |
| `POST /save/cancel-intent` `{subscription_id, reason_text}` | a real subscriber clicks cancel; the Save Agent responds |
| `POST /save/plans/setup` `{experiment_id}` | create the Lite and discount plans in PayPal |
| `POST /admin/reset-demo` | wipe and re-seed the demo merchant |
| `GET/PUT /save/rules`, `POST /save/handle`, `POST /save/sweep`, `GET /save/log`, `POST /save/log/{id}/override` | Save Agent |
| `POST /agent/arms`, `POST /engine/decide` | stateless variants |

## Notes
- The store is an in-memory cache written through to SQLite (`app/db.py`, path `PLANLAB_DB`). Call `store.save(exp)` after changing an experiment in place.
- `CANCEL_INTENT` events may carry `reason` (free text). Churn is measured on paying subscribers; an unpaid trial counts as a lost conversion, not churn.
- `retention[w]` = share of a cohort still active w weeks after their own signup.
- Simulated annual arms pay once; their `monthly_equivalent` is price/12 for MRR and LTV.
- Demo scenarios: `winner` (annual_discount wins on new MRR per visitor), `guardrail` (last challenger's churn and refunds spike and it is stopped).
