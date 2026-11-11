"""Weather for any place, without an account or API key.

Forecast, air quality and pollen come from Open-Meteo (worldwide; in Germany it uses the DWD ICON models).
In Germany the DWD rain radar (5-minute steps, two hours ahead) and the official DWD warnings are added
through Bright Sky. Places, street addresses too, are geocoded with Nominatim (OpenStreetMap), which allows
one request per second and asks clients to cache, so results are kept in data/geocode.json.

get_weather returns a short text instead of the raw data (the hourly forecast alone is ~100k chars): now,
rain in the next two hours, rain and dry phases over 24 hours (computed here: the Live model reads gaps in
long tables badly), the hours, the days, warnings, air quality and pollen.
"""
import concurrent.futures
import datetime
import json
import os
import re
import time

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
RADAR_URL = "https://api.brightsky.dev/radar"
ALERTS_URL = "https://api.brightsky.dev/alerts"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "jarvis-voice-assistant (+https://github.com/IIISethIII/jarvis)"  # Nominatim requires one
TIMEOUT = 8
CACHE_SECONDS = 600
MAX_GEOCODE_TRIES = 4

WET_MM = 0.1          # mm in an hour that count as rain
LIKELY_PCT = 60       # rain chance from which an hour counts as rainy even if the model run has no mm
POSSIBLE_PCT = 30
NOWCAST_WET_MM_H = 0.2  # radar / 15-min steps: one radar unit (0.12 mm/h) alone is noise

WMO = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "heavy showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}
SNOW_CODES = {71, 73, 75, 77, 85, 86}
POLLEN = {"alder_pollen": "alder", "birch_pollen": "birch", "grass_pollen": "grass",
          "mugwort_pollen": "mugwort", "olive_pollen": "olive", "ragweed_pollen": "ragweed"}

_cache = {}  # "lat,lon" -> (time, text)
_last_nominatim = 0.0


# --- places ---

def _geocode_file():
    return os.path.join(os.getenv("JARVIS_DATA_DIR") or os.path.join(BASE_DIR, "data"), "geocode.json")


