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

def short(value, limit=1800):
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

print("\nCANDIDATE_EVENT_ENDPOINTS")
paths = [
    "/v3/subscription/pl-PL/events/live/count",
    "/v3/subscription/pl-PL/events/prematch/count",
    "/v3/subscription/pl-PL/events/pre-match/count",
    "/v3/subscription/pl-PL/events/count",
    "/v3/subscription/pl-PL/events/prematch",
    "/v3/subscription/pl-PL/events/pre-match",
    "/v3/pl-PL/events/prematch",
    "/v3/pl-PL/events",
    "/v3/pl-PL/events/prematch? sportId=5",
    "/v2/pl-PL/events",
    "/v2/pl-PL/events/prematch",
]
for path in paths:
    try:
        x = s.get(BASE + path.replace("? ", "?"), timeout=20, stream=True)
        chunk = next(x.iter_content(1800), b"")
        print("ENDPOINT", path, x.status_code, x.headers.get("content-type"), "sample=", short(chunk.decode("utf-8","replace"), 500))
        x.close()
    except Exception as exc:
        print("ENDPOINT_ERROR", path, type(exc).__name__, str(exc)[:160])

print("\nSTRUCTURE_AND_CORNER_MARKETS")
resp = s.get(BASE + "/v2/pl-PL/struct", timeout=30)
print("STRUCT", resp.status_code, len(resp.content))
resp.raise_for_status()
data = resp.json().get("data", {})
print("ROOT_KEYS", list(data))
for key in ("sports", "outcomes", "tournaments", "categories"):
    value = data.get(key)
    print("COLLECTION", key, "type", type(value).__name__, "keys", list(value)[:10] if isinstance(value,dict) else len(value) if isinstance(value,list) else None)
    if isinstance(value,dict):
        for k,v in value.items():
            if isinstance(v,list):
                print("SAMPLE", key, k, short(v[:2]))
                break
for key, value in data.items():
    blob = json.dumps(value, ensure_ascii=False)
    if re.search(r"corner|rożn|corners", blob, re.I):
        print("CORNER_MATCH_IN", key)
        print("CORNER_DATA", short(blob, 4000))

print("\nDISCOVERY_FINISHED")
