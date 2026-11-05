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

def short(value, limit=1800):
    return re.sub(r"\s+", " ", str(value))[:limit]

now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
start = now.isoformat().replace("+00:00", "Z")
end = (now + timedelta(days=2)).isoformat().replace("+00:00", "Z")
r = s.get(BASE + "/v3/pl-PL/events", params={"startDate": start, "endDate": end}, timeout=60)
print("EVENT_LIST", r.status_code, len(r.content))
r.raise_for_status()
events = r.json().get("events", [])
football = [e for e in events if str(e.get("fixture", {}).get("sport_id")) == "5"]
print("TOTAL_EVENTS", len(events), "FOOTBALL", len(football))
future = [
    e for e in football
    if e.get("fixture", {}).get("utc_date", "") > datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
]
print("FUTURE_FOOTBALL", len(future))
print("FUTURE_SAMPLE", short([
    {"id":e.get("event_id"),"match":e.get("fixture",{}).get("event_name"),
     "kickoff":e.get("fixture",{}).get("utc_date"),"market_count":e.get("inplay_stats_metadata",{}).get("market_count"),
     "markets_returned":len(e.get("markets",[]))}
    for e in future[:12]
], 3000))

market_counts = Counter()
corner_hits = []
for listed in future[:18]:
    eid = listed.get("event_id")
    fx = listed.get("fixture", {})
    try:
        detail = s.get(BASE + "/v3/pl-PL/events", params={"events": str(eid)}, timeout=30)
        print("DETAIL", eid, detail.status_code, len(detail.content))
        detail.raise_for_status()
        full = detail.json().get("events", [])
        if not full:
            continue
        ev = full[0]
        markets = ev.get("markets", [])
        print("DETAIL_MARKETS", eid, len(markets), "match", fx.get("event_name"))
        for market in markets:
            name = str(market.get("name", ""))
            market_counts[name] += 1
            if re.search(r"corner|corners|rożn|rzut.{0,5}rożn", name, re.I):
                odds = []
                for odd in market.get("odds", []):
                    meta = odd.get("metadata", {})
                    if odd.get("status") == 1 and odd.get("display", True):
                        odds.append({
                            "name":meta.get("name"),"info":meta.get("info"),
                            "line":meta.get("special_bet_value"),"price":odd.get("price"),
                            "offer_state_id":meta.get("offer_state_id"),
                        })
                corner_hits.append({
                    "id":eid,"match":fx.get("event_name"),"kickoff":fx.get("utc_date"),
                    "market_id":market.get("id"),"name":name,"odds":odds[:20]
                })
    except Exception as exc:
        print("DETAIL_ERROR", eid, type(exc).__name__, str(exc)[:200])

print("CORNER_HITS", len(corner_hits))
print("CORNER_EXAMPLES", short(corner_hits[:30], 12000))
print("COMMON_MARKETS", short(market_counts.most_common(100), 5000))
print("PROBE_FINISHED")
