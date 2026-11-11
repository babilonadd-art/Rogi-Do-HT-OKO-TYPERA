import os
import re
import sqlite3
import logging
import json
import unicodedata
import math
import difflib
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

SUPERBET_API = "https://production-superbet-offer-pl.freetls.fastly.net"
SPORTSCORE_API = "https://sportscore.com/api/v1"
TZ = ZoneInfo("Europe/Warsaw")
DB = "okotypera_rogi.db"
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")

# LIVE: stałe rynki Over 0.5 i Over 1.5 gola, dopóki dana linia nie jest rozliczona.
# HT: 1–35 minuta, minimalny kurs 1.60. FT: 1–75 minuta, minimalny kurs 1.70.
HT_MIN_MINUTE = 1
HT_MAX_MINUTE = 35
FT_MIN_MINUTE = 1
FT_MAX_MINUTE = 75
HT_MIN_ODDS = 1.60
FT_MIN_ODDS = 1.70
MAX_PICKS_PER_DAY = 10
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
    """Zwraca aktywne Over 0.5/1.5, jeśli linia nie jest jeszcze rozliczona."""
    if phase not in ("HT", "FT") or not event_is_still_live(event):
        return []
    min_odds = HT_MIN_ODDS if phase == "HT" else FT_MIN_ODDS
    available_lines = (0.5, 1.5) if current_total < 2 else ()
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
            # Dopuszczamy wyłącznie wskazane linie; rynek musi być jeszcze nierozliczony.
            if not any(abs(line - required) <= 0.001 for required in available_lines):
                continue
            if current_total >= line + 0.5:
                continue
            if odd.get("status") not in (None, 1, 2, "1", "2", "ACTIVE", "active", "OPEN", "open", "AVAILABLE", "available", "TRADING", "trading", True) or odd.get("display") is False:
                continue
            if str(meta.get("offer_state_id", 1)) not in ("1", "2"):
                continue
            if price < min_odds:
                continue
            results.append({"line": line, "odds": price, "market": name})
    results.sort(key=lambda item: item["odds"])
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
    """Zwraca wyłącznie zweryfikowany adres konkretnego wydarzenia z payloadu Superbet."""
    url_keys = {"url", "event_url", "eventurl", "permalink", "web_url", "weburl", "event_link"}
    found = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() in url_keys and isinstance(value, str):
                    candidate = value.strip()
                    if candidate.startswith("https://") and "superbet.pl/" in candidate.lower():
                        found.append(candidate)
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(event)
    return found[0] if found else None


def _normal_team_name(value):
    if isinstance(value, dict):
        value = value.get("name") or value.get("team_name") or value.get("short_name") or ""
    value = unicodedata.normalize("NFKD", str(value or "").lower())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"\b(football club|fc|cf|sc|afc|club|team)\b", " ", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def _fixture_team(fixture, side):
    keys = ("home", "home_team", "team_home", "homeTeam") if side == "home" else (
        "away", "away_team", "team_away", "awayTeam"
    )
    for key in keys:
        if fixture.get(key):
            return fixture[key]
    teams = fixture.get("teams")
    if isinstance(teams, dict) and teams.get(side):
        return teams[side]
    return ""


