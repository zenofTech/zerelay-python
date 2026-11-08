# Zerelay for Python

Official Python SDK for the [Zerelay](https://zerelay.com) email and SMS API. No dependencies; Python 3.8+.

```bash
pip install zerelay
```

## Send an email

```python
from zerelay import Zerelay

zerelay = Zerelay()  # reads ZERELAY_API_KEY, or pass the key: Zerelay("sk_...")

result = zerelay.emails.send(
    to="jane@example.com",
    subject="Your order has shipped",
    body="<p>It will arrive on Friday.</p>",
)
print(result["task_id"])
```

For cc/bcc, separate text and HTML parts, templates or categories, `emails.mail_send()` takes the SendGrid v3 `/mail/send` format:

```python
zerelay.emails.mail_send({
    "personalizations": [{"to": [{"email": "jane@example.com"}], "cc": [{"email": "ops@example.com"}]}],
    "from": {"email": "orders@yourdomain.com", "name": "Your Store"},
    "subject": "Order #1001",
    "content": [{"type": "text/html", "value": "<p>Thanks for your order</p>"}],
})
```

## Send an SMS

```python
message = zerelay.sms.send(to="08031234567", body="Your code is 4821")
print(message["status"], message["cost_kobo"])
```

## Test mode

Create a **test key** (`sk_test_...`) under **API Keys** in the dashboard. Test keys check every request exactly like a live key does, but nothing is sent, charged or counted toward your quota. Webhooks still fire, marked `"test_mode": true`. Send to `bounce@simulator.zerelay.com` to simulate a bounce.

## Verify webhooks

Verify the **raw** request body:

```python
from zerelay import verify_webhook, ZerelayWebhookError

# Flask
@app.post("/webhooks/zerelay")
def zerelay_webhook():
    try:
        event = verify_webhook(request.get_data(), request.headers, os.environ["ZERELAY_WEBHOOK_SECRET"])
    except ZerelayWebhookError as exc:
        return str(exc), 400
    # event["event"] is e.g. "delivered", "bounce", "sms.delivered"; de-duplicate on event["id"]
    return "", 200
```

In Django use `request.body`. In FastAPI use `await request.body()`.

## Errors and retries

Failed requests raise `ZerelayError` with `status`, `message` (the API's explanation) and `body`. The client retries automatically:

- **Rate limits (429)** are always retried, after the `Retry-After` delay.
- **Network errors and 502/503/504** are retried only when repeating the request is safe. That covers GETs, plus `emails.send()` and `sms.send()`, which send an automatic `Idempotency-Key`.

Change this with `Zerelay(max_retries=0)`.

Any other endpoint can be called with `zerelay.request("GET", "/path", query={...}, body={...})`. The full reference is at <https://api.zerelay.com/docs>.

## License

MIT
