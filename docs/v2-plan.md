# PlanLab v2: plan and our part (Agent / Stats)

**One line:** an AG Studio-native workspace where a Pricing Agent designs, runs and settles pricing experiments on live PayPal
Subscriptions plans, and a Save Agent intervenes on churn in real time. Every action is auditable.

The full v2 brief (upgrades, sponsor map, 20-day plan, demo script, risks) is the source of truth; this file tracks
**our role: Agent / Stats**, what is built, and the hand-offs.

## Status of the Agent / Stats scope

| v2 item | Status | Where |
|---|---|---|
| Stats in one well-tested module, Bayesian with credible intervals | Done | `backend/app/engine/analysis.py`, `bayes.py` (value per visitor, 95% ranges, P(best), P(beats baseline)) |
| Thompson-sampling allocation (replaces 50/50) | Done, on value per visitor | `analysis.analyze`, `GET /experiments/{id}/allocation`, `POST /experiments/{id}/assign` (same visitor, same offer) |
| Guardrails the agent cannot cross: retention floor, refund ceiling, dispute ceiling | Done (merchant-adjustable) | `engine/guardrails.py`, `GET/PUT /experiments/{id}/guardrails`; a breach pauses the arm and explains why |
| Decision checkpoint with winner + credible interval | Done | `POST /experiments/{id}/decide` (see `docs/agent-engine.md` for the rule and its simulated false-alarm rates) |
| Auto-rollout with one-click approval | Done as proposal + approve; PayPal calls are dry-run | `POST .../rollout/propose`, `.../rollout/approve` |
| Save Agent: offer selection within merchant rules | Done | `backend/app/save_agent.py` (pause, downgrade, 20% off x3, let go) |
| Save Agent audit log + merchant override | Done | `GET /save/log`, `POST /save/log/{id}/override`, UI panel |
| Nightly save sweep | Done (endpoint) | `POST /save/sweep`; Render cron should call it |
| Experiment Memory feeds the Pricing Agent | Done | `backend/app/memory.py`, `GET /memory`; fallback and Gemini prompt both use it |
| Pricing Agent (goal -> variants) | Done, Gemini optional | `backend/app/agent/pricing_agent.py` |
| Plan validation before creation | Local JSON Schema only | `backend/app/validation.py` |
| Cohort Simulator (labelled synthetic merchant) | Done for 2 scenarios | `backend/app/simulate.py` (winner, guardrail) |
| Agent Framework custom agent talking to Studio agents | **Not started**: needs AG Studio access | Person C / AG Studio trial |
| Real-account backfill (`list_transaction`) | Done | `backend/app/paypal_transactions.py`, shown on the PayPal tab |
| APIMatic SDK / Context Plugin | **Not started**: needs account access | see README note below |

## What the Save Agent really does (read before demoing)

- It chooses an offer by deterministic rules (reason hint, tenure, payments made, prior saves, merchant toggles). It does not use an LLM, so
  every decision is explainable and testable. An LLM can later write the customer-facing message.
- Requests are built for `POST /v1/billing/subscriptions/{id}/revise`, `/suspend`, `/cancel`. The default executor is a **dry run**;
  Person A injects the real PayPal client by assigning `save_log.executor`.
- **Pause caveat:** PayPal has no timed pause. We `suspend` and a worker must `activate` after `pause_days`.
- **Discount caveat:** implemented as a plan override on `revise` for the first N cycles. Confirm in the sandbox that the plan permits
  overrides; if not, create a discounted plan and `revise` to its `plan_id`.
- **Rollout caveat:** `update-pricing-schemes` only applies when the winner has the same interval and trial as the baseline. Otherwise the
  proposal is "switch default plan" plus deactivating the losers.

## Honesty notes for the README / video

- Demo data is a labelled synthetic cohort (`source: "simulated"` on every event). The current PayPal routes and tab have only run against a
  local stand-in, not the real sandbox.
- Guardrail horizon: "retention" is projected to 60 days from monthly churn, so it can trip before cohorts are 60 days old.
- 12-month value treats churn as constant from month to month and ignores anything after month 12, so renewals aren't credited.

## Hand-offs

- **Person A:** call `POST /experiments/{id}/assign` before creating each subscription; post webhook events (mapped via
  `app.events.PAYPAL_EVENT_MAP`, now including `PAYMENT.FAILED`, refunds and disputes) to `POST /experiments/{id}/events`; set `Arm.plan_id`
  after creating plans; inject the real executor; persist `Store`, memory and the save log in Postgres.
- **Person C:** the `rows` from `GET /experiments/{id}/metrics` already carry CI, P(best), traffic share, refund and dispute rates for the grid and
  widgets; `POST .../ask` returns grid filters.
- **Person D:** emit events in the shared format; use `CANCEL_INTENT` to exercise the Save Agent.
- **Person E:** Render cron -> `POST /save/sweep` nightly; Bryntum checkpoint -> `POST /experiments/{id}/decide?days=N`.