def _fixture_list(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("fixtures", "matches", "data", "results", "response"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _fixture_list(value)
            if nested:
                return nested
    return []


def sportscore_live_fixtures():
    """Darmowy feed LIVE SportScore; brak danych oznacza brak typów, nie fallback na kurs."""
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        response = http.get(
            SPORTSCORE_API + "/fixtures/",
            params={"sport": "football", "date": today, "status": "live",
                    "limit": 200, "src": "rogidotypera-live"},
            timeout=18,
        )
        response.raise_for_status()
        fixtures = _fixture_list(response.json())
        live = []
        for fixture in fixtures:
            if not isinstance(fixture, dict):
                continue
            status = str(fixture.get("status") or fixture.get("state") or "").lower()
            if status and not any(token in status for token in ("live", "inplay", "in_play", "in progress")):
                continue
            if _fixture_team(fixture, "home") and _fixture_team(fixture, "away"):
                live.append(fixture)
        logging.info("SportScore: %d spotkań LIVE w feedzie statystyk", len(live))
        return live
    except Exception as exc:
        logging.warning("SportScore LIVE niedostępny (%s); bot nie wyśle typów bez statystyk", exc)
        return []


def match_sportscore_fixture(home, away, fixtures):
    target_home, target_away = _normal_team_name(home), _normal_team_name(away)
    if not target_home or not target_away:
        return None
    best, best_score = None, 0.0
    for fixture in fixtures:
        source_home = _normal_team_name(_fixture_team(fixture, "home"))
        source_away = _normal_team_name(_fixture_team(fixture, "away"))
        if not source_home or not source_away:
            continue
        direct = (difflib.SequenceMatcher(None, target_home, source_home).ratio()
                  + difflib.SequenceMatcher(None, target_away, source_away).ratio()) / 2
        reverse = (difflib.SequenceMatcher(None, target_home, source_away).ratio()
                   + difflib.SequenceMatcher(None, target_away, source_home).ratio()) / 2
        score = max(direct, reverse)
        if score > best_score:
            best, best_score = fixture, score
    return best if best_score >= 0.90 else None


def _stat_number(value):
    if isinstance(value, dict):
        value = value.get("value", value.get("total", value.get("current")))
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def extract_sportscore_stats(payload):
    """Zbiera sumy obu drużyn; zwraca None, gdy źródło nie podało kluczowych statystyk."""
    aliases = {
        "shots": {"shots", "total shots", "total_shots", "shots total", "strzaly", "strzały"},
        "shots_on_target": {"shots on target", "shots_on_target", "shots on goal",
                            "shots_on_goal", "celne strzaly", "strzały celne"},
        "dangerous_attacks": {"dangerous attacks", "dangerous_attacks", "dangerous attack",
                              "grozne ataki", "groźne ataki"},
        "corners": {"corners", "corner kicks", "corner_kicks", "rogi", "rzuty rozne", "rzuty rożne"},
    }
    totals = {}
    def walk(node):
        if isinstance(node, dict):
            name = node.get("name") or node.get("label") or node.get("type") or node.get("stat")
            if name is not None:
                normalized = str(name).strip().lower()
                normalized = re.sub(r"[_\-]+", " ", normalized)
                normalized = re.sub(r"\s+", " ", normalized)
                category = next((key for key, names in aliases.items()
                                if normalized in {re.sub(r"[_\-]+", " ", n).lower() for n in names}), None)
                if category:
                    home = _stat_number(node.get("home", node.get("home_value")))
                    away = _stat_number(node.get("away", node.get("away_value")))
                    if home is not None and away is not None:
                        totals[category] = int(home + away)
            for key, value in node.items():
                if isinstance(value, dict):
                    # Format mapowy: {"shots": {"home": 7, "away": 4}}
                    normalized_key = re.sub(r"[_\-]+", " ", str(key)).strip().lower()
                    category = next((cat for cat, names in aliases.items()
                                     if normalized_key in {re.sub(r"[_\-]+", " ", n).lower() for n in names}), None)
                    if category and isinstance(value, dict):
                        home = _stat_number(value.get("home", value.get("home_value")))
                        away = _stat_number(value.get("away", value.get("away_value")))
                        if home is not None and away is not None:
                            totals[category] = int(home + away)
                    walk(value)
                elif isinstance(value, list):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(payload)
    if "shots" not in totals or "shots_on_target" not in totals:
        return None
    if "dangerous_attacks" not in totals and "corners" not in totals:
        return None
    return totals


def sportscore_fixture_slug(fixture):
    slug = fixture.get("slug") or fixture.get("match_slug") or fixture.get("url_slug")
    if slug:
        return str(slug).strip("/")
    url = fixture.get("url") or fixture.get("match_url") or ""
    parts = [part for part in urlparse(str(url)).path.split("/") if part]
    if "match" in parts:
        index = parts.index("match")
        if index + 1 < len(parts):
            return parts[index + 1]
    return None


def extract_sportscore_text_stats(text):
    """Awaryjnie czyta statystyki z publicznej strony meczu SportScore."""
    flat = re.sub(r"\s+", " ", str(text or " "))
    labels = {
        "shots": ("total shots", "shots"),
        "shots_on_target": ("shots on target", "shots on goal"),
        "dangerous_attacks": ("dangerous attacks",),
        "corners": ("corner kicks", "corners"),
    }
    totals = {}
    for category, names in labels.items():
        for label in names:
            escaped = re.escape(label)
            match = re.search(rf"{escaped}\s*[:\-]?\s*(\d+)\s+(\d+)", flat, re.I)
            if not match:
                match = re.search(rf"(\d+)\s+{escaped}\s+(\d+)", flat, re.I)
            if match:
                totals[category] = int(match.group(1)) + int(match.group(2))
                break
    if "shots" not in totals or "shots_on_target" not in totals:
        return None
    if "dangerous_attacks" not in totals and "corners" not in totals:
        return None
    return totals


def sportscore_page_stats(fixture):
    path = str(fixture.get("url") or fixture.get("match_url") or "")
    if not path:
        return None
    url = path if path.startswith("https://") else "https://sportscore.com/" + path.lstrip("/")
    try:
        response = http.get(url, timeout=18)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        text = soup.get_text(" ", strip=True)
        stats = extract_sportscore_text_stats(text)
        if stats:
            logging.info("SportScore: statystyki odczytane ze strony meczu %s: %s", url, stats)
        return stats
    except Exception as exc:
        logging.warning("SportScore: błąd odczytu strony statystyk %s: %s", url, exc)
        return None


def sportscore_match_stats(fixture):
    slug = sportscore_fixture_slug(fixture)
    if not slug:
        return None
    try:
        response = http.get(
            SPORTSCORE_API + "/match/",
            params={"sport": "football", "slug": str(slug), "src": "rogidotypera-live"},
            timeout=18,
        )
        response.raise_for_status()
        payload = response.json()
        stats = extract_sportscore_stats(payload)
        if not stats:
            stats = sportscore_page_stats(fixture)
        if not stats:
            match_obj = payload.get("match") if isinstance(payload, dict) else {}
            logging.info("SportScore: brak kompletnych statystyk dla %s; top_keys=%s match_keys=%s",
                         slug, sorted(payload.keys()) if isinstance(payload, dict) else type(payload).__name__,
                         sorted(match_obj.keys()) if isinstance(match_obj, dict) else type(match_obj).__name__)
            if isinstance(match_obj, dict):
                logging.info("SportScore pola stats w meczu: %s", json.dumps(match_obj.get("stats"), ensure_ascii=False)[:3500])
        return stats
    except Exception as exc:
        logging.warning("SportScore: błąd pobrania statystyk %s: %s", slug, exc)
        return None


def stats_confirm_over(stats, minute, line):
    """Konserwatywny filtr tempa. Brak pełnych statystyk zawsze odrzuca typ."""
    if not stats or minute is None:
        return False
    shots = stats["shots"]
    shots_on_target = stats["shots_on_target"]
    min_shots = max(3, math.ceil(minute * 0.20))
    min_on_target = max(1, math.ceil(minute * 0.06))
    min_pressure = max(10, math.ceil(minute * 0.65))
    enough_pressure = (
        stats.get("dangerous_attacks", 0) >= min_pressure
        or stats.get("corners", 0) >= max(1, math.ceil(minute / 25))
    )
    if line >= 1.5:
        min_shots += 2
        min_on_target += 1
    return shots >= min_shots and shots_on_target >= min_on_target and enough_pressure


def db_connect():
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS live_over_sent(
        pick_key TEXT PRIMARY KEY, event_id TEXT NOT NULL, sent_at TEXT NOT NULL,
        home TEXT NOT NULL, away TEXT NOT NULL, line REAL NOT NULL,
        odds REAL NOT NULL, minute INTEGER NOT NULL)""")
    conn.commit()
    return conn


def daily_sent_count(conn, day=None):
    """Liczba typów LIVE Over wysłanych w bieżącym dniu w strefie Europe/Warsaw."""
    day = day or datetime.now(TZ).date().isoformat()
    row = conn.execute(
        "SELECT COUNT(*) FROM live_over_sent WHERE substr(sent_at, 1, 10)=?",
        (day,),
    ).fetchone()
    return int(row[0] or 0)


def send_telegram(message, event_url=None):
    if not TOKEN or not CHAT:
        logging.error("Brak TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        return False
    data = {"chat_id": CHAT, "text": message, "disable_web_page_preview": True}
    if event_url:
        data["reply_markup"] = json.dumps({
            "inline_keyboard": [[{"text": "⚽ OTWÓRZ TEN MECZ", "url": event_url}]]
        }, ensure_ascii=False)
    response = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data=data,
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
        sent_today = daily_sent_count(conn)
        remaining_today = max(0, MAX_PICKS_PER_DAY - sent_today)
        if remaining_today == 0:
            logging.info("Osiągnięto dzienny limit %d typów LIVE Over.", MAX_PICKS_PER_DAY)
            return
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
            # HT i FT są niezależnymi rynkami; dopuszczamy Over 0.5 i Over 1.5.
            phases = []
            if HT_MIN_MINUTE <= minute <= HT_MAX_MINUTE:
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
        # Twarda bramka statystyczna: bez potwierdzonych statystyk LIVE SportScore nie publikujemy typu.
        stats_fixtures = sportscore_live_fixtures()
        stats_cache = {}
        # Kontrola źródła: raz na skan pobierz próbkę statystyk z jednego meczu LIVE.
        if stats_fixtures:
            sample_fixture = stats_fixtures[0]
            sample_slug = sportscore_fixture_slug(sample_fixture)
            if sample_slug:
                stats_cache[sample_slug] = sportscore_match_stats(sample_fixture)
                logging.info("SportScore kontrola statystyk LIVE: slug=%s stats=%s", sample_slug, stats_cache[sample_slug])
            else:
                logging.warning("SportScore mecz LIVE bez adresu szczegółów; pola=%s próbka=%s", sorted(sample_fixture.keys()), sample_fixture)
        candidate_count_before_stats = len(candidates)
        verified_candidates = []
        for item in candidates:
            fixture = match_sportscore_fixture(item["home"], item["away"], stats_fixtures)
            if not fixture:
                logging.info("POMINIĘTO %s - %s: brak dopasowania w SportScore", item["home"], item["away"])
                continue
            slug = sportscore_fixture_slug(fixture)
            if slug not in stats_cache:
                stats_cache[slug] = sportscore_match_stats(fixture)
            stats = stats_cache[slug]
            if not stats or not stats_confirm_over(stats, item["minute"], item["line"]):
                logging.info("POMINIĘTO %s - %s: statystyki nie potwierdzają tempa (stats=%s minute=%s line=%.1f)",
                             item["home"], item["away"], stats, item["minute"], item["line"])
                continue
            item["stats"] = stats
            verified_candidates.append(item)
        candidates = verified_candidates
        logging.info("Statystycznie potwierdzone LIVE Over: %d/%d", len(candidates), candidate_count_before_stats)

        # Jedna selekcja na mecz i fazę; preferuj kurs bliższy środkowi zakresu, nie najwyższy za wszelką cenę.
        best = {}
        for item in candidates:
            group_key = (item["event_id"], item["phase"])
            current = best.get(group_key)
            if current is None or abs(item["odds"] - 2.35) < abs(current["odds"] - 2.35):
                best[group_key] = item
        picks = sorted(best.values(), key=lambda x: (x["phase"] != "HT", abs(x["odds"] - 2.10)))[:min(MAX_PICKS_PER_RUN, remaining_today)]
        logging.info("Superbet LIVE: %d kandydatów, %d wybranych; wysłano dziś %d/%d",
                     len(candidates), len(picks), sent_today, MAX_PICKS_PER_DAY)
        for pick in picks:
            if sent_today >= MAX_PICKS_PER_DAY:
                logging.info("Osiągnięto dzienny limit %d typów LIVE Over.", MAX_PICKS_PER_DAY)
                break
            fresh = superbet_event_details(pick["event_id"])
            if not fresh or not event_is_still_live(fresh):
                continue
            fresh_minute = event_minute(fresh)
            fresh_score = event_score(fresh)
            if fresh_minute is None or fresh_score is None:
                continue
            if pick["phase"] == "HT" and not HT_MIN_MINUTE <= fresh_minute <= HT_MAX_MINUTE:
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
                f"🔴 LIVE OVER {matching['line']:.1f} | Typ {sent_today + 1}/{MAX_PICKS_PER_DAY} | {fresh_minute}' | {fresh_score[0]}:{fresh_score[1]}\n"
                f"⚽ {home} – {away}\n"
                f"📌 Typ: OVER {matching['line']:.1f} GOLI — {phase_label}\n"
                f"🏷 Rynek: {matching['market']}\n"
                f"💹 Kurs Superbet LIVE (odświeżony przed wysyłką): {matching['odds']:.2f}\n"
                f"📊 Statystyki LIVE: strzały {pick['stats']['shots']}, celne {pick['stats']['shots_on_target']}, "
                f"groźne ataki {pick['stats'].get('dangerous_attacks', 'brak')}, rożne {pick['stats'].get('corners', 'brak')}\n"
                f"📡 Źródło statystyk: https://sportscore.com/\n"
                f"💰 Stawka: {STAKE} zł"
            )
            event_url = superbet_url(fresh)
            if not event_url:
                logging.warning("Brak bezpośredniego URL meczu Superbet dla %s - %s (event_id=%s); wysyłam typ bez fałszywego linku",
                                home, away, pick["event_id"])
            if send_telegram(message, event_url=event_url):
                conn.execute(
                    "INSERT OR IGNORE INTO live_over_sent VALUES(?,?,?,?,?,?,?,?)",
                    (pick["key"], pick["event_id"], datetime.now(TZ).isoformat(),
                     home, away, matching["line"], matching["odds"], fresh_minute),
                )
                conn.commit()
                sent_today += 1
                logging.info("Wysłano LIVE %s %s - %s | linia %.1f | kurs %.2f | limit dzienny %d/%d",
                             pick["phase"], home, away, matching["line"], matching["odds"],
                             sent_today, MAX_PICKS_PER_DAY)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
