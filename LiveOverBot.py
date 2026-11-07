import os
import re
import time
import sqlite3
import logging
import unicodedata
from html.parser import HTMLParser
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import requests

SOURCE_URL = "https://www.iddaaorantahmin.com/canli-iddaa-tahmin"
SUPERBET_API = "https://production-superbet-offer-pl.freetls.fastly.net"
TZ = ZoneInfo("Europe/Warsaw")
DB = "okotypera_rogi.db"
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")

# Późne LIVE OVERY: wyłącznie typy z minut 60–85 i wysokim wskazaniem źródła.
MIN_MINUTE = 60
MAX_MINUTE = 85
MIN_CONFIDENCE = 75
MIN_ODDS = 1.90
MAX_ODDS = 10.00
MAX_PICKS_PER_RUN = 3
STAKE = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
http = requests.Session()
http.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
    "Accept": "text/html,application/json,*/*",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    "Origin": "https://www.iddaaorantahmin.com",
    "Referer": SOURCE_URL,
})
sb = requests.Session()
sb.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
    "Accept": "application/json,*/*",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    "Origin": "https://superbet.pl",
    "Referer": "https://superbet.pl/",
})


def norm(value):
    value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    value = re.sub(r"\b(fc|cf|sc|afc|club|the)\b", " ", value)
    return re.sub(r"[^a-z0-9]", "", value)


def similarity(a, b):
    from difflib import SequenceMatcher
    x, y = norm(a), norm(b)
    if not x or not y:
        return 0.0
    if x == y or x in y or y in x:
        return 1.0 if min(len(x), len(y)) >= 5 else 0.85
    return SequenceMatcher(None, x, y).ratio()


class MatchRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.capture = 0
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self.row = {"text": [], "links": []}
        elif self.row is not None and tag == "a":
            self.anchor = {"href": attrs.get("href", ""), "text": []}

    def handle_data(self, data):
        value = " ".join(data.split())
        if not value:
            return
        if self.row is not None:
            self.row["text"].append(value)
            if self.anchor is not None:
                self.anchor["text"].append(value)

    def handle_endtag(self, tag):
        if tag == "a" and self.row is not None and self.anchor is not None:
            self.row["links"].append((self.anchor["href"], " ".join(self.anchor["text"])))
            self.anchor = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
            self.anchor = None


def parse_source_row(row):
    text = " | ".join(row["text"])
    # Źródło pokazuje również UNDER, double chance i inne rynki — odrzucamy je.
    pick_match = re.search(r"(?:İY\s*)?(\d+(?:[.,]\d+)?)\s*(?:ÜST|OVER)\b", text, re.I)
    if not pick_match:
        return None
    line = float(pick_match.group(1).replace(",", "."))
    first_half = bool(re.search(r"\bİY\b|\bİLK YARI\b|\b1\. YARI\b", text, re.I))
    # Późny typ musi mieć czytelną minutę spotkania.
    minute_match = re.search(r"\b(\d{1,2})(?:\+\d+)?\s*['’]", text)
    if not minute_match:
        return None
    minute = int(minute_match.group(1))
    if not MIN_MINUTE <= minute <= MAX_MINUTE:
        return None
    confidence_match = re.search(r"(?:%\s*(\d{2,3})|(\d{2,3})\s*%)", text)
    if not confidence_match:
        return None
    confidence = int(confidence_match.group(1) or confidence_match.group(2))
    if confidence < MIN_CONFIDENCE:
        return None
    score_match = re.search(r"(\d{1,2})\s*[-–:]\s*(\d{1,2})", text)
    if not score_match:
        return None
    # Drużyny są wyciągane z linku do konkretnego meczu, nie z tekstu ligowego.
    match_link = next(((href, label) for href, label in row["links"]
                       if href and re.search(r"\s[-–]\s", label)), None)
    if not match_link:
        return None
    href, teams = match_link
    parts = re.split(r"\s[-–]\s", teams, maxsplit=1)
    if len(parts) != 2:
        return None
    return {
        "home": parts[0].strip(), "away": parts[1].strip(),
        "source_url": href if href.startswith("http") else "https://www.iddaaorantahmin.com" + href,
        "line": line, "first_half": first_half, "minute": minute,
        "confidence": confidence, "home_score": int(score_match.group(1)),
        "away_score": int(score_match.group(2)), "raw": text,
    }


