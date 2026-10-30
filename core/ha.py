import os
import requests
from dotenv import load_dotenv

load_dotenv()

class HomeAssistant:
    def __init__(self):
        self.ha_url = os.getenv("HA_URL")
        self.ha_token = os.getenv("HA_TOKEN")
        
        if not self.ha_url or not self.ha_token:
            print("[Warning] HA_URL or HA_TOKEN not set in .env. HA integration will fail.")
            
        self.headers = {
            "Authorization": f"Bearer {self.ha_token}",
            "Content-Type": "application/json"
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def get_state(self, entity_id):
        """Fetches the raw state object of a specific entity."""
        try:
            r = self.session.get(f"{self.ha_url}/api/states/{entity_id}", timeout=5)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            print(f"[HA Error] get_state failed for {entity_id}: {e}")
            return None

    def set_state(self, entity_id, state_value, attributes=None):
        """Manually sets the state of an entity (Useful for sensor.jarvis_last_response)."""
        try:
            payload = {"state": str(state_value)}
            if attributes:
                payload["attributes"] = attributes
            r = self.session.post(f"{self.ha_url}/api/states/{entity_id}", json=payload, timeout=5)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            print(f"[HA Error] set_state failed for {entity_id}: {e}")
            return None

    def call_service(self, domain, service, service_data=None):
        """Calls a Home Assistant service (e.g. light/turn_on)."""
        try:
            payload = service_data or {}
            r = self.session.post(f"{self.ha_url}/api/services/{domain}/{service}", json=payload, timeout=5)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            print(f"[HA Error] call_service failed for {domain}.{service}: {e}")
            return None
            
    def fetch_all_states(self):
        """Fetches all states to build the context for the LLM."""
        try:
            r = self.session.get(f"{self.ha_url}/api/states", timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            print(f"[HA Error] fetch_all_states failed: {e}")
            return []

    def fetch_all_services(self):
        """Fetches all available services to build context for the LLM."""
        try:
            r = self.session.get(f"{self.ha_url}/api/services", timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            print(f"[HA Error] fetch_all_services failed: {e}")
            return []

    # --- High Level Helper Methods ---
    


    def read_mailbox(self, entity_id="input_text.jarvis_chat"):
        """Reads the remote trigger mailbox."""
        state = self.get_state(entity_id)
        if state and state.get('state') not in ['unknown', 'unavailable']:
            return state.get('state')
        return ""
        
    def clear_mailbox(self, entity_id="input_text.jarvis_chat"):
        """Empties the remote trigger mailbox."""
        return self.call_service("input_text", "set_value", {
            "entity_id": entity_id,
            "value": ""
        })
