import json
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

def short(value, limit=1500):
    return re.sub(r"\s+", " ", str(value))[:limit]

now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
start = now.isoformat().replace("+00:00", "Z")
end = (now + timedelta(days=2)).isoformat().replace("+00:00", "Z")
print("FETCH_EVENTS", start, end)
r = s.get(BASE + "/v3/pl-PL/events", params={"startDate": start, "endDate": end}, timeout=60)
print("HTTP", r.status_code, "BYTES", len(r.content))
r.raise_for_status()
events = r.json().get("events", [])
print("TOTAL_EVENTS", len(events))
sports = Counter(str(e.get("fixture", {}).get("sport_id")) for e in events)
print("SPORT_COUNTS", dict(sports))
football = [e for e in events if str(e.get("fixture", {}).get("sport_id")) == "5"]
print("FOOTBALL_EVENTS", len(football))
print("FOOTBALL_SAMPLE", short([
    {
        "id": e.get("event_id"),
        "name": e.get("fixture", {}).get("event_name"),
        "date": e.get("fixture", {}).get("utc_date"),
        "tournament": e.get("fixture", {}).get("tournament_id"),
        "markets": len(e.get("markets", [])),
    } for e in football[:8]
], 3000))

corner_markets = []
all_market_names = Counter()
for ev in football:
    fx = ev.get("fixture", {})
    for market in ev.get("markets", []):
        name = str(market.get("name", ""))
        all_market_names[name] += 1
        if re.search(r"corner|corners|rożn|rzut.{0,5}rożn", name, re.I):
            odds = []
            for odd in market.get("odds", []):
                meta = odd.get("metadata", {})
                if odd.get("status") == 1:
                    odds.append({
                        "name": meta.get("name"),
                        "info": meta.get("info"),
                        "line": meta.get("special_bet_value"),
                        "price": odd.get("price"),
                        "status": odd.get("status"),
                        "offer_state_id": meta.get("offer_state_id"),
                    })
            corner_markets.append({
                "event_id": ev.get("event_id"),
                "match": fx.get("event_name"),
                "kickoff": fx.get("utc_date"),
                "market_id": market.get("id"),
                "market_name": name,
                "odds": odds[:16],
            })
print("CORNER_MARKET_COUNT", len(corner_markets))
print("CORNER_MARKET_EXAMPLES", short(corner_markets[:25], 12000))
print("TOP_FOOTBALL_MARKET_NAMES", short(all_market_names.most_common(100), 5000))

print("PROBE_FINISHED")
