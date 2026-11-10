# PlanLab

**PlanLab is an AI copilot for PayPal subscription merchants.** It keeps customers who try to cancel. Once a merchant has enough traffic, it also tests which price earns more. Everything runs on real PayPal Subscriptions plans.

- **Save Agent (works from the first customer).** A customer clicks cancel and gives a reason in their own words. Gemini works out why they're leaving. The merchant's rules then pick an offer: pause, downgrade, a discount for a few months, or let go. Each offer is a real PayPal call, and plan changes are confirmed by the customer on PayPal.
- **Pricing Agent (works once you have the traffic).** The merchant states a goal. Gemini proposes 2–4 pricing arms, and PlanLab checks them against PayPal's rules before creating real plans. Sign-ups are split by Thompson sampling. A winner is declared only when it is ≥95% likely to earn more **new monthly revenue per visitor**, a figure you can see at checkout, not one you wait a year for. Guardrails (churn, retention, refunds, disputes) stop risky arms automatically.
- **Full from day zero.** "Import 12 months from PayPal" backfills the merchant's transaction history into monthly cohorts (retention heat map, churn, refund rate) before the first experiment. A labelled sample merchant is one click away for demos.
- **Every decision is explainable.** Each agent decision (pause an arm, declare a winner, roll out, a Save Agent offer) is logged; ask "why did arm B pause?" and get the answer with its numbers.
- **Honest about sample size.** PlanLab tells a merchant how many visitors each arm needs and how many days their traffic takes to get there. If a test isn't feasible, it says so and points them to the Save Agent.

## What is real and what is simulated

| Real (PayPal sandbox) | Simulated, labelled `source: "simulated"` |
|---|---|
| Plans, subscriptions, checkouts (KERNEL buyers), webhooks, suspend / activate / revise / cancel | A 60-day cohort replayed in a second, so the guardrails, the decision and the Save Agent have data to act on |

The UI shows both counts at all times. Every Save Agent action was verified against the sandbox: see [`docs/sandbox-verification.md`](docs/sandbox-verification.md).

## How we use each sponsor tool

| Tool | What it does in PlanLab |
|---|---|
| **PayPal Subscriptions API** | Plans per arm, subscriptions, webhooks (signature-verified), and Save Agent actions: suspend + scheduled activate, revise to a Lite or discount plan, cancel. Rollout lowers a price in place (`update-pricing-schemes`), or grandfathers existing subscribers and deactivates the losing plans. |
| **APIMatic** | Every plan and subscription call goes through PayPal's APIMatic-generated **`paypal-server-sdk`** (`backend/app/paypal_sdk.py`). Running it against the sandbox, we found that the SDK drops `total_cycles: 0`, which makes every subscription expire after one payment. We fixed it in `plan_model` and added a regression test. We also validate PlanLab's own OpenAPI spec with APIMatic's Validation API (`backend/scripts/apimatic_validate.py`): 0 errors; renaming FastAPI's long operation ids cut the warnings from 20 to 4 ([report](docs/apimatic-validation.md)). |
| **AG Grid** | The merchant workspace, on AG Grid Community. **Ask the grid:** Gemini turns a question such as "which arms earn more per visitor than baseline without more churn?" into the grid's own column filter model and sort, so the merchant sees in the headers exactly what the AI did and can edit it (a rule parser takes over if Gemini is down; matching rows are always computed from the data). **Results grid:** baseline pinned on top, every arm shown against it, column groups (Decision / Funnel / Revenue / Retention & risk), custom cells for chance-to-win (with the 95% winner line), credible intervals and weekly retention sparklines, cells that flash when real KERNEL sign-ups land, guardrail breaches as tooltips, CSV export. **Save Agent log:** the merchant overrides the agent by editing the Offer cell (select editor), which makes the PayPal call and logs the override; quick search and a real-PayPal-only filter. |
| **Bryntum Gantt** | Experiment timeline: design, plans published, guardrail checkpoints every 14 days, decision, rollout (`GET /experiments/{id}/timeline`). |
| **KERNEL** | Cloud browsers log in as sandbox buyers and approve real checkouts and plan changes (`backend/app/kernel_bot.py`, `POST /experiments/{id}/kernel/checkout`). This is how the demo gets real sign-ups. |
| **Render** | `render.yaml` sets up the API (with a persistent disk for SQLite), the web app, and a nightly cron for `POST /save/sweep`, which resumes ended pauses and handles failed payments. |
| **Postman** | A public collection for PlanLab's own API (`postman/`), generated from the OpenAPI schema so it never drifts. Its **Demo flow** folder runs the whole story with the Collection Runner: design, guardrail pause, "why?", Save Agent, history import. A test replays it on every run. |
| **Elastic** (optional) | Decision memory: every agent decision is indexed in Elasticsearch when `ELASTIC_URL` is set, and `POST /decisions/why` uses its full-text ranking. Without Elastic, or if it is down, the same search runs locally. |
| **WebMCP / outside agents** | `GET /agent/tools` publishes 7 read-only tools (metrics, grid filters, guardrails, "why", Save Agent log, cohorts); the page registers them with `navigator.modelContext` in browsers that support WebMCP, and an AG Studio custom agent can call the same endpoints. Nothing that moves money is exposed. |
| **Gemini + Groq / Ollama** | Pricing arms from a goal, cancel-reason classification, ask-the-grid filters and plain-English decision explanations. Every configured provider joins a failover chain (Gemini, then a free Groq key, then a local Ollama model); one that is overloaded or slow is skipped for a minute, and the UI names the model that answered. Deterministic templates take over if all of them fail. |

