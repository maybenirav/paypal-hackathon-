# Devpost write-up (draft)

Fill in the TODOs before submitting. Every claim below can be checked in the repo or in
`docs/sandbox-verification.md`; keep it that way.

## Inspiration

A PayPal-billed subscription business sets a price once and rarely tests it. When a customer
cancels, there is no one to make them an offer. PlanLab is the copilot that does both, on real
PayPal Subscriptions plans.

## What it does

- **Save Agent.** A customer clicks cancel and gives a reason in their own words. An LLM works out
  why (price, not using it, card problems); the merchant's rules pick an offer: pause, downgrade,
  a discount for a few cycles, or let go. Each offer is a real PayPal call (`suspend`/`activate`,
  `revise`, `cancel`), plan changes are confirmed by the customer on PayPal, and every action lands
  in an audit log the merchant can override by editing a grid cell.
- **Pricing Agent.** The merchant states a goal; the agent proposes 2-4 pricing arms, checks them
  against PayPal's rules and creates them as real plans. Sign-ups are split by Thompson sampling.
  A winner is declared only when it is at least 95% likely to earn more new monthly revenue per
  visitor. Guardrails (churn, retention floor, refund and dispute ceilings) pause a risky arm
  automatically and say why. The winner rolls out in one click.
- **Day zero.** "Import 12 months from PayPal" turns Transaction Search history into a cohort
  retention heat map before the first experiment.
- **Explainable.** Every agent decision is logged; "why did arm B pause?" returns the decision and
  its numbers (Elastic-backed search when configured).
- **Honest about traffic.** PlanLab tells a merchant how many visitors a test needs and how long
  their traffic takes, and points small merchants to the Save Agent instead.

## How we built it

FastAPI backend, Next.js frontend. PayPal Subscriptions through PayPal's APIMatic-generated
`paypal-server-sdk`; KERNEL cloud browsers approve real sandbox checkouts and plan changes;
AG Grid for the workspace; Bryntum Gantt for the experiment timeline; Render blueprint for the API,
web app and nightly Save Agent sweep; SQLite or Supabase Postgres for storage. A public Postman
collection drives the whole demo without the UI.

## AI tools used and how

- **In the product:** Gemini (fallback model, then Groq, then a local Ollama model) proposes pricing
  arms, classifies cancel reasons, turns grid questions into AG Grid filters and writes decision
  explanations. Every LLM path has a deterministic fallback, and no LLM decides a winner: the
  statistics do.
- **To build it:** TODO: list the coding assistants and other AI tools the team used, and for what.

## Challenges we ran into

- The APIMatic SDK (paypal-server-sdk 2.4.0) drops `total_cycles: 0`, so every subscription expired
  after its first payment. We found it in the sandbox, patched it in `plan_model` and added a
  regression test.
- PayPal has no timed pause: we suspend and a nightly sweep reactivates.
- A plan can have only one regular billing cycle, so the discount is a paid trial cycle followed by
  the regular price.
- Moving a subscriber to another plan needs their approval on PayPal, so the Save Agent shows the
  approval link instead of changing a price silently.
- Sandbox billing cannot be fast-forwarded, so the demo replays a labelled simulated cohort next to
  the real sign-ups, and the UI shows both counts.

## Accomplishments we're proud of

- Every Save Agent action verified against the PayPal sandbox (7 Nov 2026).
- One Bayesian decision engine on a metric visible at checkout, not a 12-month LTV guess.
- TODO: anything else the team wants to highlight.

## What we learned

TODO: team's own words.

## What's next

- Run the 12-month import and Elastic search against a real merchant account.
- Wire an AG Studio Agent Framework agent to PlanLab's read-only tools (`GET /agent/tools`).
- TODO: team's roadmap.

## Links

- Hosted app: TODO (Render)
- Demo video (< 3 min, public): TODO
- Postman workspace: TODO
- Repo: https://github.com/maybenirav/paypal-hackathon-
