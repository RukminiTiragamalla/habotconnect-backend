import logging
import uuid

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def initiate_payment(booking_id, amount):
    url = f"{settings.PAYMENT_GATEWAY_BASE_URL}/charges"
    payload = {
        "booking_id": booking_id,
        "amount": str(amount),
        "currency": "USD",
    }

    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as exc:
        logger.error("Payment gateway unreachable for booking %s: %s", booking_id, exc)
        return {"transaction_ref": f"local-{uuid.uuid4()}", "status": "pending"}