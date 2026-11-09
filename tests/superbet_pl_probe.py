import re
from collections import Counter
from datetime import datetime, timedelta, timezone
import requests

BASE = "https://production-superbet-offer-pl.freetls.fastly.net"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
    "Accept": "application/json,*/*",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    "Origin": "https://superbet.pl",
    "Referer": "https://superbet.pl/",
}
s = requests.Session()
s.headers.update(HEADERS)

def short(value, limit=2000):
    return re.sub(r"\s+", " ", str(value))[:limit]

now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
start = now.isoformat().replace("+00:00", "Z")
end = (now + timedelta(days=2)).isoformat().replace("+00:00", "Z")
r = s.get(BASE + "/v3/pl-PL/events", params={"startDate": start, "endDate": end}, timeout=60)
r.raise_for_status()
events = r.json().get("events", [])
football = [e for e in events if str(e.get("fixture", {}).get("sport_id")) == "5"]
future = [
    e for e in football
    if e.get("fixture", {}).get("utc_date", "") > datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
]
print("FOOTBALL_EVENTS", len(football), "FUTURE", len(future))
corner_market_hits = []
for listed in future[:40]:
    eid = listed.get("event_id")
    fx = listed.get("fixture", {})
    try:
        detail = s.get(BASE + "/v3/pl-PL/events", params={"events": str(eid)}, timeout=35)
        detail.raise_for_status()
        full = detail.json().get("events", [])
        if not full:
            continue
        ev = full[0]
        if len(corner_market_hits)<3:
            print("EVENT_DETAIL_KEYS", sorted(ev.keys()))
            print("EVENT_FIXTURE_KEYS", sorted((ev.get("fixture") or {}).keys()))
            print("EVENT_URL_FIELDS", {k:ev.get(k) for k in ("url","event_url","eventUrl","permalink","web_url","webUrl","deep_link","deepLink") if ev.get(k)})
            print("FIXTURE_URL_FIELDS", {k:(ev.get("fixture") or {}).get(k) for k in ("url","event_url","eventUrl","permalink","web_url","webUrl","deep_link","deepLink") if (ev.get("fixture") or {}).get(k)})
        for market in ev.get("markets", []):
            name = str(market.get("name", ""))
            if re.search(r"rożn|rożnych|rzutów rożnych", name, re.I):
                if len(corner_market_hits)<3:
                    print("CORNER_MARKET_KEYS", sorted(market.keys()), "MARKET_URL_FIELDS",
                          {k:market.get(k) for k in ("url","event_url","eventUrl","permalink","web_url","webUrl","deep_link","deepLink") if market.get(k)})
                odds = []
                for odd in market.get("odds", []):
                    meta = odd.get("metadata", {})
                    odds.append({
                        "name": meta.get("name") or odd.get("name"),
                        "info": meta.get("info"),
                        "line": meta.get("special_bet_value"),
                        "price": odd.get("price"),
                        "status": odd.get("status"),
                        "display": odd.get("display"),
                        "offer_state_id": meta.get("offer_state_id"),
                    })
                corner_market_hits.append({
                    "event_id": eid,
                    "match": fx.get("event_name"),
                    "kickoff": fx.get("utc_date"),
                    "market_id": market.get("id"),
                    "market_name": name,
                    "odds": odds,
                })
    except Exception as exc:
        print("DETAIL_ERROR", eid, type(exc).__name__, str(exc)[:150])

print("FOOTBALL_EVENTS", len(football), "FUTURE", len(future))
print("ALL_CORNER_MARKETS_FOUND", len(corner_market_hits))
print("ALL_CORNER_MARKET_SAMPLES", short(corner_market_hits[:60], 30000))
print("PROBE_FINISHED")


# Competition hierarchy used by Superbet's own frontend for tournament names.
try:
    struct_response = s.get(BASE + "/v2/pl-PL/struct", timeout=40)
    struct_response.raise_for_status()
    structure = struct_response.json()
    print("STRUCT_TOP_KEYS", list(structure)[:30] if isinstance(structure, dict) else type(structure).__name__)
    print("STRUCT_DATA_KEYS", list(structure.get("data", {})) if isinstance(structure, dict) else [])
    data = structure.get("data", {}) if isinstance(structure, dict) else {}
    print("STRUCT_TOURNAMENT_COUNT", len(data.get("tournaments", [])) if isinstance(data, dict) and isinstance(data.get("tournaments"), list) else "missing")
    print("STRUCT_TOURNAMENT_SAMPLE", short(data.get("tournaments", [])[:8] if isinstance(data, dict) else [], 5000))
    print("STRUCT_CATEGORY_SAMPLE", short(data.get("categories", [])[:4] if isinstance(data, dict) else [], 2000))
except Exception as exc:
    print("STRUCT_ERROR", type(exc).__name__, str(exc)[:200])
