from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Arm(BaseModel):
    """Arm JSON schema: output of the pricing agent, input to the PayPal plan builder."""
    arm_id: str
    name: str
    price: float = Field(gt=0, description="Price per billing interval")
    currency: str = "USD"
    interval: Literal["MONTH", "YEAR"] = "MONTH"
    trial_days: int = Field(default=0, ge=0, le=90)
    setup_fee: float = Field(default=0, ge=0)
    is_baseline: bool = False
    reason: str = ""
    plan_id: str | None = None   # PayPal plan id, set once Person A creates the plan

    @property
    def monthly_equivalent(self) -> float:
        return self.price / 12 if self.interval == "YEAR" else self.price


class GoalRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=500)
    current_price: float = Field(default=10.0, gt=0, le=1_000_000)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    num_arms: int = Field(default=3, ge=2, le=4)


class ArmsResponse(BaseModel):
    arms: list[Arm]
    source: Literal["gemini", "fallback"]
    validation: list["ArmValidation"]


class ArmValidation(BaseModel):
    arm_id: str
    valid: bool
    errors: list[str] = []
    paypal_payload: dict | None = None


class ArmInput(BaseModel):
    arm_id: str
    price: float = Field(gt=0, description="Monthly-equivalent revenue per subscriber")
    visitors: int = Field(ge=0)
    signups: int = Field(ge=0)
    churned: int = Field(ge=0)
    survival: list[float] | None = None


class DecideRequest(BaseModel):
    baseline: ArmInput
    arms: list[ArmInput]
    gamma: float = 1.30
    discount: float = 0.01
    goal: str | None = None


ArmsResponse.model_rebuild()
