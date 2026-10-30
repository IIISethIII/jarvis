import sys
import os
sys.path.append('/home/pi/jarvis_v3')
from core.tools import ha_client
import json

states = ha_client.fetch_all_states()
if states:
    print(f'Total entities: {len(states)}')
    for s in states:
        if s['entity_id'].startswith('weather.'):
            print(json.dumps(s, indent=2))
