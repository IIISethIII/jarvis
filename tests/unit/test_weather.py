import os
import tempfile
import unittest
from unittest import mock

from core import weather

PLACE = {"lat": 52.52, "lon": 13.405, "name": "Berlin", "country": "de"}


def forecast(codes=None):
    """Monday 2026-03-02 10:00 local (UTC+1): dry, rain possible at 12, rain 13-16, then dry."""
    times = [f"2026-03-0{2 + h // 24}T{h % 24:02d}:00" for h in range(48)]
    prob = [5] * 48
    mm = [0.0] * 48
    for h in (10, 11):
        prob[h] = 10
    prob[12] = 40
    for h, amount in zip((13, 14, 15), (1.2, 3.0, 0.5)):
        prob[h], mm[h] = 80, amount
    return {
        "utc_offset_seconds": 3600,
        "current": {"time": "2026-03-02T10:00", "temperature_2m": 8.2, "apparent_temperature": 5.1,
                    "weather_code": 3, "wind_speed_10m": 12.0, "wind_gusts_10m": 30.0},
        "hourly": {"time": times, "temperature_2m": [8.0] * 48, "precipitation_probability": prob,
                   "precipitation": mm, "weather_code": codes or [61 if m else 3 for m in mm]},
        "minutely_15": {"time": [f"2026-03-02T{9 + q // 4:02d}:{q % 4 * 15:02d}" for q in range(16)],
                        "precipitation": [0.0] * 6 + [0.5, 0.5] + [0.0] * 8},
        "daily": {"time": ["2026-03-01", "2026-03-02", "2026-03-03"], "weather_code": [61, 61, 0],
                  "temperature_2m_min": [-0.4, 2.0, 1.0], "temperature_2m_max": [6.0, 9.0, 12.0],
                  "precipitation_sum": [4.0, 4.7, 0.0], "precipitation_probability_max": [None, 80, 5],
                  "sunshine_duration": [0, 3600, 28800], "wind_gusts_10m_max": [55.0, 30.0, 20.0],
                  "sunrise": ["", "2026-03-02T06:50", ""], "sunset": ["", "2026-03-02T17:55", ""]},
    }


def radar(units):
    """Frames from 09:05 UTC (10:05 local) in 5-min steps; the place sits in the middle cell."""
    frames = [{"timestamp": f"2026-03-02T09:{5 + 5 * i:02d}:00+00:00",
               "precipitation_5": [[0, 0, 0], [0, u, 0], [0, 0, 0]]} for i, u in enumerate(units)]
    return {"radar": frames, "latlon_position": {"x": 1.2, "y": 0.9}}


class SummaryTest(unittest.TestCase):
    def test_rain_phases(self):
        text = weather.summarize(PLACE, forecast())
        self.assertIn("Rain phases, next 24 h: 10:00–12:00 dry (chance ≤ 10 %); 12:00–13:00 rain possible "
                      "(chance 40 %); 13:00–16:00 rain (chance 80 %, up to 3.0 mm/h, moderate); "
                      "16:00–tomorrow 10:00 dry (chance ≤ 5 %).", text)

    def test_snow(self):
        codes = [71 if 13 <= h <= 15 else 3 for h in range(48)]
        self.assertIn("13:00–16:00 snow (", weather.summarize(PLACE, forecast(codes)))

    def test_radar_replaces_model_nowcast(self):
        text = weather.summarize(PLACE, forecast(), radar=radar([0, 0, 5, 10, 1, 0]))
        # one radar unit (0.12 mm/h) alone is noise
        self.assertIn("Rain radar, next 2 h (DWD, 5-min steps): dry 10:05–10:15, "
                      "rain 10:15–10:25 (up to 1.2 mm/h, light), dry 10:25–10:35.", text)
        self.assertNotIn("forecast model", text)

    def test_model_nowcast_without_radar(self):
        text = weather.summarize(PLACE, forecast(), radar={"radar": []})
        self.assertIn("Next 2 h (forecast model, 15-min steps): dry 10:00–10:30, "
                      "rain 10:30–11:00 (up to 2.0 mm/h, light), dry 11:00–12:00.", text)

    def test_days(self):
        text = weather.summarize(PLACE, forecast())
        self.assertIn("- Yesterday: light rain, 0 to 6 °C, precipitation 4.0 mm, sun 0 h, gusts up to 55 km/h", text)
        self.assertIn("- Today: light rain, 2 to 9 °C, precipitation 4.7 mm (chance 80 %), sun 1 h, "
                      "sunrise 06:50, sunset 17:55", text)
        self.assertIn("- Tomorrow: clear", text)

    def test_now_and_hours(self):
        text = weather.summarize(PLACE, forecast())
        self.assertIn("Weather for Berlin, local time Mon 02.03. 10:00.", text)
        self.assertIn("Now: overcast, 8.2 °C (feels like 5.1 °C), wind 12.0 km/h, gusts 30.0 km/h.", text)
        self.assertIn("14h light rain 8° 80% 3.0mm", text)
        self.assertIn("tomorrow 09h overcast 8° 5% 0.0mm", text)

    def test_alerts_and_air(self):
        alerts = {"alerts": [{"onset": "2026-03-02T09:00:00+00:00", "expires": "2026-03-02T18:00:00+00:00",
                              "event_en": "frost", "severity": "minor", "headline_en": "Official WARNING of FROST"}]}
        air = {"current": {"european_aqi": 25},
               "hourly": {"time": ["2026-03-02T09:00", "2026-03-02T10:00", "2026-03-03T10:00"],
                          "birch_pollen": [0.0, 35.0, 200.0], "grass_pollen": [0.0, 0.2, 0.0]}}
        text = weather.summarize(PLACE, forecast(), alerts=alerts, air=air)
        self.assertIn("Official warnings (DWD): frost (minor) 10:00 until 19:00: Official WARNING of FROST.", text)
        self.assertIn("Air quality fair (European AQI 25); pollen today: birch moderate (35/m³).", text)
        self.assertIn("Official warnings (DWD): none.", weather.summarize(PLACE, forecast(), alerts={"alerts": []}))


