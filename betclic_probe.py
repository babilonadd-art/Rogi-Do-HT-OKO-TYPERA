import logging
from betclic_api import BetclicClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

def main():
    client = BetclicClient(locale="pl", timeout=(8, 15))
    # Test the Polish Betclic web offer, not the library's French defaults.
    client._session.headers.update({
        "x-bg-ref-platform": "DESKTOP",
        "x-bg-regulation": "PL",
        "x-bg-ref-regulator-zone": "PL",
        "x-bg-ref-brand": "BETCLIC",
        "Origin": "https://www.betclic.pl",
        "Referer": "https://www.betclic.pl/",
        "Accept-Language": "pl-PL,pl;q=0.9",
    })
    try:
        result = client.get_matches("football", offset=0)
        matches = result.get("matches", [])
        logging.info("BETCLIC_PL_MATCHES total=%s first_page=%s", result.get("total"), len(matches))
        if not matches:
            logging.warning("BETCLIC_PL_EMPTY_BOARD — brak potwierdzenia polskiej oferty")
            return
        # Inspect several events only; this is a low-volume connectivity probe.
        for item in matches[:3]:
            logging.info("BETCLIC_PL_EVENT id=%s name=%s competition=%s", item.id, item.name, item.competition)
            if not item.id:
                continue
            detail = client.get_match(item.id)
            names = [m.name for m in detail.markets]
            corner = [m for m in detail.markets if any(k in m.name.casefold() for k in ("corner", "roż", "rozny", "rożny", "korner"))]
            logging.info("BETCLIC_PL_MARKETS event=%s count=%s names=%s", item.id, len(names), names[:40])
            logging.info("BETCLIC_PL_CORNER_MARKETS event=%s names=%s", item.id, [m.name for m in corner])
        logging.info("BETCLIC_PL_PROBE_DONE — wyniki wymagają ręcznej weryfikacji nazw rynku i linii HT")
    except Exception:
        logging.exception("BETCLIC_PL_PROBE_FAILED")

if __name__ == "__main__":
    main()
