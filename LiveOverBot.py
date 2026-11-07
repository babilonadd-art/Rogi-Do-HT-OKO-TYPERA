import os
import re
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import requests

SUPERBET_API = "https://production-superbet-offer-pl.freetls.fastly.net"
TZ = ZoneInfo("Europe/Warsaw")
DB = "okotypera_rogi.db"
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")

# Późne typy LIVE: wyłącznie pełny mecz, minuty 60–85.
MIN_MINUTE = 60
MAX_MINUTE = 85
MIN_ODDS = 1.90
MAX_ODDS = 10.00
MAX_PICKS_PER_RUN = 3
STAKE = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
http = requests.Session()
http.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
    "Accept": "application/json,*/*",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    "Origin": "https://superbet.pl",
    "Referer": "https://superbet.pl/",
})


def superbet_api(path, params=None):
    response = http.get(SUPERBET_API + path, params=params, timeout=25)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Nieoczekiwany format Superbet dla {path}")
    return payload


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
        metadata = event.get("inplay_stats_metadata") or {}
        status = str(metadata.get("status") or "").upper()
        if status in ("", "NOT_STARTED", "FINISHED", "ENDED", "CANCELLED", "POSTPONED", "ABANDONED"):
            continue
        if status in ("LIVE", "INPLAY", "IN_PLAY", "FIRST_HALF", "SECOND_HALF", "HALFTIME", "1H", "2H") or any(
            word in status for word in ("LIVE", "INPLAY", "IN_PLAY", "PERIOD", "HALF")
        ):
            live.append(event)
    logging.info("Superbet: %d wydarzeń LIVE piłki nożnej z %d wydarzeń", len(live), len(events))
    return live


def event_teams(event):
    fixture = event.get("fixture") or {}
    name = str(fixture.get("event_name") or "")
    parts = re.split(r"\s*[·–-]\s*", name, maxsplit=1)
    if len(parts) != 2:
        return "", ""
    return parts[0].strip(), parts[1].strip()


def event_minute(event):
    """Czyta minutę tylko z jawnie nazwanych pól czasu meczu."""
    keys = {
        "minute", "match_minute", "current_minute", "elapsed_minute",
        "elapsed_minutes", "minute_number", "timer_minute", "inplay_minute",
    }
    clock_keys = {"clock", "timer", "match_time", "elapsed_time", "game_time"}
    found = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                k = str(key).lower()
                if isinstance(value, dict):
                    value = value.get("value", value.get("current", value.get("minute")))
                if k in keys and value is not None:
                    match = re.search(r"\b(\d{1,3})\b", str(value))
                    if match:
                        found.append(int(match.group(1)))
                elif k in clock_keys and value is not None:
                    raw = str(value)
                    match = re.search(r"(\d{1,2})(?:\+\d+)?\s*['′]", raw)
                    if not match:
                        match = re.fullmatch(r"\s*(\d{1,2}):\d{2}\s*", raw)
                    if match:
                        found.append(int(match.group(1)))
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(event)
    valid = [minute for minute in found if 0 <= minute <= 130]
    return max(valid) if valid else None


def event_score(event):
    """Odczytuje wynik wyłącznie z jawnego obiektu score/scoreboard."""
    found = []

    def number(value):
        if isinstance(value, dict):
            value = value.get("value", value.get("current", value.get("total")))
        try:
            n = int(value)
            return n if n >= 0 else None
        except (TypeError, ValueError):
            return None

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() in ("score", "scores", "scoreboard", "current_score") and isinstance(value, dict):
                    home = next((number(value[k]) for k in ("home", "home_score", "homeScore", "team1", "participant1") if k in value), None)
                    away = next((number(value[k]) for k in ("away", "away_score", "awayScore", "team2", "participant2") if k in value), None)
                    if home is not None and away is not None:
                        found.append((home, away))
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(event)
    return found[0] if found else None


def event_is_still_live(event):
    status = str((event.get("inplay_stats_metadata") or {}).get("status") or "").upper()
    return bool(status) and status not in (
        "NOT_STARTED", "FINISHED", "ENDED", "CANCELLED", "POSTPONED", "ABANDONED"
    )


def exact_live_fulltime_over_markets(event):
    """Zwraca wyłącznie aktywne selekcje OVER z dokładną linią z rynku Superbet."""
    if not event_is_still_live(event):
        return []
    results = []
    for market in event.get("markets") or []:
        name = re.sub(r"\s+", " ", str(market.get("name") or "").strip().lower())
        if not any(word in name for word in ("total", "gole", "goals", "bramki", "suma goli")):
            continue
        if any(word in name for word in ("corner", "roż", "kart", "cards", "rzut", "team total", "drużyn")):
            continue
        if any(word in name for word in ("1st half", "first half", "1. połowa", "1 połowa", "pierwsza połowa", "2nd half", "second half", "2. połowa", "2 połowa")):
            continue
        if market.get("status") not in (None, 1, "1", "ACTIVE", "active"):
            continue
        for odd in market.get("odds") or []:
            meta = odd.get("metadata") or {}
            raw_line = meta.get("special_bet_value", odd.get("special_bet_value"))
            try:
                line = float(str(raw_line).replace(",", "."))
                price = float(odd.get("price"))
            except (TypeError, ValueError):
                continue
            labels = " ".join(str(v or "") for v in (
                odd.get("name"), odd.get("label"), meta.get("name"), meta.get("info")
            )).lower()
            if not re.search(r"\b(over|powyżej|powyzej|üst)\b", labels):
                continue
            if odd.get("status") not in (1, "1", "ACTIVE", "active") or odd.get("display") is False:
                continue
            if str(meta.get("offer_state_id", 1)) != "1":
                continue
            if not MIN_ODDS <= price <= MAX_ODDS:
                continue
            results.append({"line": line, "odds": price, "market": market.get("name") or "Total Goals"})
    return results


