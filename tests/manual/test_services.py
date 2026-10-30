from core.tools import ha_client
res = ha_client.session.get(f'{ha_client.ha_url}/api/services', timeout=5).json()
print([d['domain'] for d in res])
