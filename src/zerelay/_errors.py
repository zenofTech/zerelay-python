from typing import Any, Optional


class ZerelayError(Exception):
    """An API error. `status` is the HTTP status (None for network errors)."""

    def __init__(self, message: str, *, status: Optional[int] = None, body: Any = None, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.body = body
        self.retry_after = retry_after


class ZerelayWebhookError(Exception):
    """A webhook failed verification (bad signature, stale timestamp, missing headers)."""
