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

# LIVE: HT do 35. minuty (maks. linia Over 1.5), FT od 46. do 75. minuty (maks. linia Over 2.5).
HT_MAX_MINUTE = 35
FT_MIN_MINUTE = 46
FT_MAX_MINUTE = 75
HT_MAX_LINE = 1.5
FT_MAX_LINE = 2.5
# Luźniejsze widełki, ale nadal wymagamy prawdziwego, aktywnego kursu Superbet.
MIN_ODDS = 1.05
MAX_ODDS = 10.00
MAX_PICKS_PER_RUN = 10
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


def _status_values(event):
    values = []
    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                key_l = str(key).lower()
                if key_l in ("status", "event_status", "inplay_status", "match_status") and value is not None:
                    values.append(str(value).upper())
                if key_l in ("is_live", "islive", "inplay", "is_inplay", "isinplay") and isinstance(value, bool):
                    values.append("LIVE" if value else "NOT_LIVE")
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(event)
    return values


def event_is_still_live(event):
    values = _status_values(event)
    rejected = {"NOT_STARTED", "NOT_LIVE", "PREMATCH", "PRE_MATCH", "FINISHED", "ENDED",
                "CANCELLED", "POSTPONED", "ABANDONED", "SUSPENDED"}
    live_tokens = ("LIVE", "INPLAY", "IN_PLAY", "FIRST_HALF", "SECOND_HALF",
                   "HALFTIME", "1H", "2H", "STARTED", "IN PROGRESS", "IN_PROGRESS")
    return any(v not in rejected and any(token in v for token in live_tokens) for v in values)


def live_superbet_events():
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    payload = superbet_api("/v3/pl-PL/events", {
        "startDate": start.isoformat().replace("+00:00", "Z"),
        "endDate": end.isoformat().replace("+00:00", "Z"),
    })
    events = payload.get("events") or []
    football = [e for e in events if str((e.get("fixture") or {}).get("sport_id")) == "5"]
    live = [e for e in football if event_is_still_live(e)]
    logging.info("Superbet: %d wydarzeń LIVE piłki nożnej z %d wydarzeń piłkarskich (%d wszystkich)",
                 len(live), len(football), len(events))
    if live:
        for sample in live[:3]:
            logging.info("LIVE SAMPLE id=%s status=%s minute=%s score=%s fixture_keys=%s metadata_keys=%s",
                         sample.get("event_id") or sample.get("id"), _status_values(sample)[:8],
                         event_minute(sample), event_score(sample),
                         sorted((sample.get("fixture") or {}).keys()),
                         sorted((sample.get("inplay_stats_metadata") or {}).keys()))
    if not live and football:
        # Diagnostyka bez wypisywania całych payloadów ani danych wrażliwych.
        samples = []
        for event in football:
            vals = _status_values(event)
            if vals:
                samples.append(vals[:8])
            if len(samples) >= 5:
                break
        logging.warning("Brak rozpoznanych LIVE. Przykładowe statusy Superbet: %s", samples or "brak pól statusu")
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
        "elapsed_minutes", "minutes", "minute_number", "timer_minute", "inplay_minute", "currentminute", "elapsedtime_minute", "period_minute", "event_minute",
    }
    clock_keys = {"clock", "timer", "match_time", "elapsed_time", "game_time"}
    found = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                k = str(key).lower()
                if k in keys and value is not None:
                    raw_value = value
                    if isinstance(raw_value, dict):
                        raw_value = raw_value.get("value", raw_value.get("current", raw_value.get("minute")))
                    match = re.search(r"\b(\d{1,3})\b", str(raw_value))
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
                if isinstance(value, dict):
                    lower_keys = {str(k).lower(): k for k in value}
                    if str(key).lower() in ("score", "scores", "scoreboard", "current_score"):
                        home_key = next((lower_keys[k] for k in ("home", "home_score", "homescore", "home_team_score", "team1", "participant1") if k in lower_keys), None)
                        away_key = next((lower_keys[k] for k in ("away", "away_score", "awayscore", "away_team_score", "team2", "participant2") if k in lower_keys), None)
                        home = number(value[home_key]) if home_key is not None else None
                        away = number(value[away_key]) if away_key is not None else None
                        if home is not None and away is not None:
                            found.append((home, away))
                    # Superbet czasem podaje home_score/away_score bez wrappera score.
                    home_key = next((lower_keys[k] for k in ("home_score", "homescore", "home_team_score") if k in lower_keys), None)
                    away_key = next((lower_keys[k] for k in ("away_score", "awayscore", "away_team_score") if k in lower_keys), None)
                    if home_key is not None and away_key is not None:
                        home, away = number(value[home_key]), number(value[away_key])
                        if home is not None and away is not None:
                            found.append((home, away))
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(event)
    return found[0] if found else None


