# PlanLab

**PlanLab is an AI copilot for PayPal subscription merchants.** It keeps customers who try to cancel. Once a merchant has enough traffic, it also tests which price earns more. Everything runs on real PayPal Subscriptions plans.

- **Save Agent (works from the first customer).** A customer clicks cancel and gives a reason in their own words. Gemini works out why they're leaving. The merchant's rules then pick an offer: pause, downgrade, a discount for a few months, or let go. Each offer is a real PayPal call, and plan changes are confirmed by the customer on PayPal.
- **Pricing Agent (works once you have the traffic).** The merchant states a goal. Gemini proposes 2–4 pricing arms, and PlanLab checks them against PayPal's rules before creating real plans. Sign-ups are split by Thompson sampling. A winner is declared only when it is ≥95% likely to earn more **new monthly revenue per visitor**, a figure you can see at checkout, not one you wait a year for. Guardrails (churn, retention, refunds, disputes) stop risky arms automatically.
- **Full from day zero.** "Import 12 months from PayPal" backfills the merchant's transaction history into monthly cohorts (retention heat map, churn, refund rate) before the first experiment. A labelled sample merchant is one click away for demos.
- **Every decision is explainable.** Each agent decision (pause an arm, declare a winner, roll out, a Save Agent offer) is logged; ask "why did arm B pause?" and get the answer with its numbers.
- **Honest about sample size.** PlanLab tells a merchant how many visitors each arm needs and how many days their traffic takes to get there. If a test isn't feasible, it says so and points them to the Save Agent.

## For judges: see it work in 5 minutes

We know you have a lot of projects to get through, so here's the shortest path we'd take ourselves.

**Live demo:** _Render link goes here_ · **Video:** _YouTube link goes here_

### The path (no API keys needed)

1. Open the app and hit **Start an experiment**.
2. Leave the goal as it is and press **Design experiment**. With a Gemini key the AI designs the arms; without one it falls back to our built-in templates. Either way you get 3 arms that have already been checked against PayPal's plan rules.
3. Press **Replay 60 days: risky arm**, then **Run analysis**. The guardrail stops the 7-day-trial arm and tells you why, with the numbers. This is the bit we care about most: the AI suggests, but the guardrails get the last word.
4. In the results grid, type *churn above baseline* and press **Ask the grid**. The AI doesn't just answer, it sets AG Grid's own filters, so you can see exactly what it did and undo it.
5. Scroll down to the timeline (Bryntum) to see the experiment as a plan: design, publish, checkpoints, decision, rollout.
6. Try **Replay 60 days: winner** and **Run analysis** again to see a winner declared, then propose and approve the rollout.
7. Open **History & why**, press **Load sample merchant** for 12 months of cohorts, and ask *why was the risky arm stopped?*
8. In the **Save Agent**, type a cancel reason the way a real customer would and see which offer it picks, and why.

### Why some of the data is simulated

A real pricing test takes weeks and thousands of visitors. We had neither inside a hackathon. So we split it honestly: **sign-ups and payments are real PayPal sandbox transactions**, made by cloud-browser buyers through KERNEL, and **the 60 days after them are simulated** so you can see the whole test play out in a minute. Every number in the app is labelled: look for the *Real PayPal sandbox* and *Simulated time* badges at the top.

### Run it yourself

```bash
# backend
cd backend
pip install -r requirements.txt
cp .env.example .env            # works as-is; keys are optional (see below)
uvicorn app.main:app --reload   # API docs: http://localhost:8000/docs

# frontend, in a second terminal
cd frontend
npm install
npm run dev                     # app: http://localhost:3000
```

Or run everything with `docker compose up --build`. Without keys, everything in the path above works, and any button that needs PayPal or KERNEL tells you exactly which key is missing instead of failing silently.

### To see the real PayPal parts

| What | What you need in `backend/.env` |
|---|---|
| Arms published as real PayPal billing plans | `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` from a sandbox app on developer.paypal.com, then run `python scripts/create_product.py` once and paste the `PAYPAL_PRODUCT_ID` it prints |
| Real sign-ups by cloud-browser buyers | `KERNEL_API_KEY`, plus sandbox **Personal** buyer accounts in `PAYPAL_SANDBOX_BUYER_1_EMAIL` / `_PASSWORD` (and `_2_`, `_3_`) |
| Import 12 months of history from PayPal | The PayPal keys above, with **Transaction Search** ticked under your app's Features |
| AI-designed arms and plain-English explanations | `GEMINI_API_KEY` (falls back to Groq, then Ollama, then templates) |

A few things that bit us, so they don't bite you:

- **Use US sandbox buyers.** Our first buyers were created in India, and PayPal showed its "We're sorry" page right after login. US Personal accounts went straight through.
- **Give the sandbox a minute.** It sometimes answers with a 503. PlanLab retries, and the 12-month import keeps whatever periods it got rather than failing outright.
- **New payments take a while to appear in Transaction Search** (often a couple of hours), so a fresh sign-up shows up in the app straight away but in the imported history later.

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
pytest -q                          # 111 tests, no network needed

# frontend
cd frontend
npm install
npm run dev                        # http://localhost:3000
```

Storage: set `DATABASE_URL` to a Supabase Postgres (schema in `backend/db/schema.sql`) to keep experiments, events and PayPal subscriptions there; without it, a local SQLite file (`PLANLAB_DB`) holds everything. An empty store is seeded with a labelled demo merchant on first start. Set `SAVE_AGENT_LIVE=true` to send Save Agent and rollout requests to the sandbox instead of logging them.

### With Docker

```bash
docker compose up --build        # web http://localhost:3000 · api http://localhost:8000/docs
docker compose --profile jobs run --rm jobs checkpoint    # run a scheduled job by hand
```

The API image reads `backend/.env`; the web image bakes `NEXT_PUBLIC_API_URL` in at build time.

## Deploy to Render

`render.yaml` is a Blueprint for the whole project, built from the same Dockerfiles
(validated against Render's published Blueprint schema): Dashboard > New > Blueprint > this repo,
then fill in the `sync: false` secrets.

| Service | Type | What it runs |
|---|---|---|
| `planlab-api` | web (Docker) | FastAPI, with a 1 GB disk for SQLite; `/health` is the health check |
| `planlab-web` | web (Docker) | Next.js standalone server |
| `planlab-save-sweep` | cron, daily 03:00 UTC | Save Agent: resume ended pauses, handle failed payments and cancel intents |
| `planlab-guardrail-checkpoint` | cron, every 6 h | Decision engine on every live experiment: pause risky arms, declare winners (rollout stays manual) |
| `planlab-history-refresh` | cron, Mondays 04:30 UTC | Re-import PayPal history, only if the merchant connected PayPal |

The crons run `backend/scripts/run_job.py` in the API image and call `POST /jobs/*` with a
`JOBS_TOKEN` that Render generates and shares with the API, so the public cannot trigger them.
`GET /jobs` shows the last result of each job. After the first deploy, add
`<api-url>/webhooks/paypal` as a webhook in the PayPal developer dashboard and set `PAYPAL_WEBHOOK_ID`.

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
