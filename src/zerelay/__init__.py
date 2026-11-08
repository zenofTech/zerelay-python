"""Official Python SDK for the Zerelay email and SMS API. https://zerelay.com/developer/docs"""
from ._client import DEFAULT_BASE_URL, Zerelay
from ._errors import ZerelayError, ZerelayWebhookError
from ._version import VERSION
from ._webhooks import verify_webhook

__version__ = VERSION
__all__ = ["Zerelay", "ZerelayError", "ZerelayWebhookError", "verify_webhook", "DEFAULT_BASE_URL", "VERSION"]
