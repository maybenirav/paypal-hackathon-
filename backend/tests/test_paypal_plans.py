from app.paypal_plans import build_plan_payload
from app.schemas import Arm


def test_monthly_plan_without_trial():
    p = build_plan_payload(Arm(arm_id="A", name="Base", price=10), "PROD-1", "exp1")
    assert len(p["billing_cycles"]) == 1
    cycle = p["billing_cycles"][0]
    assert cycle["tenure_type"] == "REGULAR" and cycle["sequence"] == 1
    assert cycle["pricing_scheme"]["fixed_price"] == {"value": "10.00", "currency_code": "USD"}
    assert "setup_fee" not in p["payment_preferences"]


def test_yearly_plan_with_trial_and_setup_fee():
    arm = Arm(arm_id="B", name="Annual", price=99, interval="YEAR", trial_days=14, setup_fee=5)
    p = build_plan_payload(arm, "PROD-1", "exp1")
    trial, regular = p["billing_cycles"]
    assert trial["tenure_type"] == "TRIAL"
    assert trial["frequency"] == {"interval_unit": "DAY", "interval_count": 14}
    assert "pricing_scheme" not in trial  # free trial
    assert regular["sequence"] == 2 and regular["frequency"]["interval_unit"] == "YEAR"
    assert p["payment_preferences"]["setup_fee"]["value"] == "5.00"
