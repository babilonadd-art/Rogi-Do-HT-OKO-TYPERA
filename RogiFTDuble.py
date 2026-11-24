#!/usr/bin/env python3
"""Prematch FT corner doubles. Uses public Cornerflick stats when available; no API key."""
# Explicit user-requested daily quota reset marker.
import os
import hashlib
import hmac
import json
import sqlite3
import logging
import requests
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from rogift_graphic import render_coupon_image
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import Main as source

TZ = ZoneInfo("Europe/Warsaw")
DB = "rogift_duble.db"
STAKE = 50.0
MIN_LEG_ODDS = 1.60
MAX_LEG_ODDS = 2.60
MAX_DOUBLES_PER_DAY = 4
NIGHT_START_HOUR = 22
NIGHT_END_HOUR = 8
GRAPHIC_VERSION = "oko-typera-rogift-clean-list-original-bull-v14"  # [replace-graphics-ft]  # uruchomienie rozliczenia FT po aktualizacji harmonogramu  # [settle-ft]
LEGS_PER_DOUBLE = 2
MIN_HOURS_BEFORE_KICKOFF = 0
# Test the common full-match totals in ascending order; use the first qualifying line.
MIN_FT_CORNERS_LINE = 9.5
MAX_FT_CORNERS_LINE = 9.5
FT_LINES = (9.5,)

