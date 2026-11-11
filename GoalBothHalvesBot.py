import os
import re
import json
import sqlite3
import logging
import unicodedata
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import requests

API = "https://production-superbet-offer-pl.freetls.fastly.net"
TZ = ZoneInfo("Europe/Warsaw")
DB = "okotypera_rogi.db"
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")
MAX_PICKS_PER_DAY = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
http = requests.Session()
http.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,*/*",
    "Origin": "https://superbet.pl",
    "Referer": "https://superbet.pl/",
})


def api_events():
    now_local = datetime.now(TZ)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start = start_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    end = end_local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    response = http.get(API + "/v3/pl-PL/events",
                        params={"startDate": start, "endDate": end}, timeout=25)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Nieoczekiwany format odpowiedzi Superbet")
    return payload.get("events") or []


def normalize(value):
    value = unicodedata.normalize("NFKD", str(value or "").lower())
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def event_id(event):
    return str(event.get("event_id") or event.get("id") or "")


def event_teams(event):
    fixture = event.get("fixture") or {}
    name = str(fixture.get("event_name") or "")
    parts = re.split(r"\s*[·–-]\s*", name, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", ""


def status_values(event):
    found = []
    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                key = str(key).lower()
                if key in {"status", "event_status", "inplay_status", "match_status"} and value is not None:
                    found.append(str(value).upper())
                elif key in {"is_live", "islive", "inplay", "is_inplay", "isinplay"} and isinstance(value, bool):
                    found.append("LIVE" if value else "NOT_LIVE")
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(event)
    return found


def kickoff_timestamp(event):
    keys = {"start_time", "starttime", "start_date", "startdate", "kickoff",
            "kick_off", "scheduled_at", "scheduledat", "event_start", "eventstart"}
    found = []
    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() in keys and value is not None:
                    try:
                        if isinstance(value, (int, float)) or str(value).isdigit():
                            stamp = float(value)
                            if stamp > 10_000_000_000:
                                stamp /= 1000
                            found.append(stamp)
                        else:
                            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                            if parsed.tzinfo is None:
                                parsed = parsed.replace(tzinfo=timezone.utc)
                            found.append(parsed.timestamp())
                    except (ValueError, TypeError, OverflowError):
                        pass
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(event)
    return min(found) if found else None


def is_prematch(event):
    values = status_values(event)
    rejected = ("LIVE", "INPLAY", "IN_PLAY", "FIRST_HALF", "SECOND_HALF",
                "HALFTIME", "STARTED", "IN PROGRESS", "FINISHED", "ENDED",
                "CANCELLED", "POSTPONED", "ABANDONED")
    if any(any(token in value for token in rejected) for value in values):
        return False
    prematch_tokens = ("NOT_STARTED", "NOT STARTED", "PREMATCH", "PRE_MATCH", "SCHEDULED", "NOT_LIVE")
    if any(any(token in value for token in prematch_tokens) for value in values):
        return True
    kickoff = kickoff_timestamp(event)
    return kickoff is not None and kickoff > datetime.now(timezone.utc).timestamp()


def is_both_halves_market(name):
    n = normalize(name)
    exact = {
        "goal in both halves", "goals in both halves",
        "goal scored in both halves", "goals scored in both halves",
        "both halves over 0 5 goals", "over 0 5 goals in both halves",
        "gol w obu polowach", "gole w obu polowach",
        "gol w kazdej polowie", "bramka w obu polowach",
        "bramki w obu polowach", "obie polowy gol",
        "obie polowy powyzej 0 5 gola",
    }
    return n in exact


def iter_markets(event):
    found = []
    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("odds"), list) and node.get("name"):
                found.append(node)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(event.get("markets") or [])
    return found


def both_halves_yes_market(event):
    if not is_prematch(event):
        return None
    matches = []
    for market in iter_markets(event):
        name = market.get("name") or market.get("market_name") or ""
        if not is_both_halves_market(name):
            continue
        if market.get("status") not in (None, 1, 2, "1", "2", "ACTIVE", "active",
                                        "OPEN", "open", "AVAILABLE", "available", True):
            continue
        for odd in market.get("odds") or []:
            meta = odd.get("metadata") or {}
            label = normalize(" ".join(str(x or "") for x in (
                odd.get("name"), odd.get("label"), meta.get("name"), meta.get("info")
            )))
            if label not in {"yes", "tak", "oba tak", "obie tak"}:
                continue
            try:
                price = float(odd.get("price"))
            except (TypeError, ValueError):
                continue
            if price <= 1.0:
                continue
            if odd.get("status") not in (None, 1, 2, "1", "2", "ACTIVE", "active",
                                         "OPEN", "open", "AVAILABLE", "available", True):
                continue
            if odd.get("display") is False:
                continue
            if str(meta.get("offer_state_id", 1)) not in ("1", "2"):
                continue
            matches.append({"market": str(name), "odds": price})
    return max(matches, key=lambda x: x["odds"]) if matches else None


def event_url(event):
    keys = {"url", "event_url", "eventurl", "permalink", "web_url", "weburl", "event_link"}
    found = []
    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() in keys and isinstance(value, str):
                    candidate = value.strip()
                    if candidate.startswith("https://") and "superbet.pl/" in candidate.lower():
                        found.append(candidate)
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(event)
    return found[0] if found else None


def db_connect():
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS both_halves_sent(
        event_id TEXT PRIMARY KEY, sent_at TEXT NOT NULL, home TEXT NOT NULL,
        away TEXT NOT NULL, odds REAL NOT NULL)""")
    conn.commit()
    return conn


def daily_count(conn):
    today = datetime.now(TZ).date().isoformat()
    return conn.execute("SELECT COUNT(*) FROM both_halves_sent WHERE substr(sent_at,1,10)=?",
                        (today,)).fetchone()[0]


def send_telegram(message, url=None):
    if not TOKEN or not CHAT:
        logging.error("Brak TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        return False
    data = {"chat_id": CHAT, "text": message, "disable_web_page_preview": True}
    if url:
        data["reply_markup"] = json.dumps({"inline_keyboard": [[
            {"text": "⚽ OTWÓRZ TEN MECZ", "url": url}
        ]]}, ensure_ascii=False)
    response = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                             data=data, timeout=20)
    if not response.ok:
        logging.error("Telegram HTTP %s: %s", response.status_code, response.text[:300])
        return False
    try:
        return bool(response.json().get("ok"))
    except ValueError:
        return False


def main():
    if not TOKEN or not CHAT:
        logging.error("Brak sekretów Telegrama; kończę bez publikacji.")
        return
    conn = db_connect()
    try:
        remaining = max(0, MAX_PICKS_PER_DAY - daily_count(conn))
        if not remaining:
            logging.info("Dzienny limit %d typów osiągnięty.", MAX_PICKS_PER_DAY)
            return
        events = api_events()
        football = [e for e in events if str((e.get("fixture") or {}).get("sport_id")) == "5"]
        logging.info("Superbet: %d wydarzeń piłkarskich z %d wszystkich.", len(football), len(events))
        candidates = []
        for event in football:
            eid = event_id(event)
            if not eid or conn.execute("SELECT 1 FROM both_halves_sent WHERE event_id=?", (eid,)).fetchone():
                continue
            home, away = event_teams(event)
            if not home or not away:
                continue
            selection = both_halves_yes_market(event)
            if selection:
                candidates.append((event, eid, home, away, selection))
        candidates.sort(key=lambda item: kickoff_timestamp(item[0]) or float("inf"))
        logging.info("Znaleziono %d prematch rynków Gol w obu połowach — TAK.", len(candidates))
        sent = 0
        for event, eid, home, away, selection in candidates[:remaining]:
            # Ponownie sprawdź rynek przed publikacją, żeby nie wysłać kursu, który zniknął.
            fresh = event
            current = both_halves_yes_market(fresh)
            if not current:
                continue
            message = (
                f"⚽ GOL W OBU POŁOWACH — TAK\n"
                f"🏟 {home} – {away}\n"
                f"💹 Kurs Superbet: {current['odds']:.2f}\n"
                f"📌 Rynek: {current['market']}\n"
                f"🕒 Typ przed rozpoczęciem meczu"
            )
            if send_telegram(message, event_url(fresh)):
                conn.execute("INSERT OR IGNORE INTO both_halves_sent VALUES(?,?,?,?,?)",
                             (eid, datetime.now(TZ).isoformat(), home, away, current["odds"]))
                conn.commit()
                sent += 1
        logging.info("Wysłano %d/%d typów Gol w obu połowach.", sent, min(remaining, len(candidates)))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