def market_phase(name):
    n = re.sub(r"\s+", " ", str(name or "").lower())
    # Najpierw wyklucz drugą połowę — nie wolno jej pomylić z rynkiem FT.
    second_half_terms = ("2nd half", "second half", "2. połowa", "2 połowa",
                         "druga połowa", "ii połowa", "2 poł.", "2 poł")
    if any(term in n for term in second_half_terms):
        return "2H"
    ht_terms = ("1st half", "first half", "1. połowa", "1 połowa", "pierwsza połowa",
                "i połowa", "1 poł.", "1 poł")
    ft_terms = ("full time", "full-time", "cały mecz", "caly mecz", "wynik meczu",
                "pełny mecz", "pelny mecz", "90 minut", "mecz - liczba goli")
    if any(term in n for term in ht_terms):
        return "HT"
    if any(term in n for term in ft_terms):
        return "FT"
    # Dopuszczamy tylko jednoznaczne nazwy rynku całego meczu.
    # Nie używamy szerokiego "zawiera gole/goals", bo łapie też rynki
    # drużynowe, fragmenty meczu i inne linie z innym kursem.
    generic_ft_names = {
        "total goals", "total number of goals", "goals total",
        "liczba goli", "gole", "gole w meczu", "suma goli",
        "liczba bramek", "suma bramek", "wynik - liczba goli",
    }
    if n.strip(" -:") in generic_ft_names:
        return "FT"
    return None


