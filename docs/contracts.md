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
| `GET /experiments/{id}` | full experiment: arms, validation, status, event counts by source, scenario, rollout, last decision (lets the UI restore itself after a reload) |
| `DELETE /experiments/{id}` | removes the experiment, its memory record and its Save Agent log |
| `POST /experiments/{id}/events` `[Event]` | ingest events (422 on unknown arm_id, 413 over 10,000 at once). Timestamps with a timezone are converted to UTC |
| `POST /experiments/{id}/demo` `{scenario: winner\|guardrail, seed, force}` | seeded 60-day scenario, replaces events. 409 if real events exist, unless `force` is true |
| `GET /experiments/{id}/metrics?days=` | per-arm rows (visitors, signups, conversion, churn, MRR, LTV, Z, retention, status), MRR series, `window_days`, and guardrail status as of that day. `stopped_arms` is as of that day too; `stops` lists every stop (`arm_id`, `day`, `at`, `reasons`, `automatic`, `alert`) and `auto_checks` gives `enabled`, `every_minutes`, `last_checked`, `alerts` (the channel names set up). `alert` is null, or `status` (`pending`, `sent`, `failed`), `attempts`, `sent`, `sent_at`, `next_try`, `error` |
| `POST /alerts/test` | sends a test message to every alert channel, returning `sent` and `failed` (channel to reason). 409 if none are set up, 429 within 10 seconds of the last test |
| `POST /experiments/{id}/decide?gamma=&days=` | run engine; stops an arm or picks a winner (which can be the baseline) and records it. `gamma` defaults to the stored `churn_ratio`. 409 after a rollout |
| `POST /experiments/{id}/ask?days=` `{question}` | assistant: returns `answer`, filtered/sorted `rows`, `filter`, `chart`, using metrics as of that day |
| `GET /experiments/{id}/allocation`, `POST /experiments/{id}/assign` `{visitor_id}` | Thompson traffic split on value per visitor / pick an arm. The same `visitor_id` keeps the same arm unless it's stopped or rolled out |
| `GET/PUT /experiments/{id}/guardrails` | retention floor, refund and dispute ceilings, minimum sign-ups, churn ratio, `auto_stop`; status per arm. 422 on out-of-range values. A PUT runs the automatic check straight away |
| `POST /experiments/{id}/arms/{arm_id}/resume` | undoes a stop. Automatic checks then leave that arm alone, though `decide` can still stop it. 409 if it isn't stopped or the experiment has been rolled out |
| `POST /experiments/{id}/rollout/propose`, `.../approve` | winner rollout with `existing_subscribers` per arm, merchant approves in one click. Afterwards all new customers get the winner |
| `GET /memory` | past settled experiments, with `simulated`, `winner_name`, `stopped_names` |
| `GET/PUT /save/rules`, `POST /save/handle`, `POST /save/sweep?experiment_id=`, `GET /save/log?experiment_id=`, `POST /save/log/{id}/override` | Save Agent, scoped to one experiment when `experiment_id` is given |
| `POST /experiments/{id}/paypal/subscribe` `{arm_id, visitor_id}`, `GET .../paypal/subscriptions` | create a subscription, list those created for this experiment |
| `POST /agent/arms`, `POST /engine/decide` | stateless variants |

## Notes
- The store runs in memory (`app/store.py`) and `app/persistence.py` saves it to SQLite after each change.
- `retention[w]` = life-table estimate of the share still active w weeks after their own signup. `null` for weeks too few people have reached.
- Each person counts once however many times an event repeats, and a SIGNUP implies a VISIT.
- `mrr` counts only subscribers who have paid, so unpaid free trials are excluded. Annual arms use price/12.
- `ltv` follows each plan's real payment schedule, so annual arms get their full up-front payment.
- `value_per_visitor` (conversion x `ltv`) is what decides the test and steers `allocation` and `prob_best`.
- `monthly_churn` is cancellations per subscriber-month. `churn` is the raw share who have cancelled so far.
- Demo scenarios: `winner` (annual_discount wins on LTV), `guardrail` (last challenger churn spikes and is stopped).
