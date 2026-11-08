import json
import os
import random
import time
import uuid
from typing import Any, Dict, List, Optional, Union
from urllib import error as urlerror
from urllib import parse, request

from ._errors import ZerelayError
from ._version import VERSION

DEFAULT_BASE_URL = "https://api.zerelay.com"
_RETRYABLE = {429, 502, 503, 504}


def _detail(body: Any, fallback: str) -> str:
    d = body.get("detail") if isinstance(body, dict) else None
    if isinstance(d, list):
        parts = []
        for item in d:
            if isinstance(item, dict) and item.get("msg"):
                parts.append(f"{'.'.join(str(x) for x in item.get('loc', []))}: {item['msg']}")
            else:
                parts.append(str(item))
        return "; ".join(parts)
    return str(d) if d else fallback


class Zerelay:
    """Zerelay API client.

        zr = Zerelay("sk_...")            # or set ZERELAY_API_KEY
        zr.emails.send(to="jane@example.com", subject="Hi", body="<p>Hello</p>")
        zr.sms.send(to="08031234567", body="Your code is 1234")
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        api_key = api_key or os.environ.get("ZERELAY_API_KEY")
        if not api_key:
            raise ValueError("Missing Zerelay API key: pass it to Zerelay(api_key) or set ZERELAY_API_KEY")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.emails = Emails(self)
        self.sms = Sms(self)

    def key_info(self) -> Dict[str, Any]:
        """What the configured key is: live/test, full/send-only, and the account's rate limits."""
        return self.request("GET", "/api-keys/current")

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Dict[str, Any]] = None,
        body: Any = None,
        headers: Optional[Dict[str, str]] = None,
        idempotent: bool = False,
    ) -> Any:
        """Call any endpoint (see https://api.zerelay.com/docs). 429s are always retried;
        network errors and 502-504 only for GETs and requests carrying an Idempotency-Key."""
        url = self.base_url + path
        if query:
            clean = {k: ("true" if v is True else "false" if v is False else v) for k, v in query.items() if v is not None}
            if clean:
                url += "?" + parse.urlencode(clean)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        all_headers = {
            "X-API-Key": self.api_key,
            "User-Agent": f"zerelay-python/{VERSION}",
            "Accept": "application/json",
        }
        if data is not None:
            all_headers["Content-Type"] = "application/json"
        all_headers.update(headers or {})
        can_retry_failures = method.upper() == "GET" or idempotent

        attempt = 0
        while True:
            req = request.Request(url, data=data, method=method.upper(), headers=all_headers)
            try:
                with request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    status = resp.status
                    test_mode = resp.headers.get("X-Zerelay-Test-Mode") == "true"
                if not raw:
                    return {"status": status, "test_mode": test_mode}
                return json.loads(raw)
            except urlerror.HTTPError as exc:
                raw = exc.read()
                try:
                    parsed: Any = json.loads(raw) if raw else None
                except ValueError:
                    parsed = raw.decode("utf-8", "replace")
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                retry_after_s = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else None
                retryable = exc.code == 429 or (can_retry_failures and exc.code in _RETRYABLE)
                if retryable and attempt < self.max_retries:
                    time.sleep(min(retry_after_s, 60) if retry_after_s else self._backoff(attempt))
                    attempt += 1
                    continue
                raise ZerelayError(_detail(parsed, f"HTTP {exc.code}"), status=exc.code, body=parsed, retry_after=retry_after_s) from None
            except (urlerror.URLError, TimeoutError, ConnectionError, OSError) as exc:
                if can_retry_failures and attempt < self.max_retries:
                    time.sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                raise ZerelayError(f"Request to {method.upper()} {path} failed: {exc}") from None

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(8.0, 0.5 * 2 ** attempt) * random.uniform(0.8, 1.2)


def _drop_none(values: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}


class Emails:
    def __init__(self, client: Zerelay) -> None:
        self._c = client

    def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        sender_identity_id: Optional[int] = None,
        attachments: Optional[List[Dict[str, str]]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send one email. Retries are safe: an Idempotency-Key is generated unless you pass one."""
        return self._c.request(
            "POST", "/send-email",
            body=_drop_none({"to": to, "subject": subject, "body": body, "sender_identity_id": sender_identity_id, "attachments": attachments}),
            headers={"Idempotency-Key": idempotency_key or str(uuid.uuid4())},
            idempotent=True,
        )

    def send_bulk(
        self,
        *,
        recipients: List[str],
        subject: str,
        body: str,
        sender_identity_id: Optional[int] = None,
        attachments: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """One message to many recipients (each gets their own copy)."""
        return self._c.request(
            "POST", "/send-bulk-emails",
            body=_drop_none({"recipients": recipients, "subject": subject, "body": body, "sender_identity_id": sender_identity_id, "attachments": attachments}),
        )

    def mail_send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Full control (cc, bcc, separate text/html, templates, categories): SendGrid v3 format."""
        return self._c.request("POST", "/v3/mail/send", body=payload)

    def get(self, task_id: str) -> Dict[str, Any]:
        return self._c.request("GET", f"/email-status/{parse.quote(task_id, safe='')}")

    def list(self, **query: Any) -> List[Dict[str, Any]]:
        return self._c.request("GET", "/emails", query=query)


class Sms:
    def __init__(self, client: Zerelay) -> None:
        self._c = client

    def send(
        self,
        *,
        to: Union[str, List[str]],
        body: str,
        sender_id: Optional[str] = None,
        route: Optional[str] = None,
        schedule_at: Optional[str] = None,
        reference: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send an SMS to one or more Nigerian numbers, paid from your SMS wallet."""
        return self._c.request(
            "POST", "/sms/send",
            body=_drop_none({"to": to, "body": body, "sender_id": sender_id, "route": route, "schedule_at": schedule_at, "reference": reference}),
            headers={"Idempotency-Key": idempotency_key or str(uuid.uuid4())},
            idempotent=True,
        )

    def quote(self, *, to: Union[str, List[str]], body: str, sender_id: Optional[str] = None, route: Optional[str] = None) -> Dict[str, Any]:
        """Price a send without sending or charging."""
        return self._c.request("POST", "/sms/quote", body=_drop_none({"to": to, "body": body, "sender_id": sender_id, "route": route}), idempotent=True)

    def get(self, message_id: str, *, refresh: bool = False) -> Dict[str, Any]:
        return self._c.request("GET", f"/sms/messages/{parse.quote(message_id, safe='')}", query={"refresh": refresh})

    def list(self, **query: Any) -> List[Dict[str, Any]]:
        return self._c.request("GET", "/sms/messages", query=query)

    def cancel(self, message_id: str) -> Dict[str, Any]:
        """Cancel a scheduled message (fully refunded)."""
        return self._c.request("POST", f"/sms/messages/{parse.quote(message_id, safe='')}/cancel")

    def wallet(self) -> Dict[str, Any]:
        return self._c.request("GET", "/sms/wallet")