def superbet_event_details(event_id):
    payload = superbet_api("/v3/pl-PL/events", {"events": str(event_id)})
    events = payload.get("events") or payload.get("data") or []
    if isinstance(events, dict):
        events = list(events.values())
    if len(events) == 1:
        event = events[0]
        returned_id = event.get("event_id") or event.get("id")
        if returned_id is not None and str(returned_id) != str(event_id):
            return None
        return event
    return next((e for e in events if str(e.get("event_id") or e.get("id")) == str(event_id)), None)


def superbet_url(event):
    for obj in (event, event.get("fixture") or {}):
        if not isinstance(obj, dict):
            continue
        for key in ("url", "event_url", "eventUrl", "permalink", "web_url", "webUrl"):
            value = obj.get(key)
            if isinstance(value, str) and value.startswith("https://") and "superbet" in value.lower():
                return value
    return "https://superbet.pl/zaklady-bukmacherskie/live"


def db_connect():
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS live_over_sent(
        pick_key TEXT PRIMARY KEY, event_id TEXT NOT NULL, sent_at TEXT NOT NULL,
        home TEXT NOT NULL, away TEXT NOT NULL, line REAL NOT NULL,
        odds REAL NOT NULL, minute INTEGER NOT NULL)""")
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
        events = live_superbet_events()
        candidates = []
        for listed in events:
            event_id = listed.get("event_id") or listed.get("id")
            if not event_id:
                continue
            minute = event_minute(listed)
            if minute is None or not MIN_MINUTE <= minute <= MAX_MINUTE:
                continue
            score = event_score(listed)
            if score is None:
                logging.info("Pominięto event %s: brak jawnego wyniku LIVE", event_id)
                continue
            home, away = event_teams(listed)
            if not home or not away:
                continue
            detail = superbet_event_details(event_id)
            if not detail or not event_is_still_live(detail):
                continue
            detail_minute = event_minute(detail)
            if detail_minute is not None:
                minute = detail_minute
            if not MIN_MINUTE <= minute <= MAX_MINUTE:
                continue
            detail_score = event_score(detail)
            if detail_score is not None:
                score = detail_score
            for market in exact_live_fulltime_over_markets(detail):
                line = market["line"]
                key = f"{event_id}|FT|OVER|{line:.2f}"
                if conn.execute("SELECT 1 FROM live_over_sent WHERE pick_key=?", (key,)).fetchone():
                    continue
                candidates.append({
                    "event_id": str(event_id), "home": home, "away": away,
                    "minute": minute, "score": score, "line": line,
                    "odds": market["odds"], "url": superbet_url(detail), "key": key,
                })
        # Dla jednego meczu publikuj tylko jeden rynek: najwyższy kurs w zadanym zakresie.
        best_by_event = {}
        for item in candidates:
            current = best_by_event.get(item["event_id"])
            if current is None or item["odds"] > current["odds"]:
                best_by_event[item["event_id"]] = item
        picks = sorted(best_by_event.values(), key=lambda x: (-x["odds"], -x["minute"]))[:MAX_PICKS_PER_RUN]
        logging.info("Superbet LIVE: %d kandydatów, %d wybranych do wysyłki", len(candidates), len(picks))
        for pick in picks:
            # Ostatnia kontrola: pobierz świeże dane i upewnij się, że rynek nadal istnieje.
            fresh = superbet_event_details(pick["event_id"])
            if not fresh or not event_is_still_live(fresh):
                continue
            fresh_minute = event_minute(fresh)
            if fresh_minute is None or not MIN_MINUTE <= fresh_minute <= MAX_MINUTE:
                continue
            fresh_markets = exact_live_fulltime_over_markets(fresh)
            matching = next((m for m in fresh_markets if abs(m["line"] - pick["line"]) < 0.001), None)
            if not matching:
                continue
            fresh_score = event_score(fresh) or pick["score"]
            home, away = event_teams(fresh)
            if not home or not away:
                home, away = pick["home"], pick["away"]
            message = (
                f"🔴 LIVE OVER | {fresh_minute}' | {fresh_score[0]}:{fresh_score[1]}\n"
                f"⚽ {home} – {away}\n"
                f"📌 Typ: OVER {matching['line']:.1f} GOLI — CAŁY MECZ\n"
                f"💹 Kurs Superbet LIVE: {matching['odds']:.2f}\n"
                f"💰 Stawka: {STAKE} zł\n"
                f"🔗 Mecz: {superbet_url(fresh)}"
            )
            if send_telegram(message):
                conn.execute(
                    "INSERT OR IGNORE INTO live_over_sent VALUES(?,?,?,?,?,?,?,?)",
                    (pick["key"], pick["event_id"], datetime.now(TZ).isoformat(),
                     home, away, matching["line"], matching["odds"], fresh_minute),
                )
                conn.commit()
                logging.info("Wysłano LIVE OVER %s - %s | linia %.1f | kurs %.2f",
                             home, away, matching["line"], matching["odds"])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
