import unittest
from unittest import mock

from core import tools


class CallHaServiceTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(tools, "ha_client")
        self.ha = patcher.start()
        self.addCleanup(patcher.stop)

    def test_success(self):
        self.ha.call_service.return_value = []
        self.assertEqual(tools.call_ha_service("light", "turn_on", "light.desk"), "Success")
        self.ha.call_service_response.assert_not_called()

    def test_service_with_response_retried(self):
        self.ha.call_service.return_value, self.ha.last_status = None, 400
        self.ha.call_service_response.return_value = {"weather.x": {"forecast": [{"condition": "sunny", "uid": "1"}]}}
        result = tools.call_ha_service("weather", "get_forecasts", "weather.x", '{"type": "daily"}')
        self.assertEqual(result, '{"weather.x":{"forecast":[{"condition":"sunny"}]}}')
        self.ha.call_service_response.assert_called_once_with(
            "weather", "get_forecasts", {"entity_id": "weather.x", "type": "daily"})

    def test_rejected(self):
        self.ha.call_service.return_value, self.ha.last_status = None, 400
        self.ha.call_service_response.return_value = None
        self.assertIn("HTTP 400", tools.call_ha_service("light", "turn_on", "light.nope"))

    def test_unreachable_not_retried(self):
        self.ha.call_service.return_value, self.ha.last_status = None, None
        self.assertEqual(tools.call_ha_service("light", "turn_on", "light.desk"), "Failed to contact Home Assistant")
        self.ha.call_service_response.assert_not_called()


if __name__ == "__main__":
    unittest.main()