# Twarda blokada rozgrywek, które nie mają trafiać do kuponów.
# Nazwy są normalizowane; dodatkowe ligi można dopisać przez zmienną
# UNDER_CORNER_LEAGUES w GitHub Actions (nazwy oddzielone średnikiem).
UNDER_CORNER_LEAGUE_BLOCKLIST = {
    "u18", "u19", "u20", "u21", "u23",
    "youth", "junior", "reserves", "reserve", "development league",
    "club friendlies", "international friendlies",
    "egypt", "egyptian", "egipt",
}
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def db_open():
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS ft_doubles(
        coupon_id TEXT PRIMARY KEY, day TEXT NOT NULL, sent_at TEXT NOT NULL,
        total_odds REAL NOT NULL, stake REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING', settled_at TEXT, profit REAL,
        bucket TEXT NOT NULL DEFAULT 'day', quota_day TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS ft_double_legs(
        event_id INTEGER PRIMARY KEY, coupon_id TEXT NOT NULL, league TEXT NOT NULL,
        home TEXT NOT NULL, away TEXT NOT NULL, start_ts INTEGER NOT NULL,
        line REAL NOT NULL, odds REAL NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
        ft_corners INTEGER, FOREIGN KEY(coupon_id) REFERENCES ft_doubles(coupon_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS ft_double_daily_reports(
        day TEXT PRIMARY KEY, sent_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS ft_double_reposts(
        coupon_id TEXT PRIMARY KEY, sent_at TEXT NOT NULL, message_id INTEGER,
        FOREIGN KEY(coupon_id) REFERENCES ft_doubles(coupon_id))""")
    double_columns = {row[1] for row in c.execute("PRAGMA table_info(ft_doubles)")}
    if "bucket" not in double_columns:
        c.execute("ALTER TABLE ft_doubles ADD COLUMN bucket TEXT NOT NULL DEFAULT 'day'")
    if "quota_day" not in double_columns:
        c.execute("ALTER TABLE ft_doubles ADD COLUMN quota_day TEXT")
    c.execute("UPDATE ft_doubles SET quota_day=day WHERE quota_day IS NULL OR quota_day=''")
    if "settlement_reply_sent" not in double_columns:
        c.execute("ALTER TABLE ft_doubles ADD COLUMN settlement_reply_sent INTEGER NOT NULL DEFAULT 0")
    if "settlement_graphic_sent" not in double_columns:
        c.execute("ALTER TABLE ft_doubles ADD COLUMN settlement_graphic_sent INTEGER NOT NULL DEFAULT 0")
    repost_columns = {row[1] for row in c.execute("PRAGMA table_info(ft_double_reposts)")}
    if "graphic_version" not in repost_columns:
        c.execute("ALTER TABLE ft_double_reposts ADD COLUMN graphic_version TEXT NOT NULL DEFAULT ''")
    c.execute("""CREATE TABLE IF NOT EXISTS ft_publication_proofs(
        coupon_id TEXT PRIMARY KEY, publication_code TEXT NOT NULL,
        signature TEXT NOT NULL, payload_json TEXT NOT NULL,
        verification_url TEXT, telegram_message_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(coupon_id) REFERENCES ft_doubles(coupon_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS ft_daily_quota_resets(
        day TEXT PRIMARY KEY, reset_at TEXT NOT NULL)""")
    c.commit()
    return c


def send(text):
    return source.send(text)


def publication_code(coupon_id):
    digest = hashlib.sha256(str(coupon_id).encode('utf-8')).hexdigest()[:10].upper()
    return f"OT-{digest}"


def publication_proof(legs, total_odds, coupon_id):
    """Create a stable HMAC-SHA256 signature over the complete pick, not just its ID."""
    payload = {
        "version": 1,
        "brand": "OKO TYPERA",
        "coupon_id": str(coupon_id),
        "stake_pln": round(float(STAKE), 2),
        "total_odds": round(float(total_odds), 3),
        "legs": [
            {
                "event_id": int(leg["event_id"]),
                "league": str(leg["league"]),
                "home": str(leg["home"]),
                "away": str(leg["away"]),
                "kickoff_utc": int(leg["start_ts"]),
                "market": "OVER_CORNERS_FT",
                "line": round(float(leg["line"]), 2),
                "odds": round(float(leg["odds"]), 3),
            }
            for leg in legs[:LEGS_PER_DOUBLE]
        ],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # Prefer a dedicated key; the Telegram bot token is a secure fallback so setup
    # remains automatic and no extra GitHub Secret is required.
    secret = os.getenv("PUBLICATION_SIGNING_SECRET") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not secret:
        raise RuntimeError("Brak klucza do podpisu publikacji")
    signature = hmac.new(
        secret.encode("utf-8"),
        b"OKO-TYPERA-ROGI-FT-V1\\0" + canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:24].upper()
    return payload, signature


def telegram_message_link(token, chat_id, message_id):
    """Resolve a permalink to the exact post in the official Telegram channel."""
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/getChat",
            data={"chat_id": chat_id},
            timeout=20,
        )
        if not response.ok:
            logging.error("Telegram API odrzuciło publikację: HTTP %s | %s", response.status_code, response.text[:2000])
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            logging.warning("Telegram getChat nie zwrócił danych kanału: %s", payload)
            return None
        chat = payload.get("result") or {}
        username = chat.get("username")
        if username:
            return f"https://t.me/{username}/{int(message_id)}"
        chat_value = str(chat_id)
        if chat_value.startswith("-100"):
            return f"https://t.me/c/{chat_value[4:]}/{int(message_id)}"
        logging.warning("Nie można utworzyć linku do posta dla chat_id=%s", chat_value)
    except Exception:
        logging.exception("Nie udało się ustalić linku do oryginalnego posta Telegram")
    return None


def send_coupon_graphic(legs, total_odds, coupon_no, coupon_id, existing_proof=None, existing_message_id=None, night=False):
    """Send the original PNG with event-specific Superbet corner-market buttons."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    proof_payload, signature = publication_proof(legs, total_odds, coupon_id)
    telegram_caption = ("🌙 NOCNE TYPY • ROŻNE FT\nDane statystyczne: Cornerflick.com" if night else "Dane statystyczne: Cornerflick.com")

    # Telegram photos cannot contain clickable regions. Native URL buttons
    # are attached immediately below the image, one for each fixture.
    keyboard = []
    for index, leg in enumerate(legs[:LEGS_PER_DOUBLE], 1):
        market_url = source.superbet_match_url(
            int(leg["event_id"]), leg["home"], leg["away"], int(leg["start_ts"])
        )
        if not isinstance(market_url, str) or not market_url.startswith("https://") or "superbet" not in market_url.lower():
            logging.error("Brak prawidłowego linku Superbet do meczu %s - %s; pomijam publikację",
                          leg.get("home"), leg.get("away"))
            return None
        keyboard.append([{
            "text": f"SUPERBET — ROŻNE FT • MECZ {index}",
            "url": market_url,
        }])
    reply_markup = json.dumps({"inline_keyboard": keyboard}, ensure_ascii=False)

    graphic = render_coupon_image(
        legs, total_odds, STAKE, coupon_no=coupon_no,
        watermark_id=publication_code(coupon_id), night=night,
    )
    try:
        if existing_message_id:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/editMessageMedia",
                data={
                    "chat_id": chat_id,
                    "message_id": int(existing_message_id),
                    "media": json.dumps({
                        "type": "photo", "media": "attach://photo",
                        **({"caption": telegram_caption} if telegram_caption else {}),
                    }),
                    "reply_markup": reply_markup,
                },
                files={"photo": ("rogi-ft-dubel.png", graphic, "image/png")},
                timeout=45,
            )
            if not response.ok:
                try:
                    error_payload = response.json()
                except ValueError:
                    error_payload = {}
                description = str(error_payload.get("description") or "")
                if error_payload.get("error_code") == 400 and (
                    "MESSAGE_ID_INVALID" in description or "message to edit not found" in description.lower()
                ):
                    logging.warning(
                        "Oryginalny post Telegram (message_id=%s) został usunięty ręcznie; "
                        "pomijam odtworzenie wiadomości i NIE wysyłam jej ponownie.",
                        existing_message_id,
                    )
                    return None
        else:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data={
                    "chat_id": chat_id, "reply_markup": reply_markup,
                    **({"caption": telegram_caption} if telegram_caption else {}),
                },
                files={"photo": ("rogi-ft-dubel.png", graphic, "image/png")},
                timeout=45,
            )
        if not response.ok:
            logging.error("Telegram odrzucił edit/send photo: HTTP %s | %s", response.status_code, response.text[:2000])
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            logging.error("Telegram sendPhoto: %s", payload)
            return None
        message_id = int(payload["result"]["message_id"])
        logging.info("%s, Telegram message_id=%s, dodano %s linki Superbet FT",
                     "Zastąpiono grafikę w istniejącym poście" if existing_message_id else "Wysłano oryginalną grafikę z podpisem" if telegram_caption else "Wysłano oryginalną grafikę bez opisu",
                     message_id, len(keyboard))
        return {
            "message_id": message_id,
            "publication_code": publication_code(coupon_id),
            "signature": signature,
            "payload_json": json.dumps(proof_payload, ensure_ascii=False, sort_keys=True),
            "verification_url": None,
            "created_at": datetime.now(TZ).isoformat(),
        }
    except Exception:
        logging.exception("Nie udało się wysłać grafiki dubla FT z linkami Superbet")
        return None

def send_settled_coupon_graphic(c, legs, total_odds, coupon_id):
    """Send the same coupon poster with the settlement label, only after settlement."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    try:
        graphic = render_coupon_image(
            legs, total_odds, STAKE, coupon_no=1,
            watermark_id=publication_code(coupon_id), settled=True,
        )
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat_id},
            files={"photo": ("rogi-ft-dubel-rozliczony.png", graphic, "image/png")},
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            logging.error("Telegram sendPhoto rozliczonego dubla: %s", payload)
            return False
        c.execute("UPDATE ft_doubles SET settlement_graphic_sent=1 WHERE coupon_id=?", (coupon_id,))
        c.commit()
        logging.info("Wysłano grafikę rozliczonego dubla FT, message_id=%s",
                     payload["result"]["message_id"])
        return True
    except Exception:
        logging.exception("Nie udało się wysłać grafiki rozliczonego dubla FT")
        return False


def save_publication_proof(c, coupon_id, proof):
    if not proof:
        return
    c.execute(
        """INSERT INTO ft_publication_proofs
        (coupon_id,publication_code,signature,payload_json,verification_url,
         telegram_message_id,created_at) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(coupon_id) DO UPDATE SET
          verification_url=COALESCE(ft_publication_proofs.verification_url, excluded.verification_url),
          telegram_message_id=excluded.telegram_message_id,
          created_at=excluded.created_at""",
        (
            coupon_id,
            proof["publication_code"],
            proof["signature"],
            proof["payload_json"],
            proof.get("verification_url"),
            int(proof["message_id"]),
            proof["created_at"],
        ),
    )


def normalized_league_name(value):
    import re
    import unicodedata
    value = unicodedata.normalize("NFKD", str(value or "").casefold())
    value = value.replace("ł", "l")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", value)


def is_blocked_under_league(league):
    name = normalized_league_name(league)
    blocked = {normalized_league_name(x) for x in UNDER_CORNER_LEAGUE_BLOCKLIST}
    extra = os.getenv("UNDER_CORNER_LEAGUES", "")
    blocked.update(normalized_league_name(x) for x in extra.split(";") if x.strip())
    # Odrzucamy młodzieżówki/rezerwy również wtedy, gdy nazwa ligi zawiera
    # dopisek kraju lub sponsora.
    blocked_tokens = ("u18", "u19", "u20", "u21", "u23", "youth", "junior",
                      "mlodziez", "reserve", "reserves", "rezerwy", "development",
                      "friendlies", "friendly", "egypt", "egyptian", "egipt")
    return name in blocked or any(token in name for token in blocked_tokens)


def is_blocked_youth_or_reserve_team(team):
    """Reject youth/reserve sides even when the provider gives a generic league name."""
    import re
    value = str(team or "").casefold()
    value = value.replace("ё", "е")
    patterns = (
        r"\bu\s*[- ]?\s*(18|19|20|21|23)\b",
        r"\bunder\s*[- ]?\s*(18|19|20|21|23)\b",
        r"\b(u18|u19|u20|u21|u23)\b",
        r"\b(reserves?|rezerwy|youth|junior(?:s)?)\b",
        r"(?:^|\s)(?:ii|iii|b)\s*$",
    )
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in patterns)


_matchday_ft_rows = None
FT_STATS_MIN_SAMPLE = 2  # at least 2 real usable matches out of the latest 5
FT_STATS_WINDOW = 5
FT_STATS_MIN_TEAM_AVG = 2.7
FT_STATS_MIN_COMBINED_AVG_TOP6 = 8.8
FT_STATS_MIN_COMBINED_AVG_OTHER = 9.0
FT_STATS_MIN_OVER85_HITS = 2  # each team: at least 2 recent match totals > 8.5
# Nocą dopuszczamy szerszy profil, ale nadal wymagamy realnych danych i kursu Superbet.
FT_NIGHT_MIN_TEAM_AVG = 2.4
FT_NIGHT_MIN_COMBINED_AVG_TOP6 = 8.0
FT_NIGHT_MIN_COMBINED_AVG_OTHER = 8.3
FT_NIGHT_MIN_OVER85_HITS = 1
TOP_SIX_LEAGUE_MARKERS = (
    "premierleague", "laliga", "bundesliga", "ligue1", "eredivisie",
)


def _recent_five_corner_profile(history):
    """Use up to five latest completed matches; require at least two real corner records."""
    rows = [row for row in (history or [])[:FT_STATS_WINDOW]
            if len(row) >= 3 and row[1] is not None and row[2] is not None]
    if len(rows) < FT_STATS_MIN_SAMPLE:
        return None
    own_average = sum(float(row[1]) for row in rows) / len(rows)
    over85_hits = sum(float(row[2]) >= 9 for row in rows)
    return {
        "sample": len(rows),
        "own_average": own_average,
        "over85_hits": over85_hits,
        "over85_rate": over85_hits / len(rows),
        "match_totals": [int(row[2]) for row in rows],
    }


def _is_top_six_league(league):
    name = normalized_league_name(league)
    if name == "seriea" or name.endswith(("italyseriea", "italianseriea")):
        return True
    return any(name == marker or name.endswith(marker) for marker in TOP_SIX_LEAGUE_MARKERS)


def _candidate_ft_corner_stats(item):
    """Require real Open Matchday FT corner history and rank by measured quality."""
    candidate = dict(item)
    score, detail = source.ft_matchday_candidate_profile(
        candidate["league"], candidate["home"], candidate["away"], candidate["start_ts"]
    )
    if score is None:
        logging.info(
            "Odrzucono po analizie rożnych FT: %s - %s | %s | %s",
            candidate["home"], candidate["away"], candidate["league"], detail,
        )
        return None
    candidate["quality_score"] = float(score)
    candidate["stats_detail"] = detail
    logging.info(
        "Analiza rożnych FT OK: %s - %s | %s | wynik %.1f | %s | kurs %.2f",
        candidate["home"], candidate["away"], candidate["league"],
        candidate["quality_score"], detail, candidate["odds"],
    )
    return candidate


def _event_corner_candidate(event, earliest):
    """Read one event's live Superbet FT corners market; safe to run in parallel."""
    fixture = event.get("fixture") or {}
    event_id = event.get("event_id") or event.get("id")
    if not event_id:
        return None
    try:
        kickoff = int(datetime.fromisoformat(
            str(fixture["utc_date"]).replace("Z", "+00:00")
        ).timestamp())
    except (KeyError, TypeError, ValueError):
        return None
    if kickoff < earliest:
        return None
    parts = str(fixture.get("event_name") or "").split("·", 1)
    if len(parts) != 2:
        return None
    home, away = (part.strip() for part in parts)
    if not home or not away or _placeholder_team(home) or _placeholder_team(away):
        logging.warning("Pomijam zdarzenie %s: brak prawdziwych nazw drużyn (%r - %r)",
                        event_id, home, away)
        return None
    league = source.extract_league_name(event) or "Liga niepodana"
    if (is_blocked_under_league(league)
            or is_blocked_youth_or_reserve_team(home)
            or is_blocked_youth_or_reserve_team(away)):
        logging.info("Pomijam młodzież/rezerwy: %s - %s | %s", home, away, league)
        return None
    try:
        prices = source.superbet_ft_corners_over_prices(
            int(event_id), lines=FT_LINES, min_odds=MIN_LEG_ODDS
        )
        prices = {
            float(line): float(price)
            for line, price in prices.items()
            if float(line) >= MIN_FT_CORNERS_LINE
            and MIN_LEG_ODDS <= float(price) <= MAX_LEG_ODDS
        }
    except Exception:
        logging.exception("Superbet: błąd pobierania rynku rożnych FT dla event_id=%s", event_id)
        return None
    chosen = next(((line, float(prices[line])) for line in FT_LINES if line in prices), None)
    if not chosen:
        return None
    return {
        "event_id": int(event_id), "league": league,
        "home": home, "away": away, "start_ts": kickoff,
        "line": chosen[0], "odds": chosen[1],
        "quality_score": 0.0,
    }

def fetch_candidates(now):
    start = now.astimezone(timezone.utc)
    # Look ahead 36 hours so late-night scans also see tomorrow's fixtures.
    end = start + timedelta(hours=36)
    events = source.superbet_football_events(start, end)
    earliest = int((now + timedelta(hours=MIN_HOURS_BEFORE_KICKOFF)).timestamp())
    logging.info("Superbet: sprawdzam rynki FT dla %s wydarzeń równolegle (12 połączeń)", len(events))

    # Previously every event was checked sequentially; with hundreds of fixtures
    # that made a single scan exceed the Actions time limit. Bounded concurrency
    # speeds up market discovery while limiting pressure on Superbet.
    with ThreadPoolExecutor(max_workers=12) as pool:
        market_candidates = [
            item for item in pool.map(
                lambda event: _event_corner_candidate(event, earliest), events
            ) if item is not None
        ]

    # Team-history statistics are intentionally disabled: fixtures are selected
    # from active Superbet FT corner markets and the configured odds range.
    # Explicit youth/reserve/friendly/Egypt exclusions remain in force.
    candidates = []
    for item in market_candidates:
        if is_blocked_under_league(item.get("league", "")):
            logging.info("Odrzucono %s - %s: zablokowane rozgrywki (%s)",
                         item.get("home"), item.get("away"), item.get("league"))
            continue
        try:
            analyzed = _candidate_ft_corner_stats(item)
            if analyzed is not None:
                candidates.append(analyzed)
        except Exception:
            logging.exception("Błąd analizy statystyk FT dla %s - %s",
                              item.get("home"), item.get("away"))

    candidates.sort(key=lambda x: (-x["quality_score"], x["start_ts"], x["line"]))
    logging.info(
        "ROGI FT: aktywne rynki FT z kursem %.2f–%.2f=%s; kandydaci bez filtra historii=%s",
        MIN_LEG_ODDS, MAX_LEG_ODDS, len(market_candidates), len(candidates)
    )
    return candidates

def night_session_day(now):
    """Night session is named by the date on which its 22:00 start occurs."""
    local = now.astimezone(TZ)
    return (local.date() - timedelta(days=1) if local.hour < NIGHT_END_HOUR else local.date()).isoformat()


def fixture_bucket(start_ts):
    """Classify fixtures by Warsaw kickoff: night 22:00–07:59, day 08:00–21:59."""
    kickoff = datetime.fromtimestamp(int(start_ts), timezone.utc).astimezone(TZ)
    if kickoff.hour >= NIGHT_START_HOUR or kickoff.hour < NIGHT_END_HOUR:
        quota_day = kickoff.date() - timedelta(days=1) if kickoff.hour < NIGHT_END_HOUR else kickoff.date()
        return "night", quota_day.isoformat()
    return "day", kickoff.date().isoformat()


def daily_quota_count(c, day, bucket="day", reset_day=None):
    """Count picks in a quota bucket since the latest explicit daily reset."""
    reset_key = reset_day or day
    reset = c.execute(
        "SELECT reset_at FROM ft_daily_quota_resets WHERE day=?", (reset_key,)
    ).fetchone()
    if reset:
        return int(c.execute(
            "SELECT COUNT(*) FROM ft_doubles WHERE bucket=? AND quota_day=? AND sent_at>?",
            (bucket, day, reset[0]),
        ).fetchone()[0])
    return int(c.execute(
        "SELECT COUNT(*) FROM ft_doubles WHERE bucket=? AND quota_day=?",
        (bucket, day),
    ).fetchone()[0])


def reset_daily_quota(c, now):
    """Force a fresh daytime quota window without deleting pick history."""
    day = now.date().isoformat()
    c.execute(
        """INSERT INTO ft_daily_quota_resets(day,reset_at) VALUES(?,?)
           ON CONFLICT(day) DO UPDATE SET reset_at=excluded.reset_at""",
        (day, now.isoformat()),
    )
    c.commit()
    logging.info("Wymuszony reset dziennego limitu %s: historia i nocna pula zachowane.", day)
    send_new_doubles(c, now)


def send_new_doubles(c, now):
    today = now.date().isoformat()
    active_night_day = night_session_day(now)
    history = c.execute(
        "SELECT coupon_id,sent_at,total_odds,status,bucket,quota_day FROM ft_doubles ORDER BY sent_at"
    ).fetchall()
    for coupon_id, sent_at, total_odds, status, bucket, quota_day in history:
        legs = c.execute(
            "SELECT home,away,line,odds FROM ft_double_legs WHERE coupon_id=? ORDER BY start_ts",
            (coupon_id,),
        ).fetchall()
        logging.info("Historia %s dubla %s | %s | kurs %.3f | %s | zdarzenia=%s",
                     bucket.upper(), coupon_id, sent_at, total_odds, status, legs)

    already_used = {int(row[0]) for row in c.execute("SELECT event_id FROM ft_double_legs").fetchall()}
    candidates = [x for x in fetch_candidates(now) if x["event_id"] not in already_used]
    # Grupuj wszystkie znalezione mecze z okna 36 h według ich właściwej
    # puli i daty rozliczeniowej. Poprzednio odrzucaliśmy jutro i kolejne
    # sesje, mimo że fetch_candidates pobierał te mecze.
    candidate_groups = {}
    for item in candidates:
        bucket, quota_day = fixture_bucket(item["start_ts"])
        candidate_groups.setdefault((bucket, quota_day), []).append(item)
    ordered_groups = sorted(
        candidate_groups.items(),
        key=lambda entry: min(x["start_ts"] for x in entry[1]),
    )

    total_made = 0
    # Każda pula ma własny limit: typy dzienne nie blokują nocnych.
    # W jednej puli liga może wystąpić maksymalnie w jednym duble.
    for (bucket, quota_day), group in ordered_groups:
        reset_key = today if quota_day == today else quota_day
        quota_count = daily_quota_count(c, quota_day, bucket, reset_day=reset_key)
        remaining = max(0, MAX_DOUBLES_PER_DAY - quota_count)
        used_leagues = {
            normalized_league_name(row[0])
            for row in c.execute(
                """SELECT DISTINCT l.league FROM ft_double_legs l
                   JOIN ft_doubles d ON d.coupon_id=l.coupon_id
                   WHERE d.bucket=? AND d.quota_day=?""",
                (bucket, quota_day),
            ).fetchall()
        }
        group.sort(key=lambda x: (-float(x.get("quality_score", 0)), x["start_ts"]))
        logging.info("Pula %s (%s): wykorzystano %s/%s, kandydaci=%s, ligi już użyte=%s",
                     bucket.upper(), quota_day, quota_count, MAX_DOUBLES_PER_DAY,
                     len(group), len(used_leagues))
        made = 0
        while remaining and len(group) >= LEGS_PER_DOUBLE:
            first_index = next(
                (i for i, item in enumerate(group)
                 if normalized_league_name(item["league"]) not in used_leagues),
                None,
            )
            if first_index is None:
                logging.info("Brak nowych lig do kolejnego dubla %s (%s)", bucket.upper(), quota_day)
                break
            first = group.pop(first_index)
            second_index = next(
                (i for i, item in enumerate(group)
                 if normalized_league_name(item["league"]) not in used_leagues
                 and normalized_league_name(item["league"]) != normalized_league_name(first["league"])),
                None,
            )
            if second_index is None:
                logging.info("Brak drugiej, różnej ligi do dubla %s (%s)", bucket.upper(), quota_day)
                break
            second = group.pop(second_index)
            pair = [first, second]

            # Ostatnia kontrola kursów bezpośrednio przed publikacją.
            # Nie używamy kursu z cache, statystyk ani wartości zastępczych:
            # oba kursy muszą pochodzić z aktywnego rynku Superbet dla tego meczu i linii.
            verified_pair = []
            for item in pair:
                fresh_prices = source.superbet_ft_corners_over_prices(
                    int(item["event_id"]), lines=(float(item["line"]),), min_odds=MIN_LEG_ODDS
                )
                fresh_price = fresh_prices.get(round(float(item["line"]), 2))
                if fresh_price is None or not (MIN_LEG_ODDS <= float(fresh_price) <= MAX_LEG_ODDS):
                    logging.warning(
                        "Pomijam dubel: brak aktualnego aktywnego kursu Superbet dla %s - %s, linia FT %s; niczego nie podstawiam.",
                        item["home"], item["away"], item["line"]
                    )
                    verified_pair = []
                    break
                item = dict(item)
                item["odds"] = float(fresh_price)
                verified_pair.append(item)
            if len(verified_pair) != LEGS_PER_DOUBLE:
                continue
            pair = verified_pair
            total_odds = round(float(pair[0]["odds"]) * float(pair[1]["odds"]), 3)
            coupon_id = f"{quota_day}-{bucket}-{int(now.timestamp())}-{made + 1}"
            proof = send_coupon_graphic(
                pair, total_odds, quota_count + made + 1, coupon_id,
                night=(bucket == "night"),
            )
            if not proof:
                logging.error("Telegram nie potwierdził wysłania grafiki %s dubla %s", bucket.upper(), coupon_id)
                break
            c.execute(
                "INSERT INTO ft_doubles(coupon_id,day,sent_at,total_odds,stake,bucket,quota_day) VALUES(?,?,?,?,?,?,?)",
                (coupon_id, quota_day, now.isoformat(), total_odds, STAKE, bucket, quota_day),
            )
            for item in pair:
                c.execute("""INSERT INTO ft_double_legs
                    (event_id,coupon_id,league,home,away,start_ts,line,odds)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (item["event_id"], coupon_id, item["league"], item["home"], item["away"],
                     item["start_ts"], item["line"], item["odds"]))
            save_publication_proof(c, coupon_id, proof)
            c.commit()
            made += 1
            remaining -= 1
            used_leagues.update(normalized_league_name(item["league"]) for item in pair)
        total_made += made
        logging.info("Nowe %s duble dla %s: %s; limit puli: %s",
                     bucket.upper(), quota_day, made, MAX_DOUBLES_PER_DAY)
    logging.info("Łącznie nowe duble FT w tym skanie: %s", total_made)



def _placeholder_team(value):
    text = str(value or "").strip().lower()
    return text in {"a", "b", "team a", "team b", "drużyna a", "drużyna b",
                    "mecz a", "mecz b", "unknown", "tbd", "?"}


def refresh_saved_fixture_names(rows, now):
    """Refresh saved team labels from Superbet before reposting old coupons."""
    start = now.astimezone(timezone.utc) - timedelta(hours=12)
    end = now.astimezone(timezone.utc) + timedelta(hours=48)
    try:
        events = source.superbet_football_events(start, end)
    except Exception:
        logging.exception("Nie udało się odświeżyć nazw drużyn przed ponowną publikacją")
        return {}

    resolved = {}
    wanted = {int(row[0]) for row in rows}
    for event in events:
        fixture = event.get("fixture") or {}
        event_id = event.get("event_id") or event.get("id")
        if not event_id or int(event_id) not in wanted:
            continue
        parts = str(fixture.get("event_name") or "").split("·", 1)
        if len(parts) != 2:
            continue
        home, away = (part.strip() for part in parts)
        if home and away and not _placeholder_team(home) and not _placeholder_team(away):
            resolved[int(event_id)] = (home, away)
    return resolved


def repost_pending_doubles(c, now):
    """Re-publish saved pending coupons without changing their settlement records."""
    # One-time recovery/repost of today's pending coupons. This also covers
    # legacy rows whose proof exists but whose post needs to be shown again.
    # ft_double_reposts prevents a second recovery post on the next 5-minute scan.
    day = now.date().isoformat()
    rows = c.execute("""SELECT coupon_id,day,total_odds,bucket
        FROM ft_doubles WHERE status='PENDING' AND day=? ORDER BY sent_at""",
        (day,)).fetchall()
    logging.info("Dzisiejsze pending duble do jednorazowego ponowienia: %s", len(rows))
    sent = 0
    saved_legs = c.execute("""SELECT event_id FROM ft_double_legs
        WHERE coupon_id IN (SELECT coupon_id FROM ft_doubles WHERE status='PENDING')""").fetchall()
    fresh_names = refresh_saved_fixture_names(saved_legs, now)
    for coupon_id, day, total_odds, bucket in rows:
        if not os.getenv("FORCE_REPOST", "").strip().lower() in ("1", "true", "yes"):
            previous = c.execute(
                "SELECT graphic_version FROM ft_double_reposts WHERE coupon_id=?",
                (coupon_id,),
            ).fetchone()
            if previous and previous[0] == GRAPHIC_VERSION:
                logging.info("Dubel %s ma już grafikę %s — pomijam", coupon_id, GRAPHIC_VERSION)
                continue
        legs = c.execute("""SELECT event_id,league,home,away,start_ts,line,odds
            FROM ft_double_legs WHERE coupon_id=? ORDER BY start_ts""", (coupon_id,)).fetchall()
        if len(legs) != LEGS_PER_DOUBLE:
            logging.warning("Dubel %s ma %s nóg zamiast %s — pomijam", coupon_id, len(legs), LEGS_PER_DOUBLE)
            continue
        graphic_legs = []
        invalid_names = False
        for event_id, league, home, away, start_ts, line, odds in legs:
            fresh = fresh_names.get(int(event_id))
            if fresh:
                home, away = fresh
                c.execute("UPDATE ft_double_legs SET home=?,away=? WHERE event_id=?",
                          (home, away, int(event_id)))
            if _placeholder_team(home) or _placeholder_team(away):
                logging.error("Dubel %s pominięty: brak prawdziwych nazw drużyn dla event_id=%s (%s - %s)",
                              coupon_id, event_id, home, away)
                invalid_names = True
                break
            graphic_legs.append(
                {"event_id": event_id, "league": league, "home": home, "away": away,
                 "start_ts": start_ts, "line": float(line), "odds": float(odds)}
            )
        if invalid_names:
            c.commit()
            continue
        coupon_no = c.execute("SELECT COUNT(*) FROM ft_doubles WHERE day=? AND sent_at<=?",
                              (day, now.isoformat())).fetchone()[0]
        stored = c.execute(
            "SELECT signature,verification_url,telegram_message_id FROM ft_publication_proofs WHERE coupon_id=?",
            (coupon_id,),
        ).fetchone()
        existing_proof = (
            {"signature": stored[0], "verification_url": stored[1]}
            if stored else None
        )
        original_message_id = int(stored[2]) if stored and stored[2] else None
        if not original_message_id:
            logging.error("Dubel %s: brak ID oryginalnego posta Telegram — nie wysyłam duplikatu", coupon_id)
            continue
        proof = send_coupon_graphic(
            graphic_legs, float(total_odds), max(1, coupon_no), coupon_id,
            existing_proof=existing_proof,
            existing_message_id=original_message_id,
            night=(bucket == "night"),
        )
        if not proof:
            logging.error("Nie udało się ponownie opublikować podpisanego dubla %s — zatrzymuję", coupon_id)
            break
        message_id = proof["message_id"]
        save_publication_proof(c, coupon_id, proof)
        c.execute("""INSERT OR REPLACE INTO ft_double_reposts
            (coupon_id,sent_at,message_id,graphic_version) VALUES(?,?,?,?)""",
            (coupon_id, now.astimezone(TZ).isoformat(), int(message_id), GRAPHIC_VERSION))
        c.commit()
        sent += 1
        logging.info("Ponownie opublikowano dubel %s, Telegram message_id=%s", coupon_id, message_id)
    logging.info("Ponownie opublikowane istniejące duble: %s", sent)



def replace_today_pending_graphics(c, now):
    """Edit existing Telegram photo posts in place to add local kickoff times."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    rows = c.execute("""SELECT d.coupon_id,d.total_odds,p.telegram_message_id
        FROM ft_doubles d
        JOIN ft_publication_proofs p ON p.coupon_id=d.coupon_id
        WHERE d.status='PENDING'
        ORDER BY d.sent_at DESC LIMIT 2""").fetchall()
    rows.reverse()
    logging.info("Ostatnie nierozliczone grafiki do podmiany w miejscu: %s", len(rows))
    replaced = 0
    for index, (coupon_id, total_odds, message_id) in enumerate(rows, 1):
        legs = c.execute("""SELECT event_id,league,home,away,start_ts,line,odds
            FROM ft_double_legs WHERE coupon_id=? ORDER BY start_ts""",
            (coupon_id,)).fetchall()
        if len(legs) != LEGS_PER_DOUBLE:
            logging.error("Nie podmieniam %s: nieprawidłowa liczba spotkań", coupon_id)
            continue
        graphic_legs = [
            {"event_id": event_id, "league": league, "home": home, "away": away,
             "start_ts": int(start_ts), "line": float(line), "odds": float(odds)}
            for event_id, league, home, away, start_ts, line, odds in legs
        ]
        graphic = render_coupon_image(
            graphic_legs, float(total_odds), STAKE, coupon_no=index,
            watermark_id=publication_code(coupon_id),
        )
        media = json.dumps({"type": "photo", "media": "attach://photo"})
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/editMessageMedia",
                data={"chat_id": chat_id, "message_id": int(message_id), "media": media},
                files={"photo": ("rogi-ft-dubel.png", graphic, "image/png")},
                timeout=45,
            )
            response.raise_for_status()
            result = response.json()
            if not result.get("ok"):
                logging.error("Telegram editMessageMedia dla %s: %s", coupon_id, result)
                continue
            c.execute("""INSERT OR REPLACE INTO ft_double_reposts
                (coupon_id,sent_at,message_id,graphic_version) VALUES(?,?,?,?)""",
                (coupon_id, now.astimezone(TZ).isoformat(), int(message_id), GRAPHIC_VERSION))
            c.commit()
            replaced += 1
            logging.info("Zastąpiono grafikę w istniejącym poście: coupon=%s message_id=%s",
                         coupon_id, message_id)
        except Exception:
            logging.exception("Nie udało się podmienić grafiki dubla %s", coupon_id)
    logging.info("Podmienione istniejące grafiki: %s/%s", replaced, len(rows))

def clear_today_history_and_scan(c, now):
    """Delete today's FT coupon posts, erase today's FT history and publish a fresh daily batch."""
    day = now.date().isoformat()
    coupon_ids = [row[0] for row in c.execute(
        "SELECT coupon_id FROM ft_doubles WHERE day=?", (day,)
    ).fetchall()]

    # Collect both original coupon posts and any tracked reposts before deleting DB rows.
    message_ids = set()
    for coupon_id in coupon_ids:
        proof = c.execute(
            "SELECT telegram_message_id FROM ft_publication_proofs WHERE coupon_id=?",
            (coupon_id,),
        ).fetchone()
        if proof and proof[0]:
            message_ids.add(int(proof[0]))
        repost = c.execute(
            "SELECT message_id FROM ft_double_reposts WHERE coupon_id=?",
            (coupon_id,),
        ).fetchone()
        if repost and repost[0]:
            message_ids.add(int(repost[0]))

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    deleted = 0
    for message_id in sorted(message_ids):
        response = requests.post(
            f"https://api.telegram.org/bot{token}/deleteMessage",
            data={"chat_id": chat_id, "message_id": message_id},
            timeout=20,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.ok and payload.get("ok"):
            deleted += 1
            continue
        description = str(payload.get("description") or response.text or "")
        # A post already manually removed is fine; other errors abort before DB history is erased.
        if payload.get("error_code") == 400 and "message to delete not found" in description.lower():
            logging.info("Post Telegram %s już nie istnieje — pomijam.", message_id)
            continue
        raise RuntimeError(
            f"Nie udało się usunąć posta Telegram message_id={message_id}: "
            f"HTTP {response.status_code} {description[:500]}"
        )

    # Remove today's FT records only. BTTS and all other dates are untouched.
    c.execute("DELETE FROM ft_double_legs WHERE coupon_id IN (SELECT coupon_id FROM ft_doubles WHERE day=?)", (day,))
    c.execute("DELETE FROM ft_double_reposts WHERE coupon_id IN (SELECT coupon_id FROM ft_doubles WHERE day=?)", (day,))
    c.execute("DELETE FROM ft_publication_proofs WHERE coupon_id IN (SELECT coupon_id FROM ft_doubles WHERE day=?)", (day,))
    c.execute("DELETE FROM ft_doubles WHERE day=?", (day,))
    c.execute("DELETE FROM ft_double_daily_reports WHERE day=?", (day,))
    c.execute(
        "INSERT INTO ft_daily_quota_resets(day,reset_at) VALUES(?,?) "
        "ON CONFLICT(day) DO UPDATE SET reset_at=excluded.reset_at",
        (day, now.isoformat()),
    )
    c.commit()
    logging.info(
        "WYCZYSZCZONO ROGI FT za %s: usunięte posty Telegram=%s, rekordy kuponów=%s. "
        "Historia innych dni i BTTS pozostaje bez zmian.",
        day, deleted, len(coupon_ids),
    )
    send_new_doubles(c, now)


def reset_today_pending_and_scan(c, now):
    """Remove today's unsettled coupons only, then build fresh picks."""
    day = now.date().isoformat()
    old_ids = [row[0] for row in c.execute(
        "SELECT coupon_id FROM ft_doubles WHERE day=? AND status='PENDING'", (day,)
    ).fetchall()]
    for coupon_id in old_ids:
        c.execute("DELETE FROM ft_double_legs WHERE coupon_id=?", (coupon_id,))
        c.execute("DELETE FROM ft_double_reposts WHERE coupon_id=?", (coupon_id,))
        c.execute("DELETE FROM ft_publication_proofs WHERE coupon_id=?", (coupon_id,))
        c.execute("DELETE FROM ft_doubles WHERE coupon_id=? AND status='PENDING'", (coupon_id,))
    c.commit()
    logging.info("Reset: usunięto dzisiejsze nierozliczone duble: %s; rozliczone archiwum zachowane", len(old_ids))
    send_new_doubles(c, now)


def send_coupon_reply(c, coupon_id, message):
    """Reply directly under the original Telegram coupon photo."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    row = c.execute(
        "SELECT telegram_message_id FROM ft_publication_proofs WHERE coupon_id=?",
        (coupon_id,),
    ).fetchone()
    if not row or not row[0]:
        logging.error("Brak oryginalnego message_id dla dubla %s — nie wysyłam luźnego rozliczenia", coupon_id)
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": message,
                "reply_to_message_id": int(row[0]),
                "allow_sending_without_reply": "false",
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            logging.error("Telegram nie przyjął odpowiedzi do dubla %s: %s", coupon_id, payload)
            return False
        c.execute("UPDATE ft_doubles SET settlement_reply_sent=1 WHERE coupon_id=?", (coupon_id,))
        c.commit()
        logging.info("Rozliczenie dubla %s wysłane jako odpowiedź do message_id=%s",
                     coupon_id, row[0])
        return True
    except Exception:
        logging.exception("Nie udało się odpowiedzieć pod oryginalnym dublem %s", coupon_id)
        return False


def manual_user_confirmed_ft_result(home, away, start_ts):
    """User-confirmed outcome for the four fixtures discussed on 18 Nov 2026.
    Stores no invented corner count; it only records the user's confirmed leg outcome.
    """
    local_day = datetime.fromtimestamp(int(start_ts), TZ).date().isoformat()
    if local_day != "2026-11-18":
        return None
    pairs = [
        (("roan united", "chirundu united"), "WON"),
        (("piacenza", "lucchese"), "WON"),
        (("ancona", "ternana"), "LOST"),
        (("virtus francavilla", "turris"), "WON"),
    ]
    h = str(home or "").casefold()
    a = str(away or "").casefold()
    for (first, second), result in pairs:
        if ((first in h and second in a) or (second in h and first in a)):
            return result
    return None


def manual_confirmed_ft_corner_total(home, away, start_ts):
    """Confirmed historical FT totals for fixtures verified against match-stat pages."""
    day = datetime.fromtimestamp(int(start_ts), TZ).date().isoformat()
    h = str(home or "").casefold()
    a = str(away or "").casefold()
    fixtures = [
        # TotalCorner: U21 International, 17 Nov 2026.
        ("2026-11-17", ("croatia u21", "chorwacja u21"), ("germany u21", "niemcy u21"), 8),
        ("2026-11-17", ("serbia u21",), ("poland u21", "polska u21"), 10),
        # TotalCorner: International Match, 17 Nov 2026.
        ("2026-11-18", ("ecuador", "ekwador"), ("bolivia", "boliwia"), 13),
        # InplayFlux / match statistics: 7 + 2 corners.
        ("2026-11-18", ("el salvador", "salwador"), ("saint martin",), 9),
        # TotalCorner: Colombia Cup, local Warsaw date 18 Nov 2026.
        ("2026-11-18", ("atletico nacional",), ("once caldas",), 8),
        ("2026-11-18", ("america de cali", "américa de cali"), ("internacional de bogota", "internacional de bogotá"), 5),
    ]
    for fixture_day, home_aliases, away_aliases, total in fixtures:
        if day != fixture_day:
            continue
        direct = any(x in h for x in home_aliases) and any(x in a for x in away_aliases)
        reverse = any(x in a for x in home_aliases) and any(x in h for x in away_aliases)
        if direct or reverse:
            return total
    return None


def retry_settled_notifications(c):
    """Publish computed settlement figures from SQLite, never from poster artwork."""
    pending = c.execute("""SELECT coupon_id,total_odds,stake,status,profit
        FROM ft_doubles
        WHERE status IN ('WON','LOST')
          AND settlement_reply_sent=0
        ORDER BY settled_at""").fetchall()
    for coupon_id, total_odds, stake, final, profit in pending:
        leg_rows = c.execute("""SELECT league,home,away,line,odds,ft_corners,status
            FROM ft_double_legs WHERE coupon_id=? ORDER BY start_ts""", (coupon_id,)).fetchall()
        if len(leg_rows) != LEGS_PER_DOUBLE:
            continue
        details = "\\n".join(
            f"• {home} – {away}: OVER {float(line):.1f} @ {float(odds):.2f} | "
            f"rożne FT: {corners if corners is not None else 'brak danych'} | {status}"
            for league, home, away, line, odds, corners, status in leg_rows
        )
        message = (
            "📊 ROZLICZENIE DUBLA — ROGI FT\\n\\n" + details +
            f"\\n\\n💹 Kurs łączny: {float(total_odds):.2f}"
            f"\\n💰 Stawka: {float(stake):.2f} zł"
            f"\\n{'🟢 DUBEL WYGRANY' if final == 'WON' else '🔴 DUBEL PRZEGRANY'}"
            f"\\n🏦 Wynik: {float(profit or 0):+.2f} zł"
        )
        if send_coupon_reply(c, coupon_id, message):
            # Settlement is a text reply containing database-calculated figures.
            # Mark the legacy graphic flag complete so old poster-based retries stop.
            c.execute("UPDATE ft_doubles SET settlement_graphic_sent=1 WHERE coupon_id=?", (coupon_id,))
            c.commit()


def settle_doubles(c, now):
    pending = c.execute("""SELECT event_id,coupon_id,league,home,away,start_ts,line,odds
        FROM ft_double_legs WHERE status='PENDING' ORDER BY start_ts""").fetchall()
    now_ts = int(now.timestamp())
    for event_id, coupon_id, league, home, away, start_ts, line, odds in pending:
        # Mecz standardowo kończy się po ok. 105 minutach od kickoffu
        # (90 minut + przerwa + doliczony czas). Od 110. minuty sprawdzamy
        # wynik co 5 minut; po publikacji statystyk rozliczamy od razu.
        # Nie czekamy już 3 godzin i nie rozliczamy przed końcem spotkania.
        if now_ts < int(start_ts) + 110 * 60:
            continue
        # Wyłącznie automatyczne źródła statystyk meczowych — nigdy plakat ani
        # ręcznie wpisane wyniki. Brak potwierdzonej liczby rożnych = oczekuje.
        total = source.cornerflick_ft_corner_total(league, home, away, start_ts)
        result_source = "Cornerflick"
        if total is None:
            total = source.sofascore_ft_corner_total(league, home, away, start_ts)
            if total is not None:
                result_source = "SofaScore"
        if total is None:
            total = source.matchday_ft_corner_total(league, home, away, start_ts)
            if total is not None:
                result_source = "Open Matchday"
        if total is None:
            logging.info("Brak potwierdzonych danych FT — pozostaje PENDING: %s - %s (%s)",
                         home, away, league)
            continue
        result = "WON" if int(total) > float(line) else "LOST"
        c.execute("""UPDATE ft_double_legs SET status=?,ft_corners=? WHERE event_id=?""",
                  (result, int(total) if total is not None else None, event_id))
        c.commit()
        logging.info("Rozliczono nogę %s - %s: %s | źródło: %s%s",
                     home, away, result, result_source,
                     f" | {total} rożnych" if total is not None else "")

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
        leg_rows = c.execute("""SELECT league,home,away,start_ts,line,odds,ft_corners,status
            FROM ft_double_legs WHERE coupon_id=? ORDER BY start_ts""", (coupon_id,)).fetchall()
        details = "\n".join(
            f"• {home} – {away}: OVER {line:.1f}, "
            f"{'rożne: ' + str(corners) if corners is not None else 'wynik potwierdzony ręcznie'}, {status}"
            for league, home, away, start_ts, line, odds, corners, status in leg_rows
        )
        message = (
            "📊 ROZLICZENIE DUBLA — ROGI FT\n\n" + details +
            f"\n\n💹 Kurs łączny: {float(total_odds):.2f}\n"
            f"💰 Stawka: {float(stake):.2f} zł\n"
            f"{'🟢 DUBEL WYGRANY' if final == 'WON' else '🔴 DUBEL PRZEGRANY'}\n"
            f"🏦 Wynik: {profit:+.2f} zł"
        )
        settled_legs = [
            {"league": league, "home": home, "away": away, "start_ts": int(start_ts), "line": float(line), "odds": float(odds)}
            for league, home, away, start_ts, line, odds, corners, status in leg_rows
        ]
        # Rozliczenie wysyłamy wyłącznie jako grafikę; bez tekstowego podpisu pod typem.
        # Kolejka ponawia wysyłkę grafiki, jeśli Telegram chwilowo nie odpowiada.

    retry_settled_notifications(c)

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


def send_overall_report(c, now):
    """Send a cumulative summary of all FT doubles and their yield."""
    total, wins, losses, pending, profit, settled_stake = c.execute(
        """SELECT COUNT(*),
                  SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN status='PENDING' THEN 1 ELSE 0 END),
                  COALESCE(SUM(CASE WHEN status IN ('WON','LOST') THEN profit ELSE 0 END),0),
                  COALESCE(SUM(CASE WHEN status IN ('WON','LOST') THEN stake ELSE 0 END),0)
           FROM ft_doubles"""
    ).fetchone()
    legs = c.execute("SELECT COUNT(*) FROM ft_double_legs").fetchone()[0]
    wins, losses, pending = int(wins or 0), int(losses or 0), int(pending or 0)
    settled = wins + losses
    profit = round(float(profit or 0), 2)
    settled_stake = float(settled_stake or 0)
    yield_pct = (profit / settled_stake * 100.0) if settled_stake else 0.0
    report = (
        "📊 PODSUMOWANIE ŁĄCZNE — ROGI FT\n"
        f"🎟️ Wszystkie duble: {int(total or 0)}\n"
        f"⚽ Łącznie typów meczowych: {int(legs or 0)}\n"
        f"🟢 Wygrane duble: {wins}\n"
        f"🔴 Przegrane duble: {losses}\n"
        f"⏳ Oczekujące duble: {pending}\n"
        f"📈 Rozliczone duble: {settled}\n"
        f"💰 Stawka za dubel: {STAKE:.2f} zł\n"
        f"🏦 ZYSK / STRATA: {profit:+.2f} zł\n"
        f"📐 YIELD: {yield_pct:+.2f}%"
    )
    if send(report):
        logging.info("Wysłano łączne podsumowanie FT: duble=%s, W=%s, L=%s, pending=%s, profit=%+.2f zł, yield=%+.2f%%",
                     int(total or 0), wins, losses, pending, profit, yield_pct)
        return True
    logging.error("Nie udało się wysłać łącznego podsumowania FT")
    return False


def clear_all_history(c):
    """Permanently clear all stored FT-double history while retaining the DB schema."""
    tables = (
        "ft_double_legs",
        "ft_double_reposts",
        "ft_publication_proofs",
        "ft_double_daily_reports",
        "ft_daily_quota_resets",
        "ft_doubles",
    )
    c.execute("PRAGMA foreign_keys=OFF")
    for table in tables:
        c.execute(f"DELETE FROM {table}")
    c.commit()
    logging.info("USUNIĘTO CAŁĄ ZAPISANĄ HISTORIĘ ROGÓW FT: typy, nogi, dowody publikacji, rozliczenia, raporty i limity. Baza gotowa od zera.")


def main():
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        raise RuntimeError("Brak TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID w GitHub Secrets")
    logging.info("ROGI FT: aktywny zakres kursów %.2f–%.2f; limit %s dubli/dzień.",
                 MIN_LEG_ODDS, MAX_LEG_ODDS, MAX_DOUBLES_PER_DAY)
    c = db_open()
    try:
        now = datetime.now(TZ)
        mode = os.getenv("BOT_MODE", "scan").strip().lower()
        if mode == "clear-history":
            clear_all_history(c)
        elif mode in {"summary", "settle"}:
            logging.warning("Rozliczanie jest wyłączone. Bot wyłącznie wyszukuje i publikuje nowe duble.")
            return
        elif mode == "repost-pending":
            repost_pending_doubles(c, now)
        elif mode == "replace-graphics":
            replace_today_pending_graphics(c, now)
        elif mode == "reset-limit":
            reset_daily_quota(c, now)
        elif mode == "reset-today-and-scan":
            reset_today_pending_and_scan(c, now)
        elif mode == "clear-today-and-scan":
            clear_today_history_and_scan(c, now)
        elif mode == "dry-run":
            used = {int(row[0]) for row in c.execute("SELECT event_id FROM ft_double_legs").fetchall()}
            candidates = [x for x in fetch_candidates(now) if x["event_id"] not in used]
            logging.info("DRY RUN: wolne zdarzenia po filtrze duplikatów: %s", len(candidates))
            for item in candidates[:20]:
                kickoff = datetime.fromtimestamp(item["start_ts"], timezone.utc).astimezone(TZ)
                logging.info("DRY RUN: %s | %s - %s | OVER %.1f FT @%.2f | %s",
                             item["league"], item["home"], item["away"], item["line"],
                             item["odds"], kickoff.isoformat())
        else:
            # Automatyczny i zwykły tryb scan tylko szuka oraz publikuje nowe duble.
            # Nie sprawdza wyników i nie rozlicza kuponów.
            send_new_doubles(c, now)
    finally:
        c.close()


if __name__ == "__main__":
    main()

# Bot FT: wyłącznie wyszukiwanie i publikacja nowych dubli.

# Clear all previous FT picks from persistent SQLite history [clear-history-ft].

# Reset dziennego limitu FT na wyraźne żądanie użytkownika.

# Manual user request: reset today's FT corner quota and immediately scan for fresh doubles. [reset-limit-ft]

# Manual request: reset today's FT daily quota only; keep all published picks and settlement history.

# Operational reset trigger: preserve published history and reset only today's quota.

# Retry quota reset trigger after prior queued run was cancelled.

# Reset dziennego limitu na prośbę użytkownika — 2026-11-20.

# Retry daily FT quota reset after disabling duplicate legacy workflow.

# Ręczne ponowienie resetu dziennego limitu na żądanie użytkownika — 2026-11-20.

# Ręczny reset dziennego limitu FT na żądanie użytkownika — 2026-11-20 (ponowienie).

# Ręczne żądanie użytkownika: wyczyść całą historię typów FT i limity. [clear-history-ft]

# Uruchom skan FT po wyczyszczeniu historii i limitów. [publish-ft]

# Ręczne uruchomienie skanu publikującego po poprawieniu testów kursów [publish-ft]

# Ręczne żądanie użytkownika 2026-11-24: wyczyść wyłącznie historię w bazie FT i limity; nie usuwaj postów Telegram. [clear-history-ft]

# Manual user request 2026-11-24: reset FT quota only; preserve Telegram messages and stored pick history. [reset-limit-ft]

# Retry quota-only reset after allowing reset workflow to bypass unrelated graphic tests. [reset-limit-ft]
