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
| `GET /experiments/{id}/metrics?days=` | per-arm rows (visitors, signups, conversion, churn, MRR, LTV, Z, retention, status) + weekly MRR series |
| `POST /experiments/{id}/decide?gamma=&days=` | run engine; stops an arm or picks a winner and records it |
| `POST /experiments/{id}/ask` `{question}` | assistant: returns `answer`, filtered/sorted `rows`, `filter`, `chart` |
| `POST /agent/arms`, `POST /engine/decide` | stateless variants |

## Notes
- The store is in memory (`app/store.py`). Person A swaps in the real DB behind the same `Store` methods.
- `retention[w]` = share of a cohort still active w weeks after their own signup.
- Simulated annual arms pay once; their `monthly_equivalent` is price/12 for MRR and LTV.
- Demo scenarios: `winner` (annual_discount wins on LTV), `guardrail` (last challenger churn spikes and is stopped).
