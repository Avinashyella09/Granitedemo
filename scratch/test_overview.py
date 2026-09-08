import requests
import time

start = time.time()
r = requests.get("http://localhost:8000/api/analytics/overview/")
print(f"Overview: {r.status_code} in {time.time() - start:.4f}s")