def get_source_picks():
    response = http.get(SOURCE_URL, timeout=25)
    response.raise_for_status()
    parser = MatchRows()
    parser.feed(response.text)
    picks = [p for row in parser.rows if (p := parse_source_row(row))]
    # Fallback parser: the server may render cards without table rows.
    if not picks:
        logging.warning("Źródło nie zwróciło czytelnych wierszy tabeli; bezpiecznie pomijam ten przebieg.")
    logging.info("IddaaOranTahmin: %s późnych typów OVER spełnia filtr", len(picks))
    return picks


def superbet_api(path, params=None):
    response = sb.get(SUPERBET_API + path, params=params, timeout=25)
    response.raise_for_status()
    return response.json()


def live_superbet_events():
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    payload = superbet_api("/v3/pl-PL/events", {
        "startDate": start.isoformat().replace("+00:00", "Z"),
        "endDate": end.isoformat().replace("+00:00", "Z"),
    })
    events = payload.get("events") or []
    live = []
    for event in events:
        fixture = event.get("fixture") or {}
        if str(fixture.get("sport_id")) != "5":
            continue
        status = str((event.get("inplay_stats_metadata") or {}).get("status") or "").upper()
        if status in ("", "NOT_STARTED", "FINISHED", "ENDED", "CANCELLED", "POSTPONED"):
            continue
        if not any(token in status for token in ("LIVE", "INPLAY", "IN_PLAY", "PERIOD", "HALF")):
            continue
        live.append(event)
    logging.info("Superbet: %s wydarzeń LIVE piłki nożnej", len(live))
    return live


