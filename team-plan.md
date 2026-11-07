# PlanLab – Team Plan

> RevenueCat-style pricing experimentation for PayPal-billed web businesses.

This file explains who does what, how we work together, and how we plan to win the PayPal AI Hackathon. Read it once fully before you start.

---

## 1. What we're building (quick recap)

A merchant types a goal like *"Raise annual-plan uptake without hurting 60-day retention."*

PlanLab then:

1. **Designs experiments.** A Gemini pricing agent proposes 2–4 pricing arms (price, interval, trial length, setup fee) and explains why.
2. **Validates and launches.** Each arm is checked against PayPal's API spec (APIMatic) and created as a real billing plan in the PayPal sandbox.
3. **Routes sign-ups.** New subscribers are split across arms. KERNEL cloud browsers complete real sandbox checkouts so the conversion data is genuine.
4. **Tracks results.** PayPal webhooks plus a simulated clock feed MRR, churn and retention into an AG Grid / AG Studio workspace.
5. **Decides.** The math engine runs significance tests, LTV forecasts and guardrails. The agent picks a winner, or stops a risky arm, and explains it in plain English.

---

## 2. Things to sort out before coding

These will hurt us later if we ignore them.

- [ ] **PayPal needs a product before plans.** Create a catalog product first (`POST /v1/catalogs/products`), then the plans under it.
- [ ] **Sandbox can't fast-forward real billing.** The Simulated Clock Worker generates churn and renewal events. We say this openly in the demo: *real sign-ups, simulated time*.
- [ ] **Webhooks need a public URL.** Deploy the backend to Render first, before building features.
- [ ] **One backend language: Python (FastAPI).** No split between Node and Python.
- [ ] **Check access to every sponsor tool** (AG Studio, Bryntum, KERNEL, Render Workflows, APIMatic). Look at the hackathon Resources page and ask in Discord. We only want free access.
- [x] ~~"80% of merchants never test prices"~~: no source found, removed from the pitch. Use real merchant quotes instead.
- [ ] **Read the Rules page** to see whether one project can win more than one prize.

---

## 3. Team roles

| Person | Name | Role |
|---|---|---|
| A | _fill in_ | Backend & PayPal Lead |
| B | _fill in_ | AI Agent & Math Engine |
| C | _fill in_ | Frontend Workspace (AG Grid / AG Studio) |
| D | _fill in_ | Simulation & Telemetry (KERNEL, Simulated Clock) |
| E | _fill in_ | DevOps, Bryntum & Submission |

---

## 4. Who does what

### Person A – Backend & PayPal Lead

**Owns:** everything that talks to PayPal, plus the database.

- [ ] Set up the FastAPI project structure and database (Postgres on Render, or SQLite while developing)
- [ ] PayPal auth (OAuth token handling, sandbox only)
- [ ] Create the catalog product
- [ ] Create plan arms via `POST /v1/billing/plans` (prices, trials, setup fees)
- [ ] Generate a subscription link per arm and assign each new sign-up to an arm (random or weighted split)
- [ ] Webhook receiver with signature verification for:
  - `BILLING.SUBSCRIPTION.ACTIVATED` → arm sign-up count
  - `PAYMENT.SALE.COMPLETED` → cohort MRR
  - `BILLING.SUBSCRIPTION.CANCELLED` → churn / survival curves
  - `PAYMENT.SALE.DENIED` → churn-reason analytics
- [ ] Store every event in the shared event format (see section 5)
- [ ] Pull history from `GET /v1/reporting/transactions`
- [ ] *Stretch:* move existing subscribers to the winning plan with `POST /v1/billing/subscriptions/{id}/revise`
- [ ] Expose B's analysis and decision functions as API endpoints

**Done when:** a real sandbox subscription goes through an arm and its webhook shows up in the database.

### Person B – AI Agent & Math Engine

**Owns:** the "brain" of PlanLab.

- [ ] **Pricing agent (Gemini):** merchant goal → 2–4 arms as JSON, with a short reason for each
- [ ] **APIMatic validation:** check every generated plan against PayPal's OpenAPI spec before A submits it. Note exactly how APIMatic helped, for the README.
- [ ] **Conversion significance:** two-proportion Z-test with pooled rate; significant when |Z| ≥ 1.96
- [ ] **12-month LTV:** `V_LTV = Σ (M_arm × S(t)) / (1 + d)^t` for t = 1..12
- [ ] **Guardrail trigger:** `C_arm(t) / C_base(t) > γ` AND `p_churn < 0.05`, with γ = 1.30 by default (merchant-adjustable)
- [ ] **Decision logic:** pick a winner, keep testing, or stop an arm, and have the agent write the explanation the merchant reads
- [ ] Unit tests for every formula using hand-calculated examples

**Done when:** given a set of events, the engine returns a correct decision and a clear explanation.

### Person C – Frontend Workspace

**Owns:** the screen judges look at most. This is our AG Grid prize entry, so polish matters.

- [ ] Goal input screen (merchant types the experiment goal, sees the proposed arms, clicks Launch)
- [ ] **Arms Grid:** price, sign-ups, conversion, MRR, churn, LTV, significance, status per arm
- [ ] **Cohort Retention Pivot:** retention by arm and week
- [ ] **MRR chart** over time per arm
- [ ] **AI assistant inside the workspace:** questions like *"show arms with churn above baseline"* filter and chart the grid
- [ ] Clear visual moment when a guardrail fires or a winner is chosen
- [ ] Build against fake data matching the contracts first, then switch to the real API

