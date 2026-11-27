"""Receives the event 'jarvis_trigger' from Home Assistant over its WebSocket API (push, no polling).

A daemon thread holds one connection: auth with HA_TOKEN, subscribe_events for EVENT_TYPE, then every event lands
in a queue that main.py's idle loop empties (see core/events.py for what a trigger does). Lost connections are
retried with a growing pause; a rejected token is retried every few minutes. Off without HA_URL / HA_TOKEN or with
JARVIS_EVENTS=0. Uses the sync client of 'websockets', which comes with google-genai.

Try it: Home Assistant -> Developer tools -> Events -> event type 'jarvis_trigger', data e.g.
  prompt: Say hello and ask who is there.
"""
import json
import os
import queue
import threading
import time

from core import status

EVENT_TYPE = "jarvis_trigger"
BACKOFF_S = (5, 10, 30, 60)
AUTH_RETRY_S = 300

_queue = queue.Queue()


class AuthError(Exception):
    pass


def enabled():
    return os.getenv("JARVIS_EVENTS", "1") != "0" and bool(os.getenv("HA_URL")) and bool(os.getenv("HA_TOKEN"))


def ws_url(http_url):
    """http://host:8123 -> ws://host:8123/api/websocket (https -> wss)."""
    url = http_url.rstrip("/")
    if url.startswith("https://"):
        url = "wss://" + url[len("https://"):]
    elif url.startswith("http://"):
        url = "ws://" + url[len("http://"):]
    return url + "/api/websocket"


def start():
    """Starts the listener thread. Returns the thread, or None when events are off."""
    if not enabled():
        print("[Events] Off (needs HA_URL and HA_TOKEN, and JARVIS_EVENTS not 0).", flush=True)
        return None
    try:
        import websockets.sync.client  # noqa: F401
    except ImportError:
        print("[Events] Off: the package 'websockets' is missing.", flush=True)
        return None
    t = threading.Thread(target=_run, args=(ws_url(os.getenv("HA_URL")), os.getenv("HA_TOKEN")),
                         daemon=True, name="ha-events")
    t.start()
    return t


def take():
    """The oldest waiting trigger as (received time, event data), or None. Never blocks."""
    try:
        return _queue.get_nowait()
    except queue.Empty:
        return None


def _run(url, token):
    attempt = 0
    while True:
        try:
            listen(url, token, on_ready=lambda: _set_connected(True))
            pause = BACKOFF_S[0]  # closed after a working connection (HA restart): reconnect soon
            attempt = 0
        except AuthError as e:
            pause = AUTH_RETRY_S
            print(f"[Events] Home Assistant rejected the token ({e}), retrying in {pause // 60} min.", flush=True)
        except Exception as e:
            pause = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
            attempt += 1
            print(f"[Events] Connection to Home Assistant lost or failed ({type(e).__name__}: {e}), "
                  f"retrying in {pause}s.", flush=True)
        _set_connected(False)
        time.sleep(pause)


def _set_connected(value):
    status.update(events="connected" if value else "disconnected")


def listen(url, token, on_ready=None, stop=None):
    """One connection: authenticate, subscribe, queue events until the connection ends (or stop is set)."""
    from websockets.sync.client import connect
    with connect(url, open_timeout=10, ping_interval=30, ping_timeout=20, max_size=2 ** 20) as ws:
        msg = json.loads(ws.recv(timeout=10))
        if msg.get("type") != "auth_required":
            raise RuntimeError(f"unexpected greeting {msg.get('type')!r}")
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        msg = json.loads(ws.recv(timeout=10))
        if msg.get("type") == "auth_invalid":
            raise AuthError(msg.get("message") or "auth_invalid")
        if msg.get("type") != "auth_ok":
            raise RuntimeError(f"unexpected auth answer {msg.get('type')!r}")
        ws.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": EVENT_TYPE}))
        msg = json.loads(ws.recv(timeout=10))
        if not msg.get("success"):
            raise RuntimeError(f"subscribe failed: {msg.get('error')}")
        print(f"[Events] Listening for '{EVENT_TYPE}' from Home Assistant.", flush=True)
        if on_ready:
            on_ready()
        while not (stop and stop.is_set()):
            try:
                raw = ws.recv(timeout=1 if stop else None)
            except TimeoutError:
                continue
            msg = json.loads(raw)
            if msg.get("type") != "event":
                continue
            data = (msg.get("event") or {}).get("data") or {}
            print(f"[Events] Trigger received: {str(data.get('kind') or data.get('prompt') or data)[:80]}", flush=True)
            _queue.put((time.time(), data))
