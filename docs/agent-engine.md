# Agent & Math Engine (Person B)

## Run

See the README's quick start. Tests: `python -m pytest` from `backend`.

## Endpoints

- `POST /agent/arms` `{goal, current_price, num_arms}` -> `{arms[], source, validation[]}`
  Each validation entry carries the exact `POST /v1/billing/plans` body (`paypal_payload`), built by the same
  function that publishes real plans. `product_id` and the experiment id in the name are placeholders until then.
- `POST /engine/decide` `{baseline, arms[], gamma, discount, goal}` -> `{action, arm_id, reasons, explanation, arms[]}`
  A one-off check from raw counts, using the Z-test rule below. Running experiments use `POST /experiments/{id}/decide`.

## How running experiments are judged (`backend/app/engine`)

`analysis.py` draws from posteriors for each option, and everything is read off the same draws:

- Conversion: `Beta(1 + signups, 1 + visitors - signups)`
- Monthly churn: `Gamma(0.5 + cancels, 1 / (5 + subscriber_months))`, a weak prior of about 10% a month
- Refund rate: `Beta(0.5 + refunds, 9.5 + payments - refunds)`, a weak prior of about 5%
- Value per subscriber (`ltv.plan_value`): each payment due at month `t`, within 12 months, counts
  `price * (1 - refund) * (1 - churn)^t / (1 + d)^t`, with `d = 0.01`. Monthly plans pay every month, annual plans
  pay once up front, a trial shifts every payment later, and a setup fee is added at sign-up.
- **Value per visitor = conversion x value per subscriber.** This decides the test.

`decision.decide_by_value`, in order:

1. Stop an arm when its monthly churn is more than `gamma` (default 1.30) times the baseline's with p < 0.01. This
   is a one-sided Wald test on the log rate ratio.
2. Keep testing until every live arm has at least 30 sign-ups and 15 subscriber-months.
3. An arm wins with `P(value per visitor > baseline) >= 1 - (1 - 0.99) / challengers`. The most valuable wins if
   several qualify.
4. If every challenger is at most `1 - that` likely to beat it, the baseline wins.
5. Otherwise keep testing, and say how close the nearest option is.

`guardrails.check_arm` then overrides that. An arm is stopped when, with at least 30 sign-ups, it is at least 90%
likely to be past the retention floor (projected 60-day retention), refund ceiling or dispute ceiling.

`watcher.py` runs the same guardrails without being asked, at stricter thresholds (99% confidence, churn p < 0.002)
because it never stops looking. It checks real data on a background thread every `GUARDRAIL_CHECK_SECONDS`, replays
daily checks across a simulated run when it's generated, and checks straight away when a limit changes. It only stops
options: it never picks a winner, never stops the baseline, skips arms the merchant resumed, and does nothing once an
experiment is rolled out or `auto_stop` is off. A simulated option also loses the visitors who would have arrived after
its stop.

A stop on real data also queues an alert (`alerts.py`). The alert is stored in the stop's record and sent on its own
thread, so a broken channel can't delay the stop, and it's retried after 1, 5, 15 and 60 minutes before counting as
failed. Stops on simulated data and stops from `decide` don't alert anyone.

Traffic (`bayes.shares_with_floor`): share = P(best on value per visitor), at least 5% per live arm, and at least
30% for the baseline. Results are seeded, so the same data always gives the same answer.

The thresholds come from simulating tests with weekly checks. `tests/test_math.py` keeps a smaller version of this
as a regression test:

| 3 options, 8 weekly checks | Result |
|---|---|
| Identical prices, 400 visitors a week per option: false winner | about 4%, against 15% for the previous rule |
| Identical prices: wrongly stopped | about 4-6% |
| 30% lift found, 1,000 visitors a week per option | about 80% |
| 30% lift found, 400 visitors a week per option | about 30% |

## The simple Z-test rule (`POST /engine/decide`)

- Significance: pooled two-proportion Z, significant when |Z| >= 1.96
- LTV: `sum_{t=1..12} M * S(t) / (1+d)^t`, `d` is the monthly discount (default 0.01)
- Guardrail: `C_arm/C_base > gamma` (default 1.30) AND one-sided churn p-value < 0.05
- Decision order: guardrail stop, then best significant-and-higher-LTV winner, else keep testing

This checks once from counts. It isn't suited to repeated checks: under weekly checks, a similar rule reached a 15%
false-winner rate, which is why running experiments use the rule above.

## APIMatic status

`app/validation.py` checks every arm against a JSON Schema covering the PayPal Create Plan fields we use. When
`APIMATIC_API_KEY` and `APIMATIC_VALIDATE_URL` are set, each valid plan also goes to that endpoint. This has only been
tested against a fake response, so confirm it with a real account before claiming it in the README.

## Gemini

`GEMINI_MODEL` defaults to `gemini-3.8-flash`. The API key goes in a header, not the URL. Past results go into the
prompt, and a response is only used if it passes `check_arms`: unique ids, one baseline at the current price, and
prices within 0.5x to 3x the current one. Anything else falls back to templates and logs why. This has only been tested
against fake responses, because no key was available.

See `docs/v2-plan.md` for the v2 scope, status and hand-offs.
