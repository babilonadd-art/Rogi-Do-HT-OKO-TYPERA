import logging
from betclic_api import BetclicClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

# Test only: do not send tips or change the production scanner.
client = BetclicClient(locale="pl", timeout=(8, 15))
client._session.headers.update({
    "x-bg-regulation": "PL",
    "x-bg-ref-regulator-zone": "PL",
    "Origin": "https://www.betclic.pl",
    "Referer": "https://www.betclic.pl/",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
})

try:
    result = client.get_matches("football")
    matches = result.get("matches", [])
    print(f"RESULT: total={result.get('total')} returned={len(matches)}")
    if not matches:
        print("NO_MATCHES: Polish configuration returned no football events.")
    for match in matches[:25]:
        print(f"MATCH: id={match.id} live={match.is_live} date={match.date} competition={match.competition} name={match.name}")
        try:
            detail = client.get_match(match.id)
            found = []
            for market in detail.markets:
                market_name = market.name or ""
                if "corner" in market_name.lower() or "róg" in market_name.lower() or "roż" in market_name.lower():
                    selections = ", ".join(f"{s.name}={s.odds}" for s in market.selections)
                    found.append(f"{market_name}: {selections}")
            if found:
                for item in found:
                    print(f"CORNER_MARKET: {item}")
            else:
                print("CORNER_MARKET: none in returned default markets")
        except Exception as exc:
            print(f"DETAIL_ERROR: match={match.id} error={type(exc).__name__}: {exc}")
    print("TEST_FINISHED")
except Exception as exc:
    print(f"TEST_FAILED: {type(exc).__name__}: {exc}")
    raise
