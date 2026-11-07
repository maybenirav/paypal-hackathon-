# Sandbox verification (7 Nov 2026)

Every Save Agent action was run against the real PayPal sandbox with
`backend/scripts/verify_save_actions.py`. The sandbox buyer was a KERNEL cloud browser, and the
PayPal calls went through PayPal's APIMatic-generated Server SDK.

| Step | PayPal call | Result |
|---|---|---|
| Create base, Lite and discount plans | `POST /v1/billing/plans` (SDK) | 3 ACTIVE plans |
| Buyer signs up | `POST /v1/billing/subscriptions`, then KERNEL approves on PayPal | `ACTIVE`, first $15.00 payment captured |
| Pause | `POST .../suspend` | `SUSPENDED` |
| Resume after the pause | `POST .../activate` | `ACTIVE` |
| Downgrade | `POST .../revise` to the Lite plan, then the buyer approves the link (KERNEL) | `plan_id` switched to Lite |
| Discount | `POST .../revise` to the discount plan, then the buyer approves the link (KERNEL) | `plan_id` switched to the discount plan |
| Let go | `POST .../cancel` | `CANCELLED` |

An end-to-end run through the API also worked:

1. `POST /experiments`
2. `POST /experiments/{id}/paypal/publish` created 3 plans.
3. `POST /experiments/{id}/kernel/checkout` produced a real `ACTIVE` subscription. Its visit, sign-up and payment show up in the metrics as `real` events.
4. `POST /save/plans/setup`
5. `POST /save/cancel-intent` with the reason "too expensive". The Save Agent chose the discount and called `revise`, and PayPal returned an approval link for the customer.

## What we learned

- **A plan change needs the buyer's approval.** `revise` to another plan returns an `approve` link. That is the consent step for downgrades and discounts, so the Save Agent shows the link to the customer instead of changing their price silently.
- **PayPal has no timed pause.** A pause is `suspend` now and `activate` later. The nightly `/save/sweep` (a Render cron) runs the reactivation.
- **A plan can have only one regular billing cycle.** The discount plan is therefore a paid **trial** cycle (e.g. 3 × $12) followed by the regular $15 cycle.
- **Bug in the SDK (paypal-server-sdk 2.4.0).** `BillingCycle.from_dictionary` drops `total_cycles: 0` ("bill forever") because 0 is falsy, so PayPal defaults to 1 cycle. Every subscription then expired after its first payment, which is how we found it: our first test subscription went to `EXPIRED`. `app/paypal_sdk.plan_model` sets the value back on the model, and `tests/test_fixes.py` guards against regressions.

## Reproduce

```bash
cd backend
python scripts/verify_save_actions.py --screens ./kernel-shots
```

This needs `PAYPAL_CLIENT_ID/SECRET`, `PAYPAL_PRODUCT_ID`, `KERNEL_API_KEY` and `PAYPAL_SANDBOX_BUYER_1_EMAIL/PASSWORD` in `backend/.env`.
