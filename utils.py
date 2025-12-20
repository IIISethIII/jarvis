# jarvis/utils.py
import requests

# Global Session for Connection Pooling
session = requests.Session()
adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10)
session.mount('https://', adapter)
session.mount('http://', adapter)