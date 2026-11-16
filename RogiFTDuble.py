#!/usr/bin/env python3
"""Prematch FT corner Over doubles. No team-history/statistical filters."""
import os
import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import Main as source

TZ = ZoneInfo("Europe/Warsaw")
DB = "rogift_duble.db"
STAKE = 50.0
MIN_LEG_ODDS = 1.80
MAX_DOUBLES_PER_DAY = 2
LEGS_PER_DOUBLE = 2
MIN_HOURS_BEFORE_KICKOFF = 3
# Test the common full-match totals in ascending order; use the first qualifying line.
FT_LINES = (9.5, 10.5, 11.5, 12.5, 13.5, 14.5)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def db_open():
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS ft_doubles(
        coupon_id TEXT PRIMARY KEY, day TEXT NOT NULL, sent_at TEXT NOT NULL,
        total_odds REAL NOT NULL, stake REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING', settled_at TEXT, profit REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS ft_double_legs(
        event_id INTEGER PRIMARY KEY, coupon_id TEXT NOT NULL, league TEXT NOT NULL,
        home TEXT NOT NULL, away TEXT NOT NULL, start_ts INTEGER NOT NULL,
        line REAL NOT NULL, odds REAL NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
        ft_corners INTEGER, FOREIGN KEY(coupon_id) REFERENCES ft_doubles(coupon_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS ft_double_daily_reports(
        day TEXT PRIMARY KEY, sent_at TEXT NOT NULL)""")
    c.commit()
    return c


def send(text):
    return source.send(text)


def fetch_candidates(now):
    start = now.astimezone(timezone.utc)
    # Look ahead 36 hours so late-night scans also see tomorrow's fixtures.
    end = start + timedelta(hours=36)
    events = source.superbet_football_events(start, end)
    candidates = []
    earliest = int((now + timedelta(hours=MIN_HOURS_BEFORE_KICKOFF)).timestamp())
    for event in events:
        fixture = event.get("fixture") or {}
        event_id = event.get("event_id") or event.get("id")
        if not event_id:
            continue
        try:
            kickoff = int(datetime.fromisoformat(str(fixture["utc_date"]).replace("Z", "+00:00")).timestamp())
        except (KeyError, TypeError, ValueError):
            continue
        if kickoff < earliest:
            continue
        parts = str(fixture.get("event_name") or "").split("·", 1)
        if len(parts) != 2:
            continue
        home, away = (part.strip() for part in parts)
        if not home or not away:
            continue
        prices = source.superbet_ft_corners_over_prices(
            int(event_id), lines=FT_LINES, min_odds=MIN_LEG_ODDS
        )
        chosen = next(
            ((line, float(prices[line])) for line in FT_LINES if line in prices),
            None,
        )
        if not chosen:
            continue
        candidates.append({
            "event_id": int(event_id),
            "league": source.extract_league_name(event) or "Liga niepodana",
            "home": home, "away": away, "start_ts": kickoff,
            "line": chosen[0], "odds": chosen[1],
        })
    candidates.sort(key=lambda x: (x["start_ts"], x["line"], abs(x["odds"] - MIN_LEG_ODDS)))
    logging.info("ROGI FT: kandydaci spełniający kurs >= %.2f: %s", MIN_LEG_ODDS, len(candidates))
    return candidates


def send_new_doubles(c, now):
    day = now.date().isoformat()
    sent_today = c.execute(
        "SELECT COUNT(*) FROM ft_doubles WHERE day=?", (day,)
    ).fetchone()[0]
    remaining = max(0, MAX_DOUBLES_PER_DAY - int(sent_today))
    history = c.execute(
        "SELECT coupon_id,sent_at,total_odds,status FROM ft_doubles WHERE day=? ORDER BY sent_at",
        (day,),
    ).fetchall()
    for coupon_id, sent_at, total_odds, status in history:
        legs = c.execute(
            "SELECT home,away,line,odds FROM ft_double_legs WHERE coupon_id=? ORDER BY start_ts",
            (coupon_id,),
        ).fetchall()
        logging.info("Historia dubla %s | %s | kurs %.3f | %s | zdarzenia=%s",
                     coupon_id, sent_at, total_odds, status, legs)
    if remaining == 0:
        logging.info("Limit dubli na dziś osiągnięty: %s/%s", sent_today, MAX_DOUBLES_PER_DAY)
        return

    already_used = {int(row[0]) for row in c.execute(
        "SELECT event_id FROM ft_double_legs"
    ).fetchall()}
    candidates = [x for x in fetch_candidates(now) if x["event_id"] not in already_used]
    made = 0
    while remaining and len(candidates) >= LEGS_PER_DOUBLE:
        pair = candidates[:LEGS_PER_DOUBLE]
        candidates = candidates[LEGS_PER_DOUBLE:]
        total_odds = round(pair[0]["odds"] * pair[1]["odds"], 3)
        coupon_id = f"{day}-{int(now.timestamp())}-{made + 1}"
        lines = []
        for index, item in enumerate(pair, 1):
            kickoff = datetime.fromtimestamp(item["start_ts"], timezone.utc).astimezone(TZ)
            lines.append(
                f"{index}. {item['league']}\n"
                f"⚽ {item['home']} – {item['away']}\n"
                f"🎯 OVER {item['line']:.1f} rożnych FT\n"
                f"💹 Kurs: {item['odds']:.2f} | 🕒 {kickoff:%d.%m %H:%M}"
            )
        message = (
            "🚩 ROGI FT — DUBEL\n"
            f"📅 {now:%d.%m.%Y} | DUBEL {int(sent_today) + made + 1}/{MAX_DOUBLES_PER_DAY}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            + "\n\n".join(lines)
            + "\n━━━━━━━━━━━━━━━━━━\n"
            f"💹 KURS ŁĄCZNY: {total_odds:.2f}\n"
            f"💰 STAWKA: {STAKE:.2f} zł\n"
            f"🏦 Potencjalna wygrana: {STAKE * total_odds:.2f} zł\n"
            "ℹ️ Wyłącznie rożne FT — cały mecz"
        )
        message_id = send(message)
        if not message_id:
            logging.error("Telegram nie potwierdził wysłania dubla %s", coupon_id)
            break
        c.execute(
            "INSERT INTO ft_doubles(coupon_id,day,sent_at,total_odds,stake) VALUES(?,?,?,?,?)",
            (coupon_id, day, now.isoformat(), total_odds, STAKE),
        )
        for item in pair:
            c.execute("""INSERT INTO ft_double_legs
                (event_id,coupon_id,league,home,away,start_ts,line,odds)
                VALUES(?,?,?,?,?,?,?,?)""",
                (item["event_id"], coupon_id, item["league"], item["home"], item["away"],
                 item["start_ts"], item["line"], item["odds"]))
        c.commit()
        made += 1
        remaining -= 1
    logging.info("Nowe duble dziś: %s; maksymalny limit: %s", made, MAX_DOUBLES_PER_DAY)


def settle_doubles(c, now):
    pending = c.execute("""SELECT event_id,coupon_id,league,home,away,start_ts,line,odds
        FROM ft_double_legs WHERE status='PENDING' ORDER BY start_ts""").fetchall()
    now_ts = int(now.timestamp())
    for event_id, coupon_id, league, home, away, start_ts, line, odds in pending:
        if now_ts < int(start_ts) + 3 * 60 * 60:
            continue
        total = source.cornerflick_ft_corner_total(league, home, away, start_ts)
        if total is None:
            total = source.matchday_ft_corner_total(league, home, away, start_ts)
        if total is None:
            logging.info("Brak potwierdzonego wyniku rożnych FT: %s - %s", home, away)
            continue
        result = "WON" if int(total) > float(line) else "LOST"
        c.execute("""UPDATE ft_double_legs SET status=?,ft_corners=? WHERE event_id=?""",
                  (result, int(total), event_id))
        c.commit()
        logging.info("Rozliczono nogę %s - %s: %s (%s rożnych)", home, away, result, total)

    coupons = c.execute("""SELECT coupon_id,total_odds,stake FROM ft_doubles
        WHERE status='PENDING'""").fetchall()
    for coupon_id, total_odds, stake in coupons:
        legs = c.execute(
            "SELECT status FROM ft_double_legs WHERE coupon_id=?", (coupon_id,)
        ).fetchall()
        statuses = [row[0] for row in legs]
        if len(statuses) != LEGS_PER_DOUBLE or "LOST" in statuses:
            if "LOST" in statuses and all(s in ("WON", "LOST") for s in statuses):
                final, profit = "LOST", -float(stake)
            else:
                continue
        elif all(s == "WON" for s in statuses):
            final = "WON"
            profit = round(float(stake) * (float(total_odds) - 1.0), 2)
        else:
            continue
        c.execute("""UPDATE ft_doubles SET status=?,settled_at=?,profit=?
            WHERE coupon_id=? AND status='PENDING'""",
            (final, now.astimezone(TZ).isoformat(), profit, coupon_id))
        c.commit()
        leg_rows = c.execute("""SELECT league,home,away,line,odds,ft_corners,status
            FROM ft_double_legs WHERE coupon_id=? ORDER BY start_ts""", (coupon_id,)).fetchall()
        details = "\n".join(
            f"• {home} – {away}: OVER {line:.1f}, rożne: {corners}, {status}"
            for league, home, away, line, odds, corners, status in leg_rows
        )
        message = (
            "📊 ROZLICZENIE DUBLA — ROGI FT\n\n" + details +
            f"\n\n💹 Kurs łączny: {float(total_odds):.2f}\n"
            f"💰 Stawka: {float(stake):.2f} zł\n"
            f"{'🟢 DUBEL WYGRANY' if final == 'WON' else '🔴 DUBEL PRZEGRANY'}\n"
            f"🏦 Wynik: {profit:+.2f} zł"
        )
        send(message)

    days = [row[0] for row in c.execute(
        "SELECT DISTINCT day FROM ft_doubles ORDER BY day"
    ).fetchall()]
    for day in days:
        if c.execute("SELECT 1 FROM ft_double_daily_reports WHERE day=?", (day,)).fetchone():
            continue
        open_count = c.execute(
            "SELECT COUNT(*) FROM ft_doubles WHERE day=? AND status='PENDING'", (day,)
        ).fetchone()[0]
        if open_count:
            continue
        rows = c.execute(
            "SELECT status,COALESCE(profit,0) FROM ft_doubles WHERE day=?", (day,)
        ).fetchall()
        if not rows:
            continue
        wins = sum(1 for status, _ in rows if status == "WON")
        losses = sum(1 for status, _ in rows if status == "LOST")
        profit = round(sum(float(value) for _, value in rows), 2)
        report = (
            f"🏁 PODSUMOWANIE DNIA — ROGI FT\n🗓️ {day}\n"
            f"📨 Rozliczone duble: {len(rows)}\n🟢 Wygrane: {wins}\n🔴 Przegrane: {losses}\n"
            f"💰 Stawka za dubel: {STAKE:.2f} zł\n"
            f"🏦 WYNIK DNIA: {profit:+.2f} zł"
        )
        if send(report):
            c.execute("INSERT INTO ft_double_daily_reports(day,sent_at) VALUES(?,?)",
                      (day, now.astimezone(TZ).isoformat()))
            c.commit()


def main():
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        raise RuntimeError("Brak TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID w GitHub Secrets")
    c = db_open()
    try:
        now = datetime.now(TZ)
        settle_doubles(c, now)
        if os.getenv("BOT_MODE", "scan").lower() != "settle":
            send_new_doubles(c, now)
    finally:
        c.close()


if __name__ == "__main__":
    main()