**Done when:** a merchant can go from goal to decision without leaving the workspace.

### Person D – Simulation & Telemetry

**Owns:** making the demo feel alive with believable data.

- [ ] **KERNEL bots:** headless browsers that complete real sandbox checkouts across arms using sandbox buyer accounts
- [ ] **Simulated Clock Worker:** advances time and produces renewals and cancellations per arm, using the same event format as real webhooks so the math treats them the same way
- [ ] **Demo scenarios** (seeded and repeatable):
  - *Winner scenario:* the pricier annual arm wins on LTV
  - *Guardrail scenario:* one arm's churn spikes and the AI stops it (our demo highlight)
- [ ] A single "Run demo" control that produces a full experiment in about a minute

**Done when:** pressing "Run demo" gives the same believable story every time.

### Person E – DevOps, Bryntum & Submission

**Owns:** getting it live and getting it submitted.

- [ ] Deploy the skeleton to Render first, so webhooks work early
- [ ] Run the Simulated Clock Worker as a Render Workflow / cron job
- [ ] **Bryntum Gantt:** experiment launch → 14-day guardrail checkpoints → decision milestone, updating from the API
- [ ] Repo hygiene: licence file (visible in the About section), `.env.example`, `.gitignore`, no secrets committed
- [ ] README: what it is, architecture diagram, setup and run steps, and a **"How we used each sponsor tool"** section
- [ ] Devpost write-up: features, AI tools used and how, challenges, what's next
- [ ] Demo video under 3 minutes, public on YouTube, no copyrighted music
- [ ] Keep a running log of problems we hit and how we solved them, for the write-up

**Done when:** the hosted link works for a stranger and every submission requirement is ticked.

---

## 5. Shared contracts (agree on these together, first)

Everyone builds against these, so nobody waits on anyone else.

- **Data model:** `experiment`, `arm`, `subscriber`, `event`, `metric_snapshot`
- **Arm JSON schema:** what B's agent outputs and what A sends to PayPal
- **API endpoints:** list of routes between frontend and backend, with example responses
- **Event format:** one shape for both real webhooks and simulated events

Put these in `/docs/contracts.md`. Changing a contract means telling the whole team first.

---

## 6. How we work

### Order of building

We build in this order. Don't move to the next stage until the current one works end to end.

1. **Foundation:** contracts agreed, Render deploy live, one plan created in the sandbox
2. **One real path:** one sandbox subscription through one arm, webhook received and stored
3. **Brains and screens:** agent generates arms, math works on test data, grid shows fake data
4. **End to end:** goal → arms → KERNEL sign-ups → simulated churn → grid shows real results
5. **Wow features:** guardrail, winner selection, AI assistant, Bryntum timeline
6. **Feature freeze:** only bug fixes, UI polish, README and video from here

### Git workflow

- `main` always works. Never push broken code to it.
- Each person works on branches named `feature/<area>-<short-name>` (e.g. `feature/paypal-webhooks`).
- Open a pull request and get one teammate to glance at it before merging.
- Commit often with clear messages. Judges may look at the history.
- Secrets live only in `.env`. Never commit keys.

### Communication

- A short daily call: what works, what's blocked, what's next.
- If you're stuck for more than an hour, ask the team or the PayPal Discord.
- If a sponsor tool is blocked (licence, access, bugs), drop it quickly and tell the team. A polished core beats an extra logo.

---

## 7. How we win

### Judging criteria and how we hit each one

| Criterion | Our answer |
|---|---|
| Technological implementation | Real PayPal billing plans, subscriptions, webhooks and reporting, plus an AI agent that acts on them |
| Design | One clean workspace from goal to decision, built on AG Grid |
| Potential impact | Merchants can test prices safely instead of guessing |
| Innovation | AI-run pricing experiments with automatic guardrails on PayPal |
| Presentation | A tight video showing the guardrail stopping a risky price live |

### Sponsor prizes we're targeting

| Sponsor | How we use it | Owner |
|---|---|---|
| AG Grid / AG Studio | Entire merchant workspace + AI assistant | C |
| APIMatic | Validating AI-generated plan JSON against PayPal's spec | B |
| Bryntum | Experiment timeline and checkpoints | E |
| Render | Hosting + Simulated Clock Worker (prize is credits) | E |
| KERNEL | Cloud browser checkouts for real conversion data (no prize, but strengthens the demo) | D |

Each owner writes their sponsor's section in the README, explaining what the tool does in PlanLab and why it was the right choice.

### Demo story (under 3 minutes)

1. **Problem:** merchants set a price once and never test it
2. **Goal:** merchant types a goal into PlanLab
3. **AI designs arms:** agent proposes prices with reasons; APIMatic validates; plans appear in PayPal sandbox
4. **Live sign-ups:** KERNEL bots check out across arms; the grid fills with real data
5. **Time jump:** simulated clock advances 60 days
6. **Guardrail moment:** one arm's churn spikes, and the agent stops it automatically
7. **Winner:** agent picks the best arm with the numbers to back it up
8. **Close:** why this matters for PayPal merchants

---

## 8. Final submission checklist

- [ ] Project works end to end on the hosted link
- [ ] Public GitHub repo with licence visible in the About section
- [ ] README with setup steps and sponsor-tool sections
- [ ] Text description of features on Devpost
- [ ] List of AI tools used and how
- [ ] Demo video under 3 minutes, public on YouTube, no copyrighted music
- [ ] All teammates added to the Devpost project
- [ ] No API keys anywhere in the repo
- [ ] Submitted with time to spare, not in the last hour
