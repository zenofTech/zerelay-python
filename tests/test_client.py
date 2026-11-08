import hashlib
import hmac
import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from zerelay import Zerelay, ZerelayError, ZerelayWebhookError, verify_webhook  # noqa: E402


class Mock:
    def __init__(self, responder):
        self.calls = []
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                mock.calls.append({"method": self.command, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": json.loads(body) if body else None})
                status, data, headers = responder(len(mock.calls))
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                payload = b"" if data is None else json.dumps(data).encode()
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()


class ClientTests(unittest.TestCase):
    def test_send_email_headers_and_idempotency(self):
        m = Mock(lambda n: (200, {"task_id": "t1", "status": "queued", "message": "ok", "test_mode": False}, None))
        zr = Zerelay("sk_test_x", base_url=m.url)
        out = zr.emails.send(to="a@example.com", subject="s", body="b")
        self.assertEqual(out["task_id"], "t1")
        call = m.calls[0]
        self.assertEqual(call["path"], "/send-email")
        self.assertEqual(call["headers"]["x-api-key"], "sk_test_x")
        self.assertTrue(call["headers"]["user-agent"].startswith("zerelay-python/"))
        self.assertEqual(len(call["headers"]["idempotency-key"]), 36)
        self.assertEqual(call["body"], {"to": "a@example.com", "subject": "s", "body": "b"})
        m.close()

    def test_429_retry_after_keeps_idempotency_key(self):
        m = Mock(lambda n: (429, {"detail": "Rate limit exceeded"}, {"Retry-After": "1"}) if n == 1 else (200, {"id": "sms_1"}, None))
        zr = Zerelay("sk_x", base_url=m.url)
        t0 = time.time()
        self.assertEqual(zr.sms.send(to="08031234567", body="hi")["id"], "sms_1")
        self.assertGreaterEqual(time.time() - t0, 0.9)
        self.assertEqual(m.calls[0]["headers"]["idempotency-key"], m.calls[1]["headers"]["idempotency-key"])
        m.close()

    def test_503_only_retried_when_safe(self):
        m = Mock(lambda n: (503, {"detail": "Worker unavailable"}, None))
        zr = Zerelay("sk_x", base_url=m.url, max_retries=1)
        with self.assertRaises(ZerelayError) as ctx:
            zr.emails.send_bulk(recipients=["a@example.com"], subject="s", body="b")
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(str(ctx.exception), "Worker unavailable")
        self.assertEqual(len(m.calls), 1)
        with self.assertRaises(ZerelayError):
            zr.emails.get("t1")
        self.assertEqual(len(m.calls), 3)
        m.close()

    def test_validation_detail(self):
        m = Mock(lambda n: (422, {"detail": [{"loc": ["body", "to"], "msg": "value is not a valid email address"}]}, None))
        with self.assertRaisesRegex(ZerelayError, r"body\.to: value is not a valid email address"):
            Zerelay("sk_x", base_url=m.url).emails.send(to="x", subject="s", body="b")
        m.close()

    def test_202_no_body_and_query(self):
        m = Mock(lambda n: (202, None, {"X-Zerelay-Test-Mode": "true"}) if n == 1 else (200, [], None))
        zr = Zerelay("sk_x", base_url=m.url + "/")
        self.assertEqual(zr.emails.mail_send({"personalizations": []}), {"status": 202, "test_mode": True})
        zr.sms.get("sms_a/b", refresh=True)
        self.assertEqual(m.calls[1]["path"], "/sms/messages/sms_a%2Fb?refresh=true")
        m.close()

    def test_network_error(self):
        zr = Zerelay("sk_x", base_url="http://127.0.0.1:1", max_retries=0, timeout=2)
        with self.assertRaisesRegex(ZerelayError, "failed"):
            zr.sms.wallet()

    def test_missing_key(self):
        saved = os.environ.pop("ZERELAY_API_KEY", None)
        with self.assertRaises(ValueError):
            Zerelay()
        if saved:
            os.environ["ZERELAY_API_KEY"] = saved


class WebhookTests(unittest.TestCase):
    secret = "whsec_test"

    def sign(self, body: bytes, ts: str) -> str:
        return "sha256=" + hmac.new(self.secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()

    def test_valid_and_legacy(self):
        body = json.dumps({"id": 1, "event": "delivered"}).encode()
        ts = str(int(time.time()))
        sig = self.sign(body, ts)
        self.assertEqual(verify_webhook(body, {"X-Zerelay-Signature": sig, "X-Zerelay-Timestamp": ts}, self.secret)["event"], "delivered")
        self.assertEqual(verify_webhook(body.decode(), {"x-smtp-signature": sig, "x-smtp-timestamp": ts}, self.secret)["id"], 1)

    def test_rejections(self):
        body = b'{"id":1}'
        ts = str(int(time.time()))
        sig = self.sign(body, ts)
        with self.assertRaisesRegex(ZerelayWebhookError, "Invalid webhook signature"):
            verify_webhook(body + b" ", {"x-zerelay-signature": sig, "x-zerelay-timestamp": ts}, self.secret)
        with self.assertRaisesRegex(ZerelayWebhookError, "Invalid webhook signature"):
            verify_webhook(body, {"x-zerelay-signature": sig, "x-zerelay-timestamp": ts}, "whsec_other")
        old = str(int(time.time()) - 3600)
        with self.assertRaisesRegex(ZerelayWebhookError, "tolerance"):
            verify_webhook(body, {"x-zerelay-signature": self.sign(body, old), "x-zerelay-timestamp": old}, self.secret)
        self.assertEqual(verify_webhook(body, {"x-zerelay-signature": self.sign(body, old), "x-zerelay-timestamp": old}, self.secret, tolerance_seconds=0)["id"], 1)
        with self.assertRaisesRegex(ZerelayWebhookError, "Missing"):
            verify_webhook(body, {}, self.secret)


if __name__ == "__main__":
    unittest.main()