def event_teams(event):
    fixture = event.get("fixture") or {}
    parts = re.split(r"\s*[·–-]\s*", str(fixture.get("event_name") or ""), maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ("", "")


def find_superbet_event(pick, events):
    best = None
    for event in events:
        home, away = event_teams(event)
        direct = similarity(pick["home"], home) + similarity(pick["away"], away)
        reverse = similarity(pick["home"], away) + similarity(pick["away"], home)
        score = max(direct, reverse)
        if score < 1.72:
            continue
        if best is None or score > best[0]:
            best = (score, event)
    return best[1] if best else None


def exact_live_over_price(event_id, line, first_half):
    payload = superbet_api("/v3/pl-PL/events", {"events": str(event_id)})
    events = payload.get("events") or []
    if len(events) == 1:
        event = events[0]
    else:
        event = next((e for e in events if str(e.get("event_id") or e.get("id")) == str(event_id)), None)
    if not event:
        return None
    status = str((event.get("inplay_stats_metadata") or {}).get("status") or "").upper()
    if status in ("", "NOT_STARTED", "FINISHED", "ENDED", "CANCELLED", "POSTPONED"):
        return None
    prices = []
    for market in event.get("markets") or []:
        market_name = str(market.get("name") or "").lower()
        if not any(word in market_name for word in ("total", "gole", "goals", "bramki")):
            continue
        if any(word in market_name for word in ("corner", "roż", "kart", "cards", "rzut")):
            continue
        is_first_half_market = any(word in market_name for word in
                                   ("1st half", "first half", "1. połowa", "1 połowa", "pierwsza połowa", "1h"))
        if first_half != is_first_half_market:
            continue
        if market.get("status") not in (None, 1, "1", "ACTIVE", "active"):
            continue
        for odd in market.get("odds") or []:
            meta = odd.get("metadata") or {}
            raw_line = meta.get("special_bet_value", odd.get("special_bet_value"))
            try:
                market_line = float(str(raw_line).replace(",", "."))
                price = float(odd.get("price"))
            except (TypeError, ValueError):
                continue
            labels = " ".join(str(v or "") for v in
                              (odd.get("name"), odd.get("label"), meta.get("name"), meta.get("info"))).lower()
            if not re.search(r"\b(over|powyżej|powyzej|üst)\b", labels):
                continue
            if abs(market_line - line) > 0.001:
                continue
            if odd.get("status") not in (1, "1", "ACTIVE", "active") or odd.get("display") is False:
                continue
            if str(meta.get("offer_state_id", 1)) != "1":
                continue
            if MIN_ODDS <= price <= MAX_ODDS:
                prices.append(price)
    return max(prices) if prices else None


def superbet_url(event):
    for obj in (event, event.get("fixture") or {}):
        for key in ("url", "event_url", "eventUrl", "permalink", "web_url", "webUrl"):
            value = obj.get(key) if isinstance(obj, dict) else None
            if isinstance(value, str) and value.startswith("https://") and "superbet" in value.lower():
                return value
    return "https://superbet.pl/zaklady-bukmacherskie/live"


def db_connect():
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS live_over_sent(
        pick_key TEXT PRIMARY KEY, event_id TEXT NOT NULL, sent_at TEXT NOT NULL,
        home TEXT NOT NULL, away TEXT NOT NULL, line REAL NOT NULL,
        first_half INTEGER NOT NULL, odds REAL NOT NULL, minute INTEGER NOT NULL,
        confidence INTEGER NOT NULL)""")
    conn.commit()
    return conn


def send_telegram(message):
    if not TOKEN or not CHAT:
        logging.error("Brak TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        return False
    response = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT, "text": message, "disable_web_page_preview": True},
        timeout=20,
    )
    if not response.ok:
        logging.error("Telegram HTTP %s: %s", response.status_code, response.text[:300])
    return response.ok


def main():
    if not TOKEN or not CHAT:
        logging.error("Brak sekretów Telegrama; kończę bez publikacji.")
        return
    conn = db_connect()
    try:
        source_picks = get_source_picks()
        if not source_picks:
            return
        events = live_superbet_events()
        candidates = []
        for pick in source_picks:
            event = find_superbet_event(pick, events)
            if not event:
                continue
            event_id = event.get("event_id") or event.get("id")
            if not event_id:
                continue
            price = exact_live_over_price(event_id, pick["line"], pick["first_half"])
            if price is None:
                logging.info("Pominięto %s - %s: brak dokładnego aktywnego rynku LIVE OVER",
                             pick["home"], pick["away"])
                continue
            key = f"{event_id}|{'HT' if pick['first_half'] else 'FT'}|OVER|{pick['line']:.1f}"
            exists = conn.execute("SELECT 1 FROM live_over_sent WHERE pick_key=?", (key,)).fetchone()
            if exists:
                continue
            candidates.append((pick["confidence"], price, pick, event, str(event_id), key))
        candidates.sort(key=lambda item: (-item[0], -item[1]))
        for confidence, price, pick, event, event_id, key in candidates[:MAX_PICKS_PER_RUN]:
            home, away = event_teams(event)
            period = "DO PRZERWY" if pick["first_half"] else "CAŁY MECZ"
            market = f"OVER {pick['line']:.1f} GOLI — {period}"
            link = superbet_url(event)
            message = (
                f"🔴 LIVE OVER | {pick['minute']}' | {pick['home_score']}:{pick['away_score']}\n"
                f"⚽ {home} – {away}\n"
                f"📌 Typ: {market}\n"
                f"📈 Wskazanie źródła: {confidence}%\n"
                f"💹 Kurs Superbet LIVE: {price:.2f}\n"
                f"💰 Stawka: {STAKE} zł\n"
                f"🔗 Mecz: {link}\n\n"
                f"Źródło wskazania: iddaaorantahmin.com"
            )
            if send_telegram(message):
                conn.execute(
                    "INSERT OR IGNORE INTO live_over_sent VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (key, event_id, datetime.now(TZ).isoformat(), pick["home"], pick["away"],
                     pick["line"], int(pick["first_half"]), price, pick["minute"], confidence),
                )
                conn.commit()
                logging.info("Wysłano LIVE OVER: %s - %s | %s | %.2f",
                             pick["home"], pick["away"], market, price)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