def _load_places():
    try:
        with open(_geocode_file(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_places(places):
    path = _geocode_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(places, f, ensure_ascii=False, indent=1)


def _nominatim(query):
    """First Nominatim hit as a place dict, or None. Keeps Nominatim's limit of one request per second."""
    global _last_nominatim
    wait = 1.0 - (time.time() - _last_nominatim)
    if wait > 0:
        time.sleep(wait)
    _last_nominatim = time.time()
    r = requests.get(NOMINATIM_URL, params={"q": query, "format": "jsonv2", "limit": 1, "addressdetails": 1},
                     headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    r.raise_for_status()
    hits = r.json()
    if not hits:
        return None
    hit = hits[0]
    a = hit.get("address", {})
    street = " ".join(p for p in (a.get("road"), a.get("house_number")) if p)
    town = a.get("city") or a.get("town") or a.get("village") or a.get("municipality") or a.get("county")
    # an address keeps its street; otherwise the hit's own name (a peak, a town, a station)
    first = street if street and re.search(r"\d", query) else hit.get("name") or street
    parts = [p for p in (first, town) if p]
    name = ", ".join(dict.fromkeys(parts)) or hit.get("display_name", query)
    return {"lat": float(hit["lat"]), "lon": float(hit["lon"]), "name": name,
            "country": (a.get("country_code") or "").lower()}


def resolve(location):
    """Place dict (lat, lon, name, country) for a name, an address or 'lat,lon'. Raises LookupError."""
    location = (location or "").strip()
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", location)
    if m:
        return {"lat": float(m[1]), "lon": float(m[2]), "name": location, "country": ""}
    if not location:
        raise LookupError("no location given")
    key = " ".join(location.lower().split())
    places = _load_places()
    if key in places:
        return places[key]
    # "Wohnheim X, Musterstraße 5/7, Berlin (Mitte)": drop the brackets, then leading parts until a hit
    parts = [p.strip() for p in re.sub(r"\([^)]*\)", "", location).split(",") if p.strip()]
    for i in range(min(len(parts), MAX_GEOCODE_TRIES)):
        place = _nominatim(", ".join(parts[i:]))
        if place:
            places[key] = place
            _save_places(places)
            return place
    raise LookupError(f"place '{location}' not found")


def _in_germany(place):
    """DWD radar and warnings cover Germany; without a country code (raw coordinates) a bounding box decides."""
    if place.get("country"):
        return place["country"] == "de"
    return 47.2 <= place["lat"] <= 55.1 and 5.8 <= place["lon"] <= 15.1


# --- fetching ---

def _fetch_forecast(lat, lon):
    r = requests.get(FORECAST_URL, timeout=TIMEOUT, params={
        "latitude": lat, "longitude": lon, "timezone": "auto", "past_days": 1, "forecast_days": 7,
        "current": "temperature_2m,apparent_temperature,weather_code,precipitation,wind_speed_10m,wind_gusts_10m",
        "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,"
                 "sunshine_duration,wind_gusts_10m_max,sunrise,sunset",
        "minutely_15": "precipitation", "past_minutely_15": 1, "forecast_minutely_15": 12,
    })
    r.raise_for_status()
    return r.json()


def _fetch_air(lat, lon):
    r = requests.get(AIR_URL, timeout=TIMEOUT, params={
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1,
        "current": "european_aqi,us_aqi", "hourly": ",".join(POLLEN),
    })
    r.raise_for_status()
    return r.json()


def _fetch_radar(lat, lon):
    # starting now, Bright Sky returns the radar forecast for the next two hours
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")
    r = requests.get(RADAR_URL, timeout=TIMEOUT, params={
        "lat": lat, "lon": lon, "distance": 1000, "format": "plain", "date": now,
    })
    r.raise_for_status()
    return r.json()


def _fetch_alerts(lat, lon):
    r = requests.get(ALERTS_URL, timeout=TIMEOUT, params={"lat": lat, "lon": lon})
    r.raise_for_status()
    return r.json()


def _optional(fetch, *args):
    """Radar, warnings and air quality are extras: a failure leaves them out instead of failing the tool."""
    try:
        return fetch(*args)
    except Exception as e:
        print(f"[Weather] {getattr(fetch, '__name__', 'fetch')} failed: {e}", flush=True)
        return None


# --- summary ---

def _condition(code):
    return WMO.get(code, f"weather code {code}") if code is not None else "unknown"


def _intensity(mm_h):
    return "light" if mm_h < 2.5 else "moderate" if mm_h < 10 else "heavy"


def _at(values, key, i):
    seq = (values or {}).get(key) or []
    return seq[i] if i < len(seq) else None


def _clock(dt, today, end=False):
    """'14:00', 'tomorrow 03:00' or 'Fri 14:00'; an end at midnight after today is '24:00'."""
    if dt.date() == today:
        return f"{dt:%H:%M}"
    if dt.date() == today + datetime.timedelta(days=1):
        return "24:00" if end and dt.time() == datetime.time(0) else f"tomorrow {dt:%H:%M}"
    return f"{dt:%a %H:%M}"


def _group(items, key):
    """Splits items into runs with the same key: [(key, [items])]."""
    runs = []
    for item in items:
        k = key(item)
        if runs and runs[-1][0] == k:
            runs[-1][1].append(item)
        else:
            runs.append((k, [item]))
    return runs


def _local(timestamp, offset):
    """ISO timestamp with offset -> naive local time of the place."""
    dt = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return dt.astimezone(datetime.timezone.utc).replace(tzinfo=None) + offset


def _hours(fc, now):
    h = fc.get("hourly") or {}
    start = now.replace(minute=0, second=0, microsecond=0)
    out = []
    for i, t in enumerate(h.get("time", [])):
        t = datetime.datetime.fromisoformat(t)
        if t >= start:
            out.append({"time": t, "code": _at(h, "weather_code", i), "temp": _at(h, "temperature_2m", i),
                        "prob": _at(h, "precipitation_probability", i), "mm": _at(h, "precipitation", i) or 0.0})
    return out


def _hour_class(h):
    prob = h["prob"] or 0
    if h["mm"] >= WET_MM or prob >= LIKELY_PCT:
        return "rain"
    return "possible" if prob >= POSSIBLE_PCT else "dry"


def _phase_text(cls, hours, today):
    span = f"{_clock(hours[0]['time'], today)}–{_clock(hours[-1]['time'] + datetime.timedelta(hours=1), today, end=True)}"
    probs = [h["prob"] for h in hours if h["prob"] is not None]
    if cls == "dry":
        return f"{span} dry" + (f" (chance ≤ {max(probs)} %)" if probs else "")
    pct = (f"chance {min(probs)}–{max(probs)} %" if min(probs) != max(probs) else f"chance {probs[0]} %") if probs else "chance unknown"
    kinds = {"snow" if h["code"] in SNOW_CODES else "rain" for h in hours}
    kind = "rain or snow" if len(kinds) > 1 else kinds.pop()
    if cls == "possible":
        return f"{span} {kind} possible ({pct})"
    peak = max(h["mm"] for h in hours)
    return f"{span} {kind} ({pct}, up to {peak:.1f} mm/h, {_intensity(peak)})"


def _nowcast_text(frames, step_min, today):
    """frames: [(local time, mm/h)] in steps of step_min minutes."""
    step = datetime.timedelta(minutes=step_min)
    parts = []
    for wet, run in _group(frames, lambda f: f[1] >= NOWCAST_WET_MM_H):
        span = f"{_clock(run[0][0], today)}–{_clock(run[-1][0] + step, today, end=True)}"
        if wet:
            peak = max(mm for _, mm in run)
            parts.append(f"rain {span} (up to {peak:.1f} mm/h, {_intensity(peak)})")
        else:
            parts.append(f"dry {span}")
    return ", ".join(parts)


def _radar_frames(radar, offset):
    pos = radar.get("latlon_position") or {}
    frames = []
    for r in radar.get("radar") or []:
        grid = r.get("precipitation_5") or []
        if not grid:
            continue
        y = min(max(round(pos.get("y", 0)), 0), len(grid) - 1)
        x = min(max(round(pos.get("x", 0)), 0), len(grid[y]) - 1)
        frames.append((_local(r["timestamp"], offset), grid[y][x] * 0.12))  # 0.01 mm per 5 min -> mm/h
    return frames


def _minutely_frames(fc, now):
    m = fc.get("minutely_15") or {}
    start = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
    frames = [(datetime.datetime.fromisoformat(t), (_at(m, "precipitation", i) or 0.0) * 4)
              for i, t in enumerate(m.get("time", []))]
    return [f for f in frames if f[0] >= start][:8]


def _day_lines(fc, today):
    d = fc.get("daily") or {}
    lines = []
    for i, day in enumerate(d.get("time", [])):
        date = datetime.date.fromisoformat(day)
        if date < today:
            label = "Yesterday"
        elif date == today:
            label = "Today"
        elif date == today + datetime.timedelta(days=1):
            label = "Tomorrow"
        else:
            label = f"{date:%a %d.%m.}"
        tmin, tmax = _at(d, "temperature_2m_min", i), _at(d, "temperature_2m_max", i)
        parts = [_condition(_at(d, "weather_code", i))]
        if tmin is not None and tmax is not None:
            parts.append(f"{round(tmin)} to {round(tmax)} °C")
        rain, prob = _at(d, "precipitation_sum", i), _at(d, "precipitation_probability_max", i)
        if rain is not None:
            parts.append(f"precipitation {rain:.1f} mm" + (f" (chance {prob} %)" if prob is not None else ""))
        sun = _at(d, "sunshine_duration", i)
        if sun is not None:
            parts.append(f"sun {sun / 3600:.0f} h")
        gust = _at(d, "wind_gusts_10m_max", i)
        if gust is not None and gust >= 40:
            parts.append(f"gusts up to {gust:.0f} km/h")
        if date == today and _at(d, "sunrise", i) and _at(d, "sunset", i):
            parts.append(f"sunrise {_at(d, 'sunrise', i)[11:16]}, sunset {_at(d, 'sunset', i)[11:16]}")
        lines.append(f"- {label}: " + ", ".join(parts))
    return lines


def _alerts_text(alerts, offset, today):
    items = []
    for a in (alerts.get("alerts") or [])[:3]:
        start = _clock(_local(a["onset"], offset), today) if a.get("onset") else "now"
        end = _clock(_local(a["expires"], offset), today, end=True) if a.get("expires") else "further notice"
        event = a.get("event_en") or a.get("event_de") or "warning"
        headline = a.get("headline_en") or a.get("headline_de") or ""
        items.append(f"{event} ({a.get('severity', '?')}) {start} until {end}" + (f": {headline}" if headline else ""))
    return "; ".join(items) or "none"


def _aqi_word(eaqi):
    for limit, word in ((20, "good"), (40, "fair"), (60, "moderate"), (80, "poor"), (100, "very poor")):
        if eaqi <= limit:
            return word
    return "extremely poor"


def _air_text(air, today):
    parts = []
    cur = air.get("current") or {}
    if cur.get("european_aqi") is not None:
        parts.append(f"air quality {_aqi_word(cur['european_aqi'])} (European AQI {cur['european_aqi']:.0f})")
    elif cur.get("us_aqi") is not None:
        parts.append(f"air quality US AQI {cur['us_aqi']:.0f}")
    h = air.get("hourly") or {}
    times = [datetime.datetime.fromisoformat(t).date() == today for t in h.get("time", [])]
    levels = {}
    for key, name in POLLEN.items():
        values = [v for v, is_today in zip(h.get(key) or [], times) if is_today and v is not None]
        if values:
            levels[name] = max(values)
    if levels:
        # rough grains/m³ bands; the thresholds differ per plant
        shown = [f"{name} {'low' if v <= 10 else 'moderate' if v <= 50 else 'high'} ({v:.0f}/m³)"
                 for name, v in sorted(levels.items(), key=lambda kv: -kv[1]) if v >= 1]
        parts.append("pollen today: " + (", ".join(shown) or "none"))
    return "; ".join(parts)


def summarize(place, fc, radar=None, alerts=None, air=None):
    """The tool text from the raw API answers (radar, alerts, air optional)."""
    offset = datetime.timedelta(seconds=fc.get("utc_offset_seconds", 0))
    cur = fc.get("current") or {}
    now = datetime.datetime.fromisoformat(cur["time"])
    today = now.date()
    lines = [f"Weather for {place['name']}, local time {now:%a %d.%m. %H:%M}."]

    feels = cur.get("apparent_temperature")
    lines.append(f"Now: {_condition(cur.get('weather_code'))}, {cur.get('temperature_2m', '?')} °C"
                 + (f" (feels like {feels} °C)" if feels is not None else "")
                 + f", wind {cur.get('wind_speed_10m', '?')} km/h, gusts {cur.get('wind_gusts_10m', '?')} km/h.")

    frames = _radar_frames(radar, offset) if radar else []
    if frames:
        lines.append("Rain radar, next 2 h (DWD, 5-min steps): " + _nowcast_text(frames, 5, today) + ".")
    else:
        frames = _minutely_frames(fc, now)
        if frames:
            lines.append("Next 2 h (forecast model, 15-min steps): " + _nowcast_text(frames, 15, today) + ".")

    hours = _hours(fc, now)[:24]
    if hours:
        lines.append("Rain phases, next 24 h: "
                     + "; ".join(_phase_text(cls, run, today) for cls, run in _group(hours, _hour_class)) + ".")
        cells = []
        for h in hours:
            label = f"{h['time']:%H}" if h["time"].date() == today else f"tomorrow {h['time']:%H}"
            prob = f"{h['prob']}%" if h["prob"] is not None else "-"
            temp = f"{round(h['temp'])}°" if h["temp"] is not None else "?"
            cells.append(f"{label}h {_condition(h['code'])} {temp} {prob} {h['mm']:.1f}mm")
        lines.append("Hourly (hour, condition, temperature, rain chance, rain): " + " | ".join(cells))

    days = _day_lines(fc, today)
    if days:
        lines.append("Days:\n" + "\n".join(days))
    if alerts is not None:
        lines.append("Official warnings (DWD): " + _alerts_text(alerts, offset, today) + ".")
    if air:
        text = _air_text(air, today)
        if text:
            lines.append(text[0].upper() + text[1:] + ".")
    return "\n".join(lines)


def get_weather(location):
    try:
        place = resolve(location)
    except LookupError as e:
        return f"Error: {e}. Ask the user for the town or a clearer address."
    except Exception as e:
        return f"Error: place lookup failed ({e})."
    key = f"{place['lat']:.3f},{place['lon']:.3f}"
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]
    lat, lon = place["lat"], place["lon"]
    germany = _in_germany(place)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        forecast = pool.submit(_fetch_forecast, lat, lon)
        air = pool.submit(_optional, _fetch_air, lat, lon)
        radar = pool.submit(_optional, _fetch_radar, lat, lon) if germany else None
        alerts = pool.submit(_optional, _fetch_alerts, lat, lon) if germany else None
        try:
            fc = forecast.result()
        except Exception as e:
            print(f"[Weather] forecast failed: {e}", flush=True)
            return f"Error: weather service unreachable ({e})."
        text = summarize(place, fc, radar=radar.result() if radar else None,
                         alerts=alerts.result() if alerts else None, air=air.result())
    _cache[key] = (time.time(), text)
    return text


if __name__ == "__main__":
    import sys
    print(get_weather(" ".join(sys.argv[1:]) or "Berlin"))
