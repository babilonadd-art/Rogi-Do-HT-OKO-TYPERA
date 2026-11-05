import json
import re
from datetime import datetime, timedelta, timezone
import requests

BASE = "https://production-superbet-offer-pl.freetls.fastly.net"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
    "Accept": "application/json,text/event-stream,*/*",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    "Origin": "https://superbet.pl",
    "Referer": "https://superbet.pl/",
}
s = requests.Session()
s.headers.update(HEADERS)

def short(value, limit=2400):
    return re.sub(r"\s+", " ", str(value))[:limit]

print("V3_EVENT_BY_DATE_RFC3339")
now = datetime.now(timezone.utc).replace(microsecond=0)
start = now.isoformat().replace("+00:00", "Z")
end = (now + timedelta(days=2)).isoformat().replace("+00:00", "Z")
tests = [
    {"startDate": start, "sportId": "5"},
    {"startDate": start, "endDate": end, "sportId": "5"},
    {"startDate": start, "endDate": end},
    {"startDate": start, "sport": "5"},
]
event_ids = []
for params in tests:
    try:
        x = s.get(BASE + "/v3/pl-PL/events", params=params, timeout=30)
        print("REQUEST", x.url, "STATUS", x.status_code, "TYPE", x.headers.get("content-type"), "BYTES", len(x.content))
        print("BODY", short(x.text, 1200))
        if x.ok:
            data = x.json()
            print("JSON_TYPE", type(data).__name__, "KEYS", list(data) if isinstance(data, dict) else None)
            if isinstance(data, dict):
                for k,v in data.items():
                    if isinstance(v, list):
                        print("ARRAY", k, len(v), "SAMPLE", short(v[:1], 3500))
                        if k.lower() in ("events", "data", "items"):
                            for ev in v[:10]:
                                if isinstance(ev, dict):
                                    eid = ev.get("id") or ev.get("eventId") or ev.get("event_id")
                                    if eid is not None:
                                        event_ids.append(str(eid))
            elif isinstance(data, list):
                print("ARRAY_LEN", len(data), "SAMPLE", short(data[:1], 3500))
                for ev in data[:10]:
                    if isinstance(ev, dict):
                        eid = ev.get("id") or ev.get("eventId") or ev.get("event_id")
                        if eid is not None:
                            event_ids.append(str(eid))
    except Exception as exc:
        print("REQUEST_ERROR", type(exc).__name__, str(exc)[:200])

print("\nV3_EVENT_BY_ID")
for eid in list(dict.fromkeys(event_ids))[:3]:
    try:
        x = s.get(BASE + "/v3/pl-PL/events", params={"events": eid}, timeout=20)
        print("EVENT_ID_REQUEST", eid, x.status_code, x.headers.get("content-type"), len(x.content), short(x.text, 3500))
    except Exception as exc:
        print("EVENT_ID_ERROR", eid, type(exc).__name__, str(exc)[:180])

print("\nDISCOVERY_FINISHED")