## Run locally

```bash
# backend
cd backend
pip install -r requirements.txt
cp .env.example .env        # fill in PayPal sandbox, Gemini (and/or Groq / Ollama) and KERNEL keys
python scripts/create_product.py   # once: prints PAYPAL_PRODUCT_ID
uvicorn app.main:app --reload      # http://localhost:8000/docs
pytest -q                          # 106 tests, no network needed

# frontend
cd frontend
npm install
npm run dev                        # http://localhost:3000
```

Storage: set `DATABASE_URL` to a Supabase Postgres (schema in `backend/db/schema.sql`) to keep experiments, events and PayPal subscriptions there; without it, a local SQLite file (`PLANLAB_DB`) holds everything. An empty store is seeded with a labelled demo merchant on first start. Set `SAVE_AGENT_LIVE=true` to send Save Agent and rollout requests to the sandbox instead of logging them.

## Architecture

```
frontend (Next.js, AG Grid, Bryntum)  ──►  FastAPI backend
                                            ├─ agent/      LLM agents: arms, explanations, cancel reasons (llm.py: Gemini → Groq → Ollama failover)
                                            ├─ engine/     bayes.py (Beta-Binomial, Thompson), guardrails.py, readiness.py, ltv.py (projection only)
                                            ├─ save_agent  understand → decide → act → log
                                            ├─ paypal_sdk  APIMatic SDK for every Subscriptions call
                                            ├─ kernel_bot  KERNEL sandbox buyers
                                            ├─ backfill    12-month Transaction Search import -> cohorts
                                            ├─ decision_log  every agent decision, searchable (Elastic optional)
                                            ├─ agent_tools read-only tools for WebMCP / AG Studio agents
                                            └─ db / store  SQLite write-through
PayPal sandbox  ◄──────────────────────────┘   webhooks ──► /webhooks/paypal
```

## Known limits

- A pricing test needs traffic. At a 10% conversion rate, detecting a 25% lift needs about 2,500 visitors per arm. PlanLab reports this instead of hiding it.
- PayPal has no timed pause. We suspend, and the nightly sweep calls `activate` when the pause ends.
- Moving an existing subscriber to a different plan requires their approval on PayPal. PlanLab never raises the price of an existing subscriber.
- The 12-month import was run against our PayPal sandbox app (10 Nov 2026: 7 transactions, 41 plans). Sandbox history is short, so churn is reported only once a full month has passed. Elastic search is tested against faked responses until `ELASTIC_URL` is set.
- The simulated clock replays time. Lifetime value is shown as a projection and is never used to pick a winner.
