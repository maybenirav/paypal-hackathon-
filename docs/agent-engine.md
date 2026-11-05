# Agent & Math Engine (Person B)

## Run

```
cd backend
pip install -r requirements.txt
cp .env.example .env        # optional: GEMINI_API_KEY enables Gemini; without it a deterministic fallback is used
python -m pytest            # 28 tests, hand-calculated expectations
uvicorn app.main:app --reload

cd frontend
npm install
npm run dev                 # http://localhost:3000, API at NEXT_PUBLIC_API_URL (default :8000)
```

## Endpoints

- `POST /agent/arms` `{goal, current_price, num_arms}` -> `{arms[], source, validation[]}`
  Each validation entry carries the exact `POST /v1/billing/plans` body (`paypal_payload`) that Person A can send,
  after replacing the placeholder `product_id`.
- `POST /engine/decide` `{baseline, arms[], gamma, discount, goal}` -> `{action, arm_id, reasons, explanation, arms[]}`
  `action` is `stop_arm | winner | keep_testing`.

## Formulas (`backend/app/engine`)

- Significance: pooled two-proportion Z, significant when |Z| >= 1.96
- LTV: `sum_{t=1..12} M * S(t) / (1+d)^t`, `d` is the monthly discount (default 0.01)
- Guardrail: `C_arm/C_base > gamma` (default 1.30) AND one-sided churn p-value < 0.05
- Decision order: guardrail stop, then best significant-and-higher-LTV winner, else keep testing

## APIMatic status

`app/validation.py` checks every arm against a JSON Schema covering the PayPal Create Plan fields we use.
A remote APIMatic check is wired in but only runs when `APIMATIC_API_KEY` and `APIMATIC_VALIDATE_URL` are set.
Not yet verified against a real APIMatic account; confirm access before claiming it in the README.

## Assumptions to confirm with the team

- `churned`/`signups` are cumulative counts per arm at the decision time.
- `price` in decide requests is the monthly-equivalent revenue (annual price / 12).
- The frontend results table uses editable sample numbers until Person A's API exposes real metrics.
