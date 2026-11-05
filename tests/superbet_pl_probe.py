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

def short(value, limit=500):
    return re.sub(r"\s+", " ", value)[:limit]

print("FETCH SITE")
r = s.get(SITE, timeout=30)
print("SITE", r.status_code, len(r.content), r.url)
r.raise_for_status()

scripts = re.findall(r"<script[^>]+src=['\\\"]([^'\\\"]+)", r.text, re.I)
print("SCRIPT_COUNT", len(scripts))
seen = set()
for src in scripts:
    url = urljoin(SITE, src)
    if url in seen:
        continue
    seen.add(url)
    try:
        js = s.get(url, timeout=30)
        print("\nBUNDLE", js.status_code, len(js.content), url)
        if not js.ok or len(js.content) > 20_000_000:
            continue
        body = js.text
        patterns = [
            r"""["']([^"']{0,100}(?:production-superbet-offer-pl|/v[0-9]+/pl-PL/)[^"']{0,180})["']""",
            r"""["']([^"']{0,100}(?:offerState|sportId|eventId|tournamentId)[^"']{0,180})["']""",
        ]
        found = set()
        for pattern in patterns:
            found.update(re.findall(pattern, body, re.I))
        for item in sorted(found):
            print("MATCH", short(item, 280))
    except Exception as exc:
        print("BUNDLE_ERROR", type(exc).__name__, str(exc)[:200])

print("\nSTRUCTURE_CHECK")
try:
    x = s.get(BASE + "/v2/pl-PL/struct", timeout=30)
    print("/v2/pl-PL/struct", x.status_code, len(x.content))
    data = x.json().get("data", {})
    print("ROOT_KEYS", list(data.keys()))
    for sport in data.get("marketTree", []):
        if str(sport.get("sportId")) in ("1", "5"):
            print("SPORT_MARKET_TREE", sport.get("sportId"), short(str(sport), 3500))
except Exception as exc:
    print("STRUCT_ERROR", type(exc).__name__, str(exc)[:200])

print("\nDISCOVERY_FINISHED")
