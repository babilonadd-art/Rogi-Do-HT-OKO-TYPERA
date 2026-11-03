import os
import json
import time
import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

# Rogi Do HT - OKO TYPERA
# Prematch Over 4.5 first-half corners. SofaScore endpoints are unofficial
# and may change or block automated requests; the bot fails closed on missing data.

API = "https://www.sofascore.com/api/v1"
TZ = ZoneInfo("Europe/Warsaw")
DB_PATH = "okotypera_rogi.db"
STAKE = 50.0
MAX_PICKS_PER_DAY = 2
MIN_LEAD_HOURS = 3
MAX_LOOKAHEAD_HOURS = 36
HISTORY_PER_TEAM = 8
MIN_HISTORY_PER_TEAM = 5
MIN_HIT_RATE = 0.60
MIN_AVG_TOTAL_CORNERS = 5.5
SCAN_MAX_EVENTS = 35

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("okotypera-rogi")

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.sofascore.com/",
})


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS picks (
            event_id INTEGER PRIMARY KEY,
            match_date TEXT NOT NULL,
            league TEXT,
            home TEXT,
            away TEXT,
            kickoff INTEGER,
            sent_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            first_half_corners INTEGER,
            result TEXT,
            settled_at INTEGER
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS daily_summary (
            match_date TEXT PRIMARY KEY,
            sent INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            profit REAL NOT NULL DEFAULT 0
        )
    """)
    con.commit()
    return con


def api(path, params=None):
    url = API + path
    try:
        response = session.get(url, params=params, timeout=20)
        if response.status_code in (403, 429):
            log.warning("SofaScore zwrócił HTTP %s dla %s", response.status_code, path)
            return None
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("Błąd SofaScore %s: %s", path, exc)
        return None


def telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        log.error("Brak sekretów TELEGRAM_BOT_TOKEN lub TELEGRAM_CHAT_ID.")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=20,
        )
        if not r.ok:
            log.error("Telegram HTTP %s: %s", r.status_code, r.text[:300])
            return False
        return bool(r.json().get("ok"))
    except requests.RequestException as exc:
        log.error("Telegram error: %s", exc)
        return False


def get_events(date_string):
    data = api(f"/sport/football/scheduled-events/{date_string}")
    return data.get("events", []) if isinstance(data, dict) else []


def corner_pair(stats_data):
    """Return (home, away) first-half corners; None if unavailable."""
    if not isinstance(stats_data, dict):
        return None
    root = stats_data.get("statistics", stats_data)
    periods = root.get("periods", []) if isinstance(root, dict) else []
    candidates = []
    for period in periods:
        if str(period.get("period", "")).upper() in ("1ST", "FIRST_HALF", "1H"):
            candidates.append(period)
    if not candidates:
        # Some SofaScore responses expose the requested half as a flat structure.
        if isinstance(root, dict) and root.get("period") in ("1ST", "FIRST_HALF", "1H"):
            candidates.append(root)
    for period in candidates:
        for group in period.get("groups", []):
            for item in group.get("statistics", []):
                name = str(item.get("name", "")).strip().lower()
                if name in ("corner kicks", "corners", "corner"):
                    try:
                        return int(float(item["home"])), int(float(item["away"]))
                    except (KeyError, TypeError, ValueError):
                        pass
    return None


def team_history(team_id):
    data = api(f"/team/{team_id}/events/last/0")
    events = data.get("events", []) if isinstance(data, dict) else []
    values = []
    for event in events:
        status = event.get("status", {}).get("type")
        if status not in ("finished", "after_extra_time", "after_penalties"):
            continue
        event_id = event.get("id")
        if not event_id:
            continue
        stats = api(f"/event/{event_id}/statistics")
        pair = corner_pair(stats)
        if pair is not None:
            values.append(sum(pair))
        if len(values) >= HISTORY_PER_TEAM:
            break
        time.sleep(0.15)
    return values


def assess(event):
    home_team = event.get("homeTeam") or {}
    away_team = event.get("awayTeam") or {}
    if not home_team.get("id") or not away_team.get("id"):
        return None

    home_history = team_history(home_team["id"])
    away_history = team_history(away_team["id"])
    if len(home_history) < MIN_HISTORY_PER_TEAM or len(away_history) < MIN_HISTORY_PER_TEAM:
        log.info("Za mało danych historycznych: %s - %s", home_team.get("name"), away_team.get("name"))
        return None

    combined = home_history + away_history
    hit_rate = sum(1 for n in combined if n >= 5) / len(combined)
    avg = sum(combined) / len(combined)

    # Conservative filter: both recent samples together must support the line.
    if hit_rate < MIN_HIT_RATE or avg < MIN_AVG_TOTAL_CORNERS:
        return None

    return {"hit_rate": hit_rate, "average": avg, "sample": len(combined)}


def format_pick(event, assessment, now):
    kickoff = int(event["startTimestamp"])
    start = datetime.fromtimestamp(kickoff, TZ)
    remaining = start - now
    hours = int(remaining.total_seconds() // 3600)
    minutes = int((remaining.total_seconds() % 3600) // 60)
    tournament = event.get("tournament") or {}
    category = tournament.get("category") or {}
    league = tournament.get("name") or category.get("name") or "Liga nieznana"
    home = event.get("homeTeam", {}).get("name", "Gospodarze")
    away = event.get("awayTeam", {}).get("name", "Goście")
    return (
        "🎯 ROGI DO HT – OKO TYPERA\n\n"
        f"⚽ {home} – {away}\n"
        f"🏆 {league}\n"
        f"🕒 Start: {start.strftime('%d.%m.%Y %H:%M')} (Polska)\n"
        f"⏳ Do meczu: {hours} godz. {minutes} min\n\n"
        "📌 TYP: Over 4.5 rożnego do HT\n"
        f"📊 Ostatnia próbka: {assessment['hit_rate']*100:.0f}% meczów z min. 5 rożnymi do HT\n"
        f"📈 Średnia próbki: {assessment['average']:.2f} rożnego\n"
        f"💰 Stawka: {STAKE:.0f} zł\n"
        "ℹ️ Analiza statystyczna, nie gwarancja wyniku."
    )


def settle_pending(con, now_ts):
    rows = con.execute("SELECT * FROM picks WHERE status='PENDING' AND kickoff < ?", (now_ts,)).fetchall()
    for row in rows:
        stats = api(f"/event/{row['event_id']}/statistics")
        pair = corner_pair(stats)
        if pair is None:
            continue
        total = sum(pair)
        # Only settle once the match has progressed beyond the first half.
        detail = api(f"/event/{row['event_id']}")
        ev = detail.get("event", {}) if isinstance(detail, dict) else {}
        status = (ev.get("status") or {}).get("type", "")
        if status not in ("inprogress", "finished", "after_extra_time", "after_penalties"):
            continue
        # If the first-half stats are returned, the half is complete once event is live/finished.
        won = total >= 5
        result = "WIN" if won else "LOSS"
        profit = STAKE * 1.0 if won else -STAKE
        # Flat stake accounting is stake-based, not odds-based; net profit cannot be exact
        # without the actual bookmaker odds. WIN is shown as +50 zł stake units, not payout.
        con.execute(
            "UPDATE picks SET status=?, first_half_corners=?, result=?, settled_at=? WHERE event_id=?",
            (result, total, result, now_ts, row["event_id"])
        )
        con.execute("""
            INSERT INTO daily_summary(match_date, wins, losses, profit)
            VALUES(?,?,?,?)
            ON CONFLICT(match_date) DO UPDATE SET
              wins=wins+excluded.wins,
              losses=losses+excluded.losses,
              profit=profit+excluded.profit
        """, (row["match_date"], 1 if won else 0, 0 if won else 1, profit))
        con.commit()
        label = "✅ WYGRANY" if won else "❌ PRZEGRANY"
        telegram(
            f"📊 ROZLICZENIE – ROGI DO HT\n\n"
            f"⚽ {row['home']} – {row['away']}\n"
            f"🏆 {row['league']}\n"
            f"📌 Over 4.5 rożnego do HT\n"
            f"🚩 Rożne w 1. połowie: {total}\n"
            f"{label}\n"
            f"💰 Stawka: {STAKE:.0f} zł\n"
            f"{'Wynik: +50 zł jednostek stawki (bez kursu nie jest to zysk netto)' if won else 'Wynik: -50 zł stawki'}"
        )


def main():
    con = db()
    now = datetime.now(TZ)
    now_ts = int(now.timestamp())
    settle_pending(con, now_ts)

    day = now.strftime("%Y-%m-%d")
    already_today = con.execute(
        "SELECT COUNT(*) FROM picks WHERE match_date=?", (day,)
    ).fetchone()[0]
    remaining_slots = max(0, MAX_PICKS_PER_DAY - already_today)
    if remaining_slots == 0:
        log.info("Dzienny limit typów wykorzystany.")
        con.close()
        return

    earliest = now + timedelta(hours=MIN_LEAD_HOURS)
    latest = now + timedelta(hours=MAX_LOOKAHEAD_HOURS)
    events = []
    for offset in (0, 1, 2):
        date_str = (now.date() + timedelta(days=offset)).isoformat()
        events.extend(get_events(date_str))
        time.sleep(0.25)

    seen = set()
    candidates = []
    for event in events:
        event_id = event.get("id")
        kickoff_ts = event.get("startTimestamp")
        if not event_id or not kickoff_ts or event_id in seen:
            continue
        seen.add(event_id)
        start = datetime.fromtimestamp(int(kickoff_ts), TZ)
        if start < earliest or start > latest:
            continue
        if (event.get("status") or {}).get("type") != "notstarted":
            continue
        if con.execute("SELECT 1 FROM picks WHERE event_id=?", (event_id,)).fetchone():
            continue
        candidates.append(event)

    candidates.sort(key=lambda e: int(e["startTimestamp"]))
    log.info("Mecze w oknie 3-36h: %s", len(candidates))

    sent = 0
    for event in candidates[:SCAN_MAX_EVENTS]:
        if sent >= remaining_slots:
            break
        assessment = assess(event)
        if not assessment:
            continue
        message = format_pick(event, assessment, now)
        if telegram(message):
            kickoff = int(event["startTimestamp"])
            match_day = datetime.fromtimestamp(kickoff, TZ).strftime("%Y-%m-%d")
            con.execute("""
                INSERT OR IGNORE INTO picks
                (event_id, match_date, league, home, away, kickoff, sent_at, status)
                VALUES(?,?,?,?,?,?,?,'PENDING')
            """, (
                event["id"], match_day,
                (event.get("tournament") or {}).get("name", "Liga nieznana"),
                event.get("homeTeam", {}).get("name", ""),
                event.get("awayTeam", {}).get("name", ""),
                kickoff, now_ts
            ))
            con.commit()
            sent += 1
            time.sleep(1)
    log.info("Wysłane typy: %s", sent)
    con.close()


if __name__ == "__main__":
    main()
