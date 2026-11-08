import hashlib
import hmac
import json
import time
from typing import Any, Mapping, Optional, Union

from ._errors import ZerelayWebhookError


def _header(headers: Mapping[str, Any], name: str) -> Optional[str]:
    getter = getattr(headers, "get", None)
    if getter is not None:
        value = getter(name)
        if value is None:
            value = getter(name.lower())
        if value is not None:
            return value[0] if isinstance(value, (list, tuple)) else str(value)
    lower = name.lower()
    for key, value in dict(headers).items():
        if str(key).lower() == lower:
            return value[0] if isinstance(value, (list, tuple)) else str(value)
    return None


def verify_webhook(
    payload: Union[bytes, str],
    headers: Mapping[str, Any],
    secret: str,
    tolerance_seconds: int = 300,
    now: Optional[float] = None,
) -> Any:
    """Verify a Zerelay webhook and return the parsed event.

    Pass the RAW request body (e.g. Flask `request.get_data()`, Django `request.body`,
    FastAPI `await request.body()`), not re-serialised JSON. Raises ZerelayWebhookError if
    the signature is wrong or the timestamp is older than `tolerance_seconds` (0 disables).
    """
    if not secret:
        raise ZerelayWebhookError("Missing webhook signing secret")
    raw = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
    signature = _header(headers, "X-Zerelay-Signature") or _header(headers, "X-SMTP-Signature")
    timestamp = _header(headers, "X-Zerelay-Timestamp") or _header(headers, "X-SMTP-Timestamp")
    if not signature or not timestamp:
        raise ZerelayWebhookError("Missing X-Zerelay-Signature or X-Zerelay-Timestamp header")
    try:
        ts = int(timestamp)
    except ValueError:
        raise ZerelayWebhookError("Invalid X-Zerelay-Timestamp header") from None
    current = time.time() if now is None else now
    if tolerance_seconds and abs(current - ts) > tolerance_seconds:
        raise ZerelayWebhookError("Webhook timestamp is outside the allowed tolerance (possible replay)")
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ZerelayWebhookError("Invalid webhook signature")
    try:
        return json.loads(raw)
    except ValueError:
        raise ZerelayWebhookError("Webhook body is not valid JSON") from None
