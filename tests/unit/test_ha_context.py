import os
import unittest
from unittest import mock

from core import ha_context


def state(eid, value, **attrs):
    return {"entity_id": eid, "state": value, "attributes": attrs}


STATES = [
    state("light.desk", "on", friendly_name="Desk", brightness=180, supported_color_modes=["xy"]),
    state("sensor.rain", "0.0", friendly_name="Rain", unit_of_measurement="mm",
          data=[{"datetime": f"2026-11-0{i}", "value": 0.0} for i in range(1, 9)]),
    state("sensor.temp", "21.5", friendly_name="Temp", unit_of_measurement="°C", device_class="temperature"),
    state("automation.x", "on", friendly_name="Hidden"),
    state("todo.shopping", "3", friendly_name="Shopping"),
]


class OverviewTest(unittest.TestCase):
    def test_lines(self):
        text = ha_context.devices_overview(STATES, areas={})
        self.assertIn("- Desk (light.desk): on [brightness=180]", text)
        self.assertNotIn("supported_color_modes", text)
        self.assertIn("- Rain (sensor.rain): 0.0 mm +", text)    # long data behind '+'
        self.assertIn("- Temp (sensor.temp): 21.5 °C\n", text)   # noise attrs dropped, no '+'
        self.assertIn("(todo.shopping): 3 +items", text)          # items via a service response
        self.assertNotIn("automation.x", text)

    def test_areas(self):
        text = ha_context.devices_overview(STATES, areas={"light.desk": "Office"})
        self.assertTrue(text.startswith("Office:\n- Desk"))
        self.assertIn("No room:\n", text)

    def test_budget_keeps_controls(self):
        many = STATES + [state(f"sensor.s{i}", "1", friendly_name=f"Sensor number {i}", foo="bar") for i in range(300)]
        with mock.patch.dict(os.environ, {"JARVIS_HA_CONTEXT_CHARS": "2000"}):
            text = ha_context.devices_overview(many, areas={})
        self.assertIn("light.desk", text)
        self.assertNotIn("sensor.s1)", text)
        self.assertIn("get_ha_details(query=", text)

    def test_services_skip_admin(self):
        services = [{"domain": "light", "services": {"turn_on": {}, "reload": {}}},
                    {"domain": "hassio", "services": {"host_shutdown": {}}}]
        self.assertEqual(ha_context.services_overview(services), "- light: turn_on")


if __name__ == "__main__":
    unittest.main()
