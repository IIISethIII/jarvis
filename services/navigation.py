# jarvis/services/navigation.py
import requests
import urllib.parse
from jarvis.config import MY_LAT, MY_LNG

HEADERS = {'User-Agent': 'JarvisAssistant/1.0 (me@home.local)'}

# --- KONFIGURATION ---
# Hier stellen wir ein, wie schnell du "wirklich" bist (inkl. Ampeln).
# 20 km/h ist ein guter Mittelwert für München (Komoot "Sportlich" bis "Durchschnitt").
REALISTIC_SPEED_KMH = 20.6

def get_coordinates(query):
    # ... (Dieser Teil bleibt exakt gleich wie vorher) ...
    try:
        base_url = "https://nominatim.openstreetmap.org/search"
        params = {'q': query, 'format': 'json', 'limit': 1, 'addressdetails': 1}
        r = requests.get(base_url, params=params, headers=HEADERS, timeout=5)
        data = r.json()
        if data:
            return float(data[0]['lat']), float(data[0]['lon']), data[0].get('display_name', query)
    except Exception as e:
        print(f"[Nav Error] Geocoding failed: {e}")
    return None, None, None

def get_route_estimate(start_lat, start_lon, end_lat, end_lon):
    """
    Holt die DISTANZ via OSRM, berechnet die ZEIT aber selbst,
    damit sie realistisch (wie bei Komoot) ist.
    """
    # Wir nutzen 'cycling' für eine gute Rad-Route (Vermeidung von Autobahnen etc.)
    url = f"http://router.project-osrm.org/route/v1/cycling/{start_lon},{start_lat};{end_lon},{end_lat}?overview=false"
    
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data['routes']:
                # OSRM liefert Meter und theoretische Sekunden
                distance_meters = data['routes'][0]['distance']
                
                # --- UNSER REALISMUS-HACK ---
                # Anstatt OSRM-Zeit zu nehmen, teilen wir Distanz durch unsere Real-Geschwindigkeit.
                # Zeit = Weg / Geschwindigkeit
                speed_ms = REALISTIC_SPEED_KMH / 3.6
                realistic_seconds = distance_meters / speed_ms
                
                return realistic_seconds, distance_meters
    except Exception as e:
        print(f"[Nav Error] OSRM failed: {e}")
        
    return None, None

def generate_komoot_url(end_location, sport="touringbicycle", start_location=None):
    if start_location:
        s_lat, s_lon, s_name = get_coordinates(start_location)
    else:
        s_lat, s_lon, s_name = MY_LAT, MY_LNG, "Zuhause"

    e_lat, e_lon, e_name = get_coordinates(end_location)
    
    if not e_lat:
        return f"Ich konnte den Ort '{end_location}' nicht finden.", None, None

    # Mapping für Komoot URL
    sport_map = {
        "rennrad": "racebike", "fahrrad": "touringbicycle", "gravel": "touringbicycle",
        "mountainbike": "mtb", "mtb": "mtb", "wandern": "hike", "laufen": "jog"
    }
    komoot_sport = sport_map.get(sport.lower(), "touringbicycle")

    # Zeit berechnen
    duration_sec, dist_m = get_route_estimate(s_lat, s_lon, e_lat, e_lon)
    
    # Text formatieren
    duration_str = ""
    dist_str = ""
    if duration_sec:
        # Auf 5 Minuten runden sieht natürlicher aus ("ca 15 Min" statt "13 Min 42 Sek")
        mins_total = int(duration_sec / 60)
        # Runden auf nächste 1er oder 5er Stelle? Komoot macht minutengenau.
        # Wir lassen es genau, aber addieren vielleicht pauschal 1-2 Min Ampelpuffer für Start/Ziel.
        
        hours = int(mins_total / 60)
        rem_mins = mins_total % 60
        
        if hours > 0: duration_str = f"{hours} Std {rem_mins} Min"
        else: duration_str = f"{mins_total} Minuten"
        
        dist_km = round(dist_m / 1000, 1)
        dist_str = f"({dist_km} km)"

    # URL Bauen (bleibt gleich)
    url = f"https://www.komoot.de/plan?sport={komoot_sport}&p[0][name]={urllib.parse.quote(s_name)}&p[0][loc]={s_lat},{s_lon}&p[1][name]={urllib.parse.quote(e_name)}&p[1][loc]={e_lat},{e_lon}"

    result_text = f"Route nach {e_name.split(',')[0]} {dist_str}." # Nur erster Teil des Namens, sonst zu lang
    if duration_str:
        result_text += f" Geschätzte Zeit (Real): ca. {duration_str}."
    
    return result_text, url, duration_str