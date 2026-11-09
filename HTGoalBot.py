import os
import re
import logging
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

import requests
import LiveOverBot as sb

API = "https://api.pitchapi.dev"
API_KEY = os.getenv("PITCHAPI_API_KEY")
TZ = ZoneInfo("Europe/Warsaw")
DB = "okotypera_rogi.db"
MINUTE_MIN, MINUTE_MAX = 22, 26
ODDS_MIN, ODDS_MAX = 1.80, 2.00
MOMENTUM_THRESHOLD = 20
MAX_PICKS = 10

log = logging.getLogger("ht_goal_bot")
session = requests.Session()
session.headers.update({"X-API-KEY": API_KEY or "", "Accept": "application/json"})


def pitch_get(path, params=None):
    if not API_KEY:
        raise RuntimeError("Brak sekretu PITCHAPI_API_KEY")
    response = session.get(API + path, params=params, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(str(payload["error"]))
    return payload.get("data") or {}


def normalize_team(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(c for c in value if not unicodedata.combining(c)).lower()
    value = re.sub(r"\b(football club|fc|cf|afc|sc|fk|sk|club|the)\b", " ", value)
    return re.sub(r"[^a-z0-9]", "", value)


def team_similarity(a, b):
    na, nb = normalize_team(a), normalize_team(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def find_pitch_match(fixtures, home, away):
    ranked = []
    for match in fixtures:
        p_home = (match.get("home_team") or {}).get("name", "")
        p_away = (match.get("away_team") or {}).get("name", "")
        hs, aws = team_similarity(home, p_home), team_similarity(away, p_away)
        if hs >= 0.90 and aws >= 0.90:
            ranked.append((min(hs, aws), match))
    ranked.sort(key=lambda x: x[0], reverse=True)
    if not ranked:
        return None
    if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 0.015:
        return None
    return ranked[0][1]


def unwrap_shots(payload):
    shots = []
    for period in payload.get("periods") or []:
        if str(period.get("period", "")).lower() in ("firsthalf", "1h", "first_half"):
            shots.extend(period.get("shots") or [])
    if not shots:
        shots = [s for p in (payload.get("periods") or []) for s in (p.get("shots") or [])
                 if int(p.get("period_number", 1) or 1) == 1]
    return [s for s in shots if int(s.get("minute", 999) or 999) <= 20]


def momentum_is_strong(points):
    selected = {}
    raw_values = []
    for point in points or []:
        try:
            minute = float(point.get("minute"))
            value = float(point.get("value"))
        except (TypeError, ValueError):
            continue
        if 16 <= minute <= 20:
            selected[int(minute)] = value
            raw_values.append(abs(value))
    if not selected:
        return False
    # Dokumentacja podaje zakres -100..100, ale przykładowe payloady są
    # znormalizowane do -1..1. Ujednolicamy oba warianty do punktów.
    scale = 100 if max(raw_values) <= 1.5 else 1
    strong_minutes = {m for m, v in selected.items() if abs(v) * scale >= MOMENTUM_THRESHOLD}
    return any(all(m + offset in strong_minutes for offset in range(3))
               for m in range(16, 19))


def _numeric(value):
    match = re.search(r"-?\d+", str(value or ""))
    return int(match.group()) if match else None


def corners_from_live(live):
    stats = live.get("stats") or []
    for stat in stats:
        key = re.sub(r"[^a-z]", "", str(stat.get("key") or stat.get("title") or "").lower())
        if key in {"corners", "cornerkicks", "totalcorners", "cornerswon"}:
            home, away = _numeric(stat.get("home")), _numeric(stat.get("away"))
            if home is not None and away is not None:
                return home + away
    return None


def corners_from_match_stats(payload):
    for period in payload.get("periods") or []:
        if str(period.get("period", "")).lower() not in ("firsthalf", "1h", "first_half"):
            continue
        for group in period.get("groups") or []:
            for item in group.get("items") or []:
                key = re.sub(r"[^a-z]", "", str(item.get("key") or item.get("title") or "").lower())
                if key in {"corners", "cornerkicks", "totalcorners", "cornerswon"}:
                    home, away = _numeric(item.get("home")), _numeric(item.get("away"))
                    if home is not None and away is not None:
                        return home + away
    return None


def first_goal_minute(events):
    goals = []
    for event in events or []:
        kind = str(event.get("event_type") or event.get("type") or "").lower()
        if "goal" not in kind:
            continue
        try:
            goals.append(int(event.get("minute")))
        except (TypeError, ValueError):
            continue
    return min(goals) if goals else None


def red_card_to_leader(events, score):
    # Bez kursów przedmeczowych nie da się wiarygodnie wskazać faworyta.
    # Odrzucamy więc bezpieczniej każdy przypadek czerwonej kartki dla prowadzącej drużyny.
    leader = "home" if score[0] > score[1] else "away" if score[1] > score[0] else None
    if not leader:
        return False
    for event in events or []:
        kind = str(event.get("event_type") or event.get("type") or "").lower()
        if "red" not in kind:
            continue
        side = event.get("side")
        if side == leader:
            return True
        team_id = event.get("team_id")
        # Team ID is checked against the IDs from live snapshot in the caller.
        if team_id and event.get("_leader_team_id") == team_id:
            return True
    return False


def exact_ht_market(event, line):
    matches = []
    for market in event.get("markets") or []:
        name = str(market.get("name") or "")
        if sb.market_phase(name) != "HT":
            continue
        if any(word in name.lower() for word in ("corner", "roż", "kart", "cards", "team total", "drużyn")):
            continue
        if market.get("status") not in (None, 1, 2, "1", "2", "ACTIVE", "active", "OPEN", "open", "AVAILABLE", "available", True):
            continue
        for odd in market.get("odds") or []:
            meta = odd.get("metadata") or {}
            try:
                current_line = float(str(meta.get("special_bet_value", odd.get("special_bet_value"))).replace(",", "."))
                price = float(odd.get("price"))
            except (TypeError, ValueError):
                continue
            labels = " ".join(str(v or "") for v in (odd.get("name"), odd.get("label"), meta.get("name"), meta.get("info"))).lower()
            if not re.search(r"\b(over|powyżej|powyzej)\b", labels):
                continue
            if abs(current_line - line) > 0.001 or not ODDS_MIN <= price <= ODDS_MAX:
                continue
            if odd.get("status") not in (None, 1, 2, "1", "2", "ACTIVE", "active", "OPEN", "open", "AVAILABLE", "available", "TRADING", "trading", True) or odd.get("display") is False:
                continue
            matches.append({"line": current_line, "odds": price, "market": name})
    return sorted(matches, key=lambda x: x["odds"])


def db_seen(event_id):
    conn = sb.db_connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS ht_goal_sent(
            event_id TEXT PRIMARY KEY, sent_at TEXT NOT NULL)""")
        conn.commit()
        return conn.execute("SELECT 1 FROM ht_goal_sent WHERE event_id=?", (str(event_id),)).fetchone() is not None
    finally:
        conn.close()


def db_mark_sent(event_id):
    conn = sb.db_connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS ht_goal_sent(
            event_id TEXT PRIMARY KEY, sent_at TEXT NOT NULL)""")
        conn.execute("INSERT OR IGNORE INTO ht_goal_sent VALUES(?,?)",
                     (str(event_id), datetime.now(TZ).isoformat()))
        conn.commit()
    finally:
        conn.close()


def qualifies(match_id, current_score):
    live = pitch_get(f"/v1/matches/{match_id}/live")
    if not live.get("live"):
        return None
    if live.get("age_seconds") is not None and int(live.get("age_seconds") or 0) > 120:
        return None
    clock = live.get("clock") or {}
    minute = int(clock.get("minute") or 0)
    if not MINUTE_MIN <= minute <= MINUTE_MAX:
        return None
    pitch_score = (int(live.get("score_home") or 0), int(live.get("score_away") or 0))
    if pitch_score != tuple(current_score):
        return None

    events = live.get("events") or []
    goal_min = first_goal_minute(events)
    if pitch_score == (0, 0):
        target_line = 0.5
    elif pitch_score in ((1, 0), (0, 1)) and goal_min is not None and goal_min < 10:
        target_line = 1.5
    else:
        return None

    home_id = (live.get("home_team") or {}).get("id")
    away_id = (live.get("away_team") or {}).get("id")
    leader_id = home_id if pitch_score[0] > pitch_score[1] else away_id if pitch_score[1] > pitch_score[0] else None
    adjusted_events = []
    for event in events:
        copy = dict(event)
        if leader_id:
            copy["_leader_team_id"] = leader_id
        adjusted_events.append(copy)
    if red_card_to_leader(adjusted_events, pitch_score):
        return None

    shots_data = pitch_get(f"/v1/matches/{match_id}/shots")
    shots = unwrap_shots(shots_data)
    total_shots = len(shots)
    on_target = sum(1 for shot in shots if shot.get("is_on_target") is True)
    if total_shots < 5 or on_target < 2:
        return None

    corners = corners_from_live(live)
    if corners is None:
        try:
            corners = corners_from_match_stats(pitch_get(f"/v1/matches/{match_id}/stats"))
        except Exception as exc:
            log.info("Nie udało się pobrać statystyk rożnych: %s", exc)
    if corners is None or corners < 2:
        return None

    momentum_data = pitch_get(f"/v1/matches/{match_id}/momentum")
    if not momentum_is_strong(momentum_data.get("points") or []):
        return None

    return {
        "minute": minute, "score": pitch_score, "line": target_line,
        "shots": total_shots, "on_target": on_target, "corners": corners,
        "momentum": True, "goal_minute": goal_min,
    }


def main():
    if not API_KEY:
        log.error("Brak PITCHAPI_API_KEY — nie uruchamiam strategii HT.")
        return
    now = datetime.now(TZ)
    fixtures_data = pitch_get(f"/v1/date/{now.date().isoformat()}", params={"status": "all"})
    fixtures = fixtures_data.get("matches") or []
    if not fixtures:
        log.info("PitchAPI: brak spotkań w terminarzu na dziś.")
        return

    listed_events = sb.live_superbet_events()
    picks = []
    for listed in listed_events:
        event_id = listed.get("event_id") or listed.get("id")
        if not event_id or db_seen(event_id):
            continue
        detail = sb.superbet_event_details(event_id)
        if not detail or not sb.event_is_still_live(detail):
            continue
        minute = sb.event_minute(detail)
        score = sb.event_score(detail)
        home, away = sb.event_teams(detail)
        if not home or not away or minute is None or score is None:
            continue
        if not MINUTE_MIN <= minute <= MINUTE_MAX:
            continue
        fixture = find_pitch_match(fixtures, home, away)
        if not fixture:
            log.info("Brak jednoznacznego dopasowania PitchAPI: %s - %s", home, away)
            continue
        try:
            signal = qualifies(fixture["id"], score)
        except Exception as exc:
            log.warning("PitchAPI nie potwierdził %s - %s: %s", home, away, exc)
            continue
        if not signal:
            continue
        markets = exact_ht_market(detail, signal["line"])
        if not markets:
            continue
        picks.append((detail, event_id, home, away, signal, markets[0]))

    for detail, event_id, home, away, signal, market in picks[:MAX_PICKS]:
        # Ostatnia kontrola kursu i statusu rynku przed publikacją.
        fresh = sb.superbet_event_details(event_id)
        if not fresh or not sb.event_is_still_live(fresh):
            continue
        current = sb.event_minute(fresh)
        current_score = sb.event_score(fresh)
        if current is None or current_score != signal["score"] or not MINUTE_MIN <= current <= MINUTE_MAX:
            continue
        current_market = next((m for m in exact_ht_market(fresh, signal["line"])
                               if abs(m["line"] - market["line"]) < 0.001), None)
        if not current_market:
            continue
        message = (
            f"⚡ ALGORYTM 2 — GOL DO PRZERWY | {current}' | {current_score[0]}:{current_score[1]}\n"
            f"⚽ {home} – {away}\n"
            f"🎯 Typ: OVER {current_market['line']:.1f} GOLA HT\n"
            f"💹 Kurs Superbet: {current_market['odds']:.2f}\n"
            f"📊 1–20': strzały {signal['shots']} (celne {signal['on_target']}), "
            f"rożne {signal['corners']}\n"
            f"📈 Momentum: mocny nacisk przez min. 3 kolejne odczyty\n"
            f"💰 Stawka: {sb.STAKE} zł\n"
            f"🔗 Mecz: {sb.superbet_url(fresh)}"
        )
        if sb.send_telegram(message):
            db_mark_sent(event_id)
            log.info("Wysłano Algorytm 2: %s - %s | OVER %.1f @ %.2f",
                     home, away, signal["line"], current_market["odds"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
