import json
import re
from datetime import datetime, timedelta, timezone
import requests
from urllib.parse import urljoin

BASE = "https://production-superbet-offer-pl.freetls.fastly.net"
SITE = "https://superbet.pl/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
    "Accept": "application/json,text/event-stream,*/*",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    "Origin": "https://superbet.pl",
    "Referer": SITE,
}
s = requests.Session()
s.headers.update(HEADERS)

def short(value, limit=2200):
    return re.sub(r"\s+", " ", str(value))[:limit]

print("FETCH SITE CONFIG")
page = s.get(SITE, timeout=30)
print("SITE", page.status_code, len(page.content))
page.raise_for_status()
for src in re.findall(r"<script[^>]+src=['\\\"]([^'\\\"]+)", page.text, re.I):
    url = urljoin(SITE, src)
    if url.endswith("/fetchConfig/app"):
        js = s.get(url, timeout=30)
        print("APP_CONFIG", js.status_code, len(js.content))
        for key in ("offerServer", "offerV3RestEventByDateEnabled", "offerV3RestEventByIdEnabled", "offerV3SseCountEnabled"):
            m = re.search(r'"' + re.escape(key) + r'"\\s*:\\s*("[^"]*"|true|false)', js.text)
            if m:
                print("CONFIG", key, m.group(1))

print("\nV3_EVENT_BY_DATE")
today = datetime.now(timezone.utc).date()
dates = [today + timedelta(days=i) for i in range(0, 3)]
tested = [
    ("/v3/pl-PL/events", {"startDate": dates[0].isoformat(), "sportId": "5"}),
    ("/v3/pl-PL/events", {"startDate": dates[0].isoformat()}),
    ("/v3/pl-PL/events", {"startDate": dates[0].isoformat(), "endDate": dates[-1].isoformat(), "sportId": "5"}),
    ("/v3/pl-PL/events", {"startDate": dates[0].isoformat(), "sport": "5"}),
]
event_ids = []
for path, params in tested:
    try:
        x = s.get(BASE + path, params=params, timeout=25)
        print("REQUEST", x.url, "STATUS", x.status_code, "TYPE", x.headers.get("content-type"), "BYTES", len(x.content))
        print("BODY", short(x.text, 1000))
        if x.ok and "json" in x.headers.get("content-type", ""):
            data = x.json()
            print("JSON_KEYS", list(data) if isinstance(data, dict) else type(data).__name__)
            if isinstance(data, dict):
                for k,v in data.items():
                    if isinstance(v, list):
                        print("ARRAY", k, len(v), "SAMPLE", short(v[:1], 2500))
                        if k.lower() in ("events", "data", "items"):
                            for ev in v[:5]:
                                if isinstance(ev, dict):
                                    eid = ev.get("id") or ev.get("eventId") or ev.get("event_id")
                                    if eid is not None:
                                        event_ids.append(str(eid))
            elif isinstance(data, list):
                print("ARRAY_LEN", len(data), "SAMPLE", short(data[:1], 2500))
    except Exception as exc:
        print("REQUEST_ERROR", type(exc).__name__, str(exc)[:200])

print("\nV3_EVENT_BY_ID")
for eid in list(dict.fromkeys(event_ids))[:3]:
    try:
        x = s.get(BASE + "/v3/pl-PL/events", params={"events": eid}, timeout=20)
        print("EVENT_ID_REQUEST", eid, x.status_code, x.headers.get("content-type"), len(x.content), short(x.text, 3000))
    except Exception as exc:
        print("EVENT_ID_ERROR", eid, type(exc).__name__, str(exc)[:180])

print("\nLIVE_COUNT_REFERENCE")
try:
    x = s.get(BASE + "/v3/subscription/pl-PL/events/live/count", timeout=15, stream=True)
    print("LIVE_COUNT", x.status_code, x.headers.get("content-type"), short(next(x.iter_content(500), b"").decode("utf-8","replace"), 500))
    x.close()
except Exception as exc:
    print("LIVE_COUNT_ERROR", type(exc).__name__, str(exc)[:180])

print("\nDISCOVERY_FINISHED")