def exact_live_over_markets(event, phase, current_total):
    """Pobiera aktywną linię na następnego gola, z limitem linii dla danej połowy."""
    if not event_is_still_live(event):
        return []
    max_line = HT_MAX_LINE if phase == "HT" else FT_MAX_LINE
    min_line = current_total + 0.5
    if min_line > max_line:
        return []
    results = []
    for market in event.get("markets") or []:
        name = str(market.get("name") or "")
        if market_phase(name) != phase:
            continue
        low_name = name.lower()
        if any(word in low_name for word in ("corner", "roż", "kart", "cards", "team total", "drużyn")):
            continue
        if market.get("status") not in (None, 1, 2, "1", "2", "ACTIVE", "active", "OPEN", "open", "AVAILABLE", "available", True):
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
            # Gramy wyłącznie najbliższą linię: przy 0:0 Over 0.5,
            # przy 1 golu Over 1.5 itd. Nie przeskakujemy od razu na wyższą linię.
            if abs(line - min_line) > 0.001 or line > max_line + 0.001:
                continue
            if odd.get("status") not in (None, 1, 2, "1", "2", "ACTIVE", "active", "OPEN", "open", "AVAILABLE", "available", "TRADING", "trading", True) or odd.get("display") is False:
                continue
            if str(meta.get("offer_state_id", 1)) not in ("1", "2"):
                continue
            if not MIN_ODDS <= price <= MAX_ODDS:
                continue
            results.append({"line": line, "odds": price, "market": name})
    results.sort(key=lambda item: abs(item["odds"] - 1.80))
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
            # Lista LIVE często nie zawiera wyniku ani zegara — pobierz pełne dane meczu.
            detail = superbet_event_details(event_id)
            if not detail or not event_is_still_live(detail):
                continue
            minute = event_minute(detail)
            score = event_score(detail)
            if minute is None or score is None:
                continue
            home, away = event_teams(detail)
            if not home or not away:
                home, away = event_teams(listed)
            if not home or not away:
                continue
            # HT: wyłącznie 1–35'. FT: wyłącznie 46–75'.
            phases = []
            if 1 <= minute <= HT_MAX_MINUTE:
                phases.append("HT")
            if FT_MIN_MINUTE <= minute <= FT_MAX_MINUTE:
                phases.append("FT")
            if not phases:
                continue
            # Po ponownym pobraniu zawsze sprawdzamy granice minuty.
            phases = []
            if 1 <= minute <= HT_MAX_MINUTE:
                phases.append("HT")
            if FT_MIN_MINUTE <= minute <= FT_MAX_MINUTE:
                phases.append("FT")
            if not phases:
                continue
            market_names = [str(m.get("name") or m.get("market_name") or "") for m in (detail.get("markets") or [])]
            logging.info("LIVE DIAG id=%s %s-%s minute=%s score=%s phases=%s detail_keys=%s markets=%d names=%s",
                         event_id, home, away, minute, score, phases, sorted(detail.keys()),
                         len(market_names), market_names[:30])
            total = sum(score)
            for phase in phases:
                for market in exact_live_over_markets(detail, phase, total):
                    key = f"{event_id}|{phase}|OVER|{market['line']:.2f}"
                    if conn.execute("SELECT 1 FROM live_over_sent WHERE pick_key=?", (key,)).fetchone():
                        continue
                    candidates.append({
                        "event_id": str(event_id), "phase": phase, "home": home, "away": away,
                        "minute": minute, "score": score, "line": market["line"],
                        "odds": market["odds"], "url": superbet_url(detail), "key": key,
                    })
        # Jedna selekcja na mecz i fazę; preferuj kurs bliższy środkowi zakresu, nie najwyższy za wszelką cenę.
        best = {}
        for item in candidates:
            group_key = (item["event_id"], item["phase"])
            current = best.get(group_key)
            if current is None or abs(item["odds"] - 2.35) < abs(current["odds"] - 2.35):
                best[group_key] = item
        picks = sorted(best.values(), key=lambda x: (x["phase"] != "HT", abs(x["odds"] - 2.35)))[:MAX_PICKS_PER_RUN]
        logging.info("Superbet LIVE: %d kandydatów, %d wybranych do wysyłki", len(candidates), len(picks))
        for pick in picks:
            fresh = superbet_event_details(pick["event_id"])
            if not fresh or not event_is_still_live(fresh):
                continue
            fresh_minute = event_minute(fresh)
            fresh_score = event_score(fresh)
            if fresh_minute is None or fresh_score is None:
                continue
            if pick["phase"] == "HT" and not 1 <= fresh_minute <= HT_MAX_MINUTE:
                continue
            if pick["phase"] == "FT" and not FT_MIN_MINUTE <= fresh_minute <= FT_MAX_MINUTE:
                continue
            fresh_markets = exact_live_over_markets(fresh, pick["phase"], sum(fresh_score))
            matching = next((m for m in fresh_markets if abs(m["line"] - pick["line"]) < 0.001), None)
            if not matching:
                continue
            home, away = event_teams(fresh)
            if not home or not away:
                home, away = pick["home"], pick["away"]
            phase_label = "DO PRZERWY (HT)" if pick["phase"] == "HT" else "DO KOŃCA MECZU (FT)"
            message = (
                f"🔴 LIVE LATE OVER | {fresh_minute}' | {fresh_score[0]}:{fresh_score[1]}\n"
                f"⚽ {home} – {away}\n"
                f"📌 Typ: OVER {matching['line']:.1f} GOLI — {phase_label}\n"
                f"🏷 Rynek: {matching['market']}\n"
                f"💹 Kurs Superbet LIVE (odświeżony przed wysyłką): {matching['odds']:.2f}\n"
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
                logging.info("Wysłano LIVE %s %s - %s | linia %.1f | kurs %.2f",
                             pick["phase"], home, away, matching["line"], matching["odds"])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
