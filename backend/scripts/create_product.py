"""Run once: creates the PayPal catalog product that all plan arms belong to."""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.paypal_client import paypal_request  # noqa: E402

product = paypal_request(
    "POST",
    "/v1/catalogs/products",
    json={
        "name": "PlanLab Demo SaaS",
        "description": "Demo subscription product for PlanLab pricing experiments",
        "type": "SERVICE",
        "category": "SOFTWARE",
    },
)

print("Product created!")
print("Add this line to your .env:")
print(f"PAYPAL_PRODUCT_ID={product['id']}")
