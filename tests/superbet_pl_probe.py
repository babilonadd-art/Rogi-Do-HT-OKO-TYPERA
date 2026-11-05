import json
import re
import requests
from urllib.parse import urljoin

BASE = "https://production-superbet-offer-pl.freetls.fastly.net"
SITE = "https://superbet.pl/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
    "Accept": "text/html,application/json,*/*",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    "Origin": "https://superbet.pl",
    "Referer": SITE,
}
s = requests.Session()
s.headers.update(HEADERS)

def short(value, limit=2500):
    return re.sub(r"\s+", " ", str(value))[:limit]

print("FETCH SITE")
page = s.get(SITE, timeout=30)
print("SITE", page.status_code, len(page.content))
page.raise_for_status()
scripts = re.findall(r"<script[^>]+src=['\\\"]([^'\\\"]+)", page.text, re.I)
print("SCRIPT_COUNT", len(scripts))
for src in scripts:
    url = urljoin(SITE, src)
    try:
        js = s.get(url, timeout=30)
        print("\nBUNDLE", js.status_code, len(js.content), url)
        if url.endswith("/fetchConfig/app"):
            print("APP_CONFIG_BODY", short(js.text, 12000))
        if not js.ok or len(js.content) > 20_000_000:
            continue
        for hit in sorted(set(re.findall(r"""["']([^"']{0,120}(?:production-superbet-offer-pl|subscription|events|eventDetails|event-detail|offerState)[^"']{0,180})["']""", js.text, re.I))):
            print("BUNDLE_MATCH", short(hit, 350))
    except Exception as exc:
        print("BUNDLE_ERROR", type(exc).__name__, str(exc)[:180])

print("\nSTRUCTURE_AND_MARKET_LABELS")
resp = s.get(BASE + "/v2/pl-PL/struct", timeout=30)
print("STRUCT", resp.status_code, len(resp.content))
resp.raise_for_status()
data = resp.json().get("data", {})
print("ROOT_KEYS", list(data))
for key in ("sports", "outcomes", "tournaments", "categories"):
    value = data.get(key)
    if isinstance(value, dict):
        print(key.upper(), "keys", list(value)[:12], "length", value.get("length"))
        items = value.get("data") or value.get("items") or value.get("list")
        if isinstance(items, list):
            print(key.upper(), "sample", short(items[:2]))
    elif isinstance(value, list):
        print(key.upper(), "sample", short(value[:2]))

for key, value in data.items():
    blob = json.dumps(value, ensure_ascii=False)
    if re.search(r"corner|rożn|corners|kick", blob, re.I):
        print("CORNER_MATCH_IN", key)
        if isinstance(value, dict):
            for subkey, subval in value.items():
                if re.search(r"corner|rożn|corners|kick", json.dumps(subval, ensure_ascii=False), re.I):
                    print("CORNER_SUBTREE", key, subkey, short(subval, 5000))
        else:
            print("CORNER_DATA", short(value, 5000))

print("\nDISCOVERY_FINISHED")
