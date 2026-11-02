import datetime
import json
import os
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMERS_FILE = os.path.join(ROOT, "data", "timers.json")
ALARM_SOUND = os.path.join(ROOT, "assets", "sounds", "alarm.pcm")

# Ringing stops by itself after this many seconds
ALARM_TIMEOUT = float(os.getenv("JARVIS_ALARM_TIMEOUT", "120"))
# Timers that were due while Jarvis was down still ring if they are at most this late, otherwise they are dropped
MAX_LATE = 600
# The alarm sound is queued in short pieces so a stop takes effect within one piece (24 kHz, s16le mono)
PIECE_BYTES = 24000 * 2 // 4
ORANGE = (255, 100, 0)


def _fmt_duration(seconds):
    seconds = max(0, int(round(seconds)))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    parts = []
    if h:
        parts.append(f"{h} Std.")
    if m:
        parts.append(f"{m} Min.")
    if s and not h:
        parts.append(f"{s} Sek.")
    return " ".join(parts) or "0 Sek."


class TimerManager:
    """Timers and alarms that ring through the speaker. Persisted in data/timers.json so they survive restarts.

    A ringing alarm stops on the wake word, the button, or after ALARM_TIMEOUT.
    """

    def __init__(self, path=TIMERS_FILE):
        self.path = path
        self._lock = threading.Lock()
        self._timers = []  # dicts: id, kind ('timer' | 'alarm'), label, due (epoch seconds)
        self._next_id = 1
        self._ringing_since = None
        self._ringing_labels = []
        self._pos = 0  # read position in the looped alarm sound
        self._get_audio = None
        self._hardware = None
        self._on_ring = None
        self._sound = None
        self._load()

    # --- persistence ---

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._timers = data.get("timers", [])
            self._next_id = data.get("next_id", 1)
        except FileNotFoundError:
            return
        except Exception as e:
            print(f"[Timer] Could not read {self.path}: {e}", flush=True)
            return
        now = time.time()
        stale = [t for t in self._timers if now - t["due"] > MAX_LATE]
        if stale:
            print(f"[Timer] Dropping {len(stale)} timer(s) missed while offline: {[t['label'] for t in stale]}", flush=True)
            self._timers = [t for t in self._timers if t not in stale]
            self._save()
        if self._timers:
            print(f"[Timer] Loaded {len(self._timers)} active timer(s).", flush=True)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"timers": self._timers, "next_id": self._next_id}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except Exception as e:
            print(f"[Timer] Could not save timers: {e}", flush=True)

    # --- managing timers (called from the manage_timer tool) ---

    def _add(self, kind, due, label):
        with self._lock:
            timer = {"id": str(self._next_id), "kind": kind, "label": label, "due": due}
            self._next_id += 1
            self._timers.append(timer)
            self._timers.sort(key=lambda t: t["due"])
            self._save()
        print(f"[Timer] Set {kind} #{timer['id']} '{label}' for {datetime.datetime.fromtimestamp(due):%Y-%m-%d %H:%M:%S}", flush=True)
        return timer

    def set_timer(self, seconds, label=""):
        seconds = int(seconds)
        if seconds <= 0:
            return "Fehler: Die Dauer muss größer als 0 Sekunden sein."
        timer = self._add("timer", time.time() + seconds, label or "Timer")
        return f"Timer #{timer['id']} läuft {_fmt_duration(seconds)} (klingelt um {self._clock(timer['due'])} Uhr)."

    def set_alarm(self, time_str, date_str="", label=""):
        try:
            hour, minute = (int(x) for x in (time_str.strip().replace(".", ":") + ":0").split(":")[:2])
            target_time = datetime.time(hour, minute)
        except ValueError:
            return f"Fehler: Uhrzeit '{time_str}' nicht verstanden, erwartet wird HH:MM."
        now = datetime.datetime.now()
        if date_str:
            try:
                target_date = datetime.date.fromisoformat(date_str.strip())
            except ValueError:
                return f"Fehler: Datum '{date_str}' nicht verstanden, erwartet wird YYYY-MM-DD."
            due_dt = datetime.datetime.combine(target_date, target_time)
            if due_dt <= now:
                return "Fehler: Dieser Zeitpunkt liegt in der Vergangenheit."
        else:
            due_dt = datetime.datetime.combine(now.date(), target_time)
            if due_dt <= now:
                due_dt += datetime.timedelta(days=1)
        timer = self._add("alarm", due_dt.timestamp(), label or "Wecker")
        day = "heute" if due_dt.date() == now.date() else ("morgen" if due_dt.date() == now.date() + datetime.timedelta(days=1) else f"am {due_dt:%d.%m.}")
        return f"Wecker #{timer['id']} gestellt für {day} {due_dt:%H:%M} Uhr (in {_fmt_duration(due_dt.timestamp() - time.time())})."

    def describe(self):
        """Human-readable list of active timers, or '' if there are none."""
        with self._lock:
            timers = list(self._timers)
        now = time.time()
        lines = []
        for t in timers:
            kind = "Timer" if t["kind"] == "timer" else "Wecker"
            lines.append(f"#{t['id']} {kind} '{t['label']}': klingelt {self._clock(t['due'], with_date=True)} Uhr, noch {_fmt_duration(t['due'] - now)}")
        return "\n".join(lines)

    def list_timers(self):
        return self.describe() or "Keine Timer oder Wecker aktiv."

    def cancel(self, timer_id="", label=""):
        with self._lock:
            if not self._timers:
                removed = []
            elif timer_id == "all":
                removed = list(self._timers)
            elif timer_id:
                removed = [t for t in self._timers if t["id"] == timer_id.lstrip("#")]
            elif label:
                removed = [t for t in self._timers if label.lower() in t["label"].lower()]
            elif len(self._timers) == 1:
                removed = list(self._timers)
            else:
                removed = None
            if removed:
                self._timers = [t for t in self._timers if t not in removed]
                self._save()
        if removed is None:
            return "Mehrere Timer aktiv, gib timer_id an (oder 'all'):\n" + self.describe()
        if not removed:
            return "Kein passender Timer gefunden. Aktiv:\n" + self.list_timers()
        print(f"[Timer] Cancelled {[t['id'] for t in removed]}", flush=True)
        return f"{len(removed)} gelöscht: " + ", ".join(f"#{t['id']} '{t['label']}'" for t in removed)

    @staticmethod
    def _clock(ts, with_date=False):
        dt = datetime.datetime.fromtimestamp(ts)
        if not with_date:
            return f"{dt:%H:%M}"
        day = f"am {dt:%d.%m.} " if dt.date() != datetime.date.today() else ""
        return f"{day}um {dt:%H:%M}"

    # --- ringing ---

    def start(self, get_audio_manager, hardware, on_ring=None):
        """Starts the background thread that fires due timers.

        get_audio_manager returns the current AudioManager (the test mode replaces it after each session).
        on_ring is called when a timer fires, e.g. to end a running Live session.
        """
        self._get_audio = get_audio_manager
        self._hardware = hardware
        self._on_ring = on_ring
        threading.Thread(target=self._run, daemon=True, name="timers").start()

    @property
    def is_ringing(self):
        return self._ringing_since is not None

    def stop_ringing(self, reason="manual"):
        """Stops a ringing alarm. Returns True if one was ringing."""
        with self._lock:
            if self._ringing_since is None:
                return False
            self._ringing_since = None
            labels, self._ringing_labels = self._ringing_labels, []
            self._sound = None  # ~1 MB, not worth keeping on the Pi between alarms
        audio = self._get_audio() if self._get_audio else None
        if audio:
            audio.stop_playback()
        if self._hardware:
            self._hardware.turn_off()
        print(f"[Timer] Alarm stopped ({reason}): {labels}", flush=True)
        return True

    def _fire(self, due):
        print(f"[Timer] Ringing: {[t['label'] for t in due]}", flush=True)
        if self._sound is None:
            try:
                with open(ALARM_SOUND, "rb") as f:
                    self._sound = f.read()
            except OSError as e:
                print(f"[Timer] Alarm sound missing ({e}), run scripts/convert_sounds.py", flush=True)
                self._sound = b""
        with self._lock:
            self._ringing_labels += [t["label"] for t in due]
            self._ringing_since = time.time()
            self._pos = 0
        if self._on_ring:
            try:
                self._on_ring()
            except Exception as e:
                print(f"[Timer] on_ring failed: {e}", flush=True)

    def _run(self):
        last_led = 0.0
        while True:
            try:
                time.sleep(0.1)
                now = time.time()
                with self._lock:
                    due = [t for t in self._timers if t["due"] <= now]
                    if due:
                        self._timers = [t for t in self._timers if t not in due]
                        self._save()
                if due:
                    self._fire(due)
                if self._ringing_since is None:
                    continue
                if now - self._ringing_since > ALARM_TIMEOUT:
                    self.stop_ringing(f"timeout {ALARM_TIMEOUT:.0f}s")
                    continue
                # A session ending clears the playback queue and the LED, so keep both topped up
                audio = self._get_audio()
                if self._sound and audio and audio.playback_queue.qsize() < 3:
                    piece = self._sound[self._pos:self._pos + PIECE_BYTES]
                    self._pos = (self._pos + PIECE_BYTES) % len(self._sound)
                    audio.play_chunk(piece, is_tts=False)
                if now - last_led > 1.0 and self._hardware and not self._hardware.is_pulsing():
                    self._hardware.start_pulse(*ORANGE)
                    last_led = now
            except Exception as e:
                print(f"[Timer] Error: {e}", flush=True)


timer_manager = TimerManager()