class ClockTest(unittest.TestCase):
    def test_midnight(self):
        import datetime
        today = datetime.date(2026, 3, 2)
        midnight = datetime.datetime(2026, 3, 3, 0, 0)
        self.assertEqual(weather._clock(midnight, today, end=True), "24:00")
        self.assertEqual(weather._clock(midnight, today), "tomorrow 00:00")
        self.assertEqual(weather._clock(datetime.datetime(2026, 3, 4, 9, 30), today), "Wed 09:30")


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {"JARVIS_DATA_DIR": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_coordinates(self):
        place = weather.resolve("48.1, 11.5")
        self.assertEqual((place["lat"], place["lon"]), (48.1, 11.5))

    def test_drops_leading_parts_and_caches(self):
        hit = {"lat": 52.5, "lon": 13.4, "name": "Musterstraße 5, Berlin", "country": "de"}
        with mock.patch.object(weather, "_nominatim", side_effect=[None, hit]) as nominatim:
            self.assertEqual(weather.resolve("Wohnheim X, Musterstraße 5/7, Berlin (Mitte)"), hit)
        self.assertEqual([c.args[0] for c in nominatim.call_args_list],
                         ["Wohnheim X, Musterstraße 5/7, Berlin", "Musterstraße 5/7, Berlin"])
        with mock.patch.object(weather, "_nominatim") as nominatim:
            self.assertEqual(weather.resolve("  wohnheim x,  Musterstraße 5/7, Berlin (Mitte)"), hit)
        nominatim.assert_not_called()

    def test_name_of_hit(self):
        hit = [{"lat": "48.1", "lon": "11.5", "name": "Some Restaurant", "address": {
            "road": "Musterplatz", "house_number": "1", "city": "Musterstadt", "country_code": "DE"}}]
        response = mock.Mock(json=mock.Mock(return_value=hit))
        with mock.patch.object(weather.requests, "get", return_value=response), mock.patch.object(weather.time, "sleep"):
            self.assertEqual(weather._nominatim("Musterplatz 1, Musterstadt")["name"], "Musterplatz 1, Musterstadt")
            self.assertEqual(weather._nominatim("Some Restaurant")["name"], "Some Restaurant, Musterstadt")
            self.assertEqual(weather._nominatim("Musterplatz 1")["country"], "de")

    def test_not_found(self):
        with mock.patch.object(weather, "_nominatim", return_value=None):
            with self.assertRaises(LookupError):
                weather.resolve("Nowhere")
            self.assertTrue(weather.get_weather("Nowhere").startswith("Error: place 'Nowhere' not found."))

    def test_germany(self):
        self.assertTrue(weather._in_germany({"lat": 0, "lon": 0, "country": "de"}))
        self.assertFalse(weather._in_germany({"lat": 48.1, "lon": 11.5, "country": "at"}))
        self.assertTrue(weather._in_germany({"lat": 48.1, "lon": 11.5, "country": ""}))
        self.assertFalse(weather._in_germany({"lat": 48.86, "lon": 2.35, "country": ""}))


class GetWeatherTest(unittest.TestCase):
    def setUp(self):
        weather._cache.clear()
        self.addCleanup(weather._cache.clear)

    def test_cache_and_extras(self):
        with mock.patch.object(weather, "resolve", return_value=PLACE), \
                mock.patch.object(weather, "_fetch_forecast", return_value=forecast()) as fc, \
                mock.patch.object(weather, "_fetch_radar", side_effect=OSError("down")), \
                mock.patch.object(weather, "_fetch_alerts", return_value={"alerts": []}), \
                mock.patch.object(weather, "_fetch_air", return_value={}):
            first = weather.get_weather("Berlin")
            self.assertEqual(weather.get_weather("Berlin"), first)
        fc.assert_called_once()
        self.assertIn("forecast model", first)  # radar failed: model nowcast instead
        self.assertIn("Official warnings (DWD): none.", first)

    def test_no_radar_outside_germany(self):
        paris = {"lat": 48.86, "lon": 2.35, "name": "Paris", "country": "fr"}
        with mock.patch.object(weather, "resolve", return_value=paris), \
                mock.patch.object(weather, "_fetch_forecast", return_value=forecast()), \
                mock.patch.object(weather, "_fetch_radar") as radar_fetch, \
                mock.patch.object(weather, "_fetch_alerts") as alerts_fetch, \
                mock.patch.object(weather, "_fetch_air", return_value={}):
            text = weather.get_weather("Paris")
        radar_fetch.assert_not_called()
        alerts_fetch.assert_not_called()
        self.assertNotIn("warnings", text)

    def test_forecast_down(self):
        with mock.patch.object(weather, "resolve", return_value=PLACE), \
                mock.patch.object(weather, "_fetch_forecast", side_effect=OSError("timeout")), \
                mock.patch.object(weather, "_optional", return_value=None):
            self.assertEqual(weather.get_weather("Berlin"), "Error: weather service unreachable (timeout).")


if __name__ == "__main__":
    unittest.main()
