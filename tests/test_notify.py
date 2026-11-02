import io
import json
import urllib.error
import urllib.parse

import notify

TOKEN = "123456:SECRET-token-value"


class Recorder:
    def __init__(self, exc=None):
        self.calls = []
        self.exc = exc

    def __call__(self, req, timeout=None):
        self.calls.append((req, timeout))
        if self.exc:
            raise self.exc
        return io.BytesIO(json.dumps({"ok": True}).encode())


def test_no_env_vars_means_no_call(monkeypatch):
    monkeypatch.delenv("TH_TRADER_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TH_TRADER_TELEGRAM_CHAT_ID", raising=False)
    rec = Recorder()
    sent, why = notify.send_telegram("hello", opener=rec)
    assert sent is False and "not set up" in why
    assert rec.calls == []


def test_sends_to_the_bot_url(monkeypatch):
    monkeypatch.setenv("TH_TRADER_TELEGRAM_TOKEN", TOKEN)
    monkeypatch.setenv("TH_TRADER_TELEGRAM_CHAT_ID", "987")
    rec = Recorder()
    sent, why = notify.send_telegram("PTT hit the stop", opener=rec)
    assert sent is True, why
    req, timeout = rec.calls[0]
    assert req.full_url == "https://api.telegram.org/bot%s/sendMessage" % TOKEN
    body = urllib.parse.parse_qs(req.data.decode())
    assert body["chat_id"] == ["987"] and body["text"] == ["PTT hit the stop"]
    assert timeout == 10
    assert TOKEN not in why


def test_http_error_reports_status_not_token(monkeypatch):
    monkeypatch.setenv("TH_TRADER_TELEGRAM_TOKEN", TOKEN)
    monkeypatch.setenv("TH_TRADER_TELEGRAM_CHAT_ID", "987")
    err = urllib.error.HTTPError("https://api.telegram.org/bot%s/sendMessage" % TOKEN, 401, "Unauthorized",
                                 {}, io.BytesIO(b'{"ok":false}'))
    sent, why = notify.send_telegram("x", opener=Recorder(err))
    assert sent is False and "401" in why
    assert TOKEN not in why and "SECRET" not in why


def test_network_error_hides_token(monkeypatch):
    monkeypatch.setenv("TH_TRADER_TELEGRAM_TOKEN", TOKEN)
    monkeypatch.setenv("TH_TRADER_TELEGRAM_CHAT_ID", "987")
    err = urllib.error.URLError("cannot reach https://api.telegram.org/bot%s/sendMessage" % TOKEN)
    sent, why = notify.send_telegram("x", opener=Recorder(err))
    assert sent is False and TOKEN not in why
