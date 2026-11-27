"""Messages to the user's phone: a Telegram bot, else a Home Assistant notify service.

Pure standard library (urllib), so the web interface uses the same code for its test button and for connecting a
Telegram chat. The settings are read per call: a change in .env applies from the next conversation.

Telegram: the user creates a bot with @BotFather and connects their chat on the web interface, which stores the
chat id. Jarvis only sends for now. Nobody else may poll the bot (getUpdates or a webhook), because Telegram hands
each update to one consumer only, and connecting reads the bot's updates.

Test off the Pi:  python3 -m core.notify "Hallo vom Dev-Rechner"
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

TELEGRAM_API = "https://api.telegram.org"  # tests point this at a fake server
TELEGRAM_MAX = 4096  # characters per Telegram message
HA_MAX = 4000  # notifications get cut by the phone anyway
TIMEOUT_S = 10


def telegram_config():
    """(bot token, chat id) from .env, '' when unset."""
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()


def ha_notify_service():
    return os.getenv("JARVIS_HA_NOTIFY", "").strip()


def channel():
    """'telegram', 'ha' or None when no channel is set up. Telegram wins when both are."""
    token, chat = telegram_config()
    if token and chat:
        return "telegram"
    if ha_notify_service() and os.getenv("HA_URL") and os.getenv("HA_TOKEN"):
        return "ha"
    return None


def send(text, title=None, link=None):
    """Sends a message on the configured channel. Returns (ok, detail): the channel used, or why it failed."""
    text = (text or "").strip()
    if not text:
        return False, "the message is empty"
    which = channel()
    if which == "telegram":
        token, chat = telegram_config()
        err = send_telegram(token, chat, compose(text, title, link))
    elif which == "ha":
        err = send_ha(os.getenv("HA_URL", ""), os.getenv("HA_TOKEN", ""), ha_notify_service(), text, title, link)
    else:
        return False, "no channel is set up (Telegram or a Home Assistant notify service)"
    if err:
        print(f"[Notify] Sending via {which} failed: {err}", flush=True)
        return False, err
    print(f"[Notify] Sent via {which} ({len(text)} chars).", flush=True)
    return True, which


def compose(text, title=None, link=None):
    """One plain-text message: title line, text, link (unless the text already has it)."""
    parts = [title.strip()] if title and title.strip() else []
    parts.append(text.strip())
    body = "\n\n".join(parts)
    if link and link.strip() and link.strip() not in body:
        body += "\n" + link.strip()
    return body


# --- Telegram --------------------------------------------------------------------------------

def telegram(token, method, params=None, timeout=TIMEOUT_S):
    """Calls a Bot API method. Returns (result, error): error is a short text for the user, result None then.
    Never logs or returns the URL, since it contains the token."""
    if not token:
        return None, "no bot token set"
    req = urllib.request.Request(f"{TELEGRAM_API}/bot{token}/{method}", data=json.dumps(params or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
        except (ValueError, OSError):
            return None, f"Telegram answered HTTP {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, f"Telegram is not reachable: {getattr(e, 'reason', e)}"
    if not isinstance(body, dict):
        return None, "Telegram sent no valid answer"
    if body.get("ok"):
        return body.get("result"), None
    return None, telegram_error(body)


def telegram_error(body):
    code = body.get("error_code")
    desc = str(body.get("description") or "")
    if code in (401, 404):
        return "the bot token was rejected"
    if code == 403:
        return "the bot was blocked or removed from the chat"
    if code == 409:
        return ("another program receives this bot's messages (a webhook or getUpdates): "
                "give Jarvis a bot of its own")
    if code == 429:
        wait = (body.get("parameters") or {}).get("retry_after", "?")
        return f"Telegram's rate limit, retry in {wait} s"
    if "chat not found" in desc.lower():
        return "chat not found: connect the chat again"
    return f"Telegram: {desc[:160]}" if desc else f"Telegram error {code}"


def chunks(text, size=TELEGRAM_MAX):
    """Splits text into pieces of at most size characters, at line breaks where possible."""
    out = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        if cut < size // 2:
            cut = text.rfind(" ", 0, size)
        if cut < size // 2:
            cut = size
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n ")
    if text:
        out.append(text)
    return out


def send_telegram(token, chat_id, text):
    """Sends plain text (no parse mode, so nothing needs escaping). Returns an error text or None."""
    if not chat_id:
        return "no Telegram chat connected"
    for part in chunks(text):
        _, err = telegram(token, "sendMessage", {"chat_id": chat_id, "text": part})
        if err:
            return err
    return None


def bot_name(token):
    """(username of the bot, error)."""
    me, err = telegram(token, "getMe")
    if err:
        return None, err
    name = (me or {}).get("username")
    return (name, None) if name else (None, "Telegram sent no bot name")


def find_start(token, code, offset=None):
    """Looks for the message that connects a chat: '/start <code>' (the t.me link sends that) or the bare code.

    Reads the bot's pending updates from offset on and confirms them, so the next call only sees newer ones.
    Returns (chat or None, next offset, error); chat = {"id": ..., "name": ...}."""
    params = {"timeout": 0, "allowed_updates": ["message"]}
    if offset:
        params["offset"] = offset
    updates, err = telegram(token, "getUpdates", params)
    if err:
        return None, offset, err
    pattern = re.compile(rf"^(/start(@\w+)?\s+)?{re.escape(code)}$")
    found = None
    for update in updates or []:
        offset = max(offset or 0, int(update.get("update_id", 0)) + 1)
        msg = update.get("message") or {}
        chat = msg.get("chat") or {}
        if found is None and "id" in chat and pattern.match((msg.get("text") or "").strip()):
            name = " ".join(p for p in (chat.get("first_name"), chat.get("last_name")) if p) \
                or chat.get("title") or chat.get("username") or str(chat["id"])
            found = {"id": chat["id"], "name": name}
    if updates:
        telegram(token, "getUpdates", {"offset": offset, "timeout": 0})  # confirm, so they are not read again
    return found, offset, None


# --- Home Assistant ---------------------------------------------------------------------------

def send_ha(url, token, service, text, title=None, link=None):
    """Calls notify.<name> in Home Assistant. Returns an error text or None."""
    domain, _, name = (service or "").partition(".")
    if domain != "notify" or not name:
        return "the notify service must look like notify.mobile_app_<phone>"
    if not url or not token:
        return "Home Assistant URL and token are needed"
    payload = {"message": text[:HA_MAX]}
    if title:
        payload["title"] = title
    if link:
        payload["data"] = {"clickAction": link, "url": link}  # Android app: clickAction, iOS app: url
    req = urllib.request.Request(f"{url.rstrip('/')}/api/services/notify/{name}", data=json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S):
            return None
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return "Home Assistant rejected the token"
        if e.code in (400, 404):
            return f"Home Assistant has no service {service}"
        return f"Home Assistant answered HTTP {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return f"Home Assistant is not reachable: {getattr(e, 'reason', e)}"


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    print(send(" ".join(sys.argv[1:]) or "Test message from Jarvis."))
