"""Telegram delivery for the th-trader skills. Standard library only.

The bot token and chat id come from the environment, never from a file or the chat:

    TH_TRADER_TELEGRAM_TOKEN     the token BotFather gave you
    TH_TRADER_TELEGRAM_CHAT_ID   your chat id with the bot

The token never appears in output, errors or files: a failure is reported by HTTP status or
by the kind of network error only.
"""
import os
import urllib.error
import urllib.parse
import urllib.request

TOKEN_VAR = "TH_TRADER_TELEGRAM_TOKEN"
CHAT_VAR = "TH_TRADER_TELEGRAM_CHAT_ID"
TIMEOUT = 10


def send_telegram(text, opener=urllib.request.urlopen):
    """-> (sent, why). Never raises for a send that fails."""
    token = os.environ.get(TOKEN_VAR, "").strip()
    chat = os.environ.get(CHAT_VAR, "").strip()
    if not token or not chat:
        return False, "Telegram not set up: TH_TRADER_TELEGRAM_TOKEN / _CHAT_ID"
    body = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with opener(req, timeout=TIMEOUT) as r:
            r.read()
    except urllib.error.HTTPError as e:
        e.close()
        return False, "Telegram answered HTTP %d" % e.code
    except (urllib.error.URLError, OSError) as e:
        return False, "Telegram could not be reached (%s)" % type(getattr(e, "reason", e)).__name__
    return True, "sent to Telegram"
