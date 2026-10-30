import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

class HomeAssistant:
    def __init__(self):
        self.ha_url = os.getenv("HA_URL")
        self.ha_token = os.getenv("HA_TOKEN")
        
        self.enabled = bool(self.ha_url and self.ha_token)
        if not self.enabled:
            print("[Warning] HA_URL or HA_TOKEN not set in .env. HA integration will fail.")

        # Players whose playback counts as background audio. Empty = every media_player, "none" = off.
        players_env = os.getenv("JARVIS_MEDIA_PLAYERS", "").strip()
        self.media_watch = self.enabled and players_env.lower() != "none"
        self.media_players = [p.strip() for p in players_env.split(',') if p.strip()] if self.media_watch else []

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

    def media_playing(self):
        """True if a watched media player is playing. False when HA or media watching is off, or on errors."""
        if not self.media_watch:
            return False
        if self.media_players:
            template = f"{{{{ {json.dumps(self.media_players)} | select('is_state', 'playing') | list | count }}}}"
        else:
            template = "{{ states.media_player | selectattr('state', 'eq', 'playing') | list | count }}"
        try:
            # One template call instead of fetching all states: the Pi polls this every few seconds
            r = self.session.post(f"{self.ha_url}/api/template", json={"template": template}, timeout=3)
            r.raise_for_status()
            return int(r.text.strip() or 0) > 0
        except (requests.exceptions.RequestException, ValueError) as e:
            print(f"[HA Error] media_playing failed: {e}")
            return False


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
