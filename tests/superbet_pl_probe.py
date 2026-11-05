import json
import requests

BASE = "https://production-superbet-offer-pl.freetls.fastly.net"
URLS = [
    f"{BASE}/v2/pl-PL/struct",
    f"{BASE}/subscription/v2/pl-PL/structure",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    "Origin": "https://superbet.pl",
    "Referer": "https://superbet.pl/",
}

def summarize(value, depth=0):
    if depth > 3:
        return type(value).__name__
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:30]:
            if isinstance(item, (dict, list)):
                result[str(key)] = summarize(item, depth + 1)
            else:
                result[str(key)] = item
        return result
    if isinstance(value, list):
        return {"length": len(value), "sample": [summarize(x, depth + 1) for x in value[:2]]}
    return value

for url in URLS:
    print(f"\\nREQUEST {url}")
    try:
        response = requests.get(url, headers=HEADERS, timeout=25)
        print(f"HTTP {response.status_code}; content-type={response.headers.get('content-type')}; bytes={len(response.content)}")
        response.raise_for_status()
        try:
            payload = response.json()
            print(json.dumps(summarize(payload), ensure_ascii=False)[:12000])
        except ValueError:
            print(response.text[:5000])
    except Exception as exc:
        print(f"PROBE_ERROR {type(exc).__name__}: {exc}")
print("\\nPROBE_FINISHED")
