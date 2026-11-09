import os
import re
import sqlite3
import logging
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests

API = "https://moviodds.com"
GROUP_ID = "50530"  # Team Shots on Target — pełny mecz (FT)
TZ = ZoneInfo("Europe/Warsaw")
DB = "celne_strzaly_ft.db"
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")
MOVIODDS_KEY = os.getenv("MOVIODDS_API_KEY", "").strip()
MAX_DAILY = 10
MIN_ODDS = 1.70
MAX_ODDS = 2.50
PREFERRED_LINES = (2.5, 3.5, 4.5)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
session = requests.Session()
session.headers.update({"User-Agent": "OkoTypera-CelneStrzalyFT/1.0", "Accept": "application/json", "Accept-Encoding": "gzip"})
if MOVIODDS_KEY:
    session.headers["X-API-Key"] = MOVIODDS_KEY

def load_board():
    response = session.get(f"{API}/odds", params={"sport": "soccer", "groups": GROUP_ID, "links": "1"}, timeout=45)
    response.raise_for_status()
    payload = response.json()
    matches = payload.get("matches") or []
    if not isinstance(matches, list):
        raise ValueError("MoviOdds zwrócił nieprawidłową listę meczów")
    return matches, payload

def norm(value):
    raw = str(value or "").lower().replace("ł", "l")
    raw = unicodedata.normalize("NFKD", raw)
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", raw)

def parse_line(quote):
    raw = quote.get("line")
    if isinstance(raw, (int, float)):
        return float(raw)
    if raw is not None:
        m = re.search(r"(\d+(?:[.,]\d+)?)", str(raw))
        if m:
            return float(m.group(1).replace(",", "."))
    for field in ("label", "outcome", "name"):
        m = re.search(r"(\d+(?:[.,]\d+)?)", str(quote.get(field) or ""))
        if m:
            return float(m.group(1).replace(",", "."))
    return None

def is_over(quote):
    direction = norm(" ".join(str(quote.get(k) or "") for k in ("side", "label", "outcome", "name")))
    return any(term in direction for term in ("over", "above", "more", "powyzej"))

def team_for_quote(quote, home, away):
    fields = (quote.get("n2"), quote.get("team"), quote.get("participant"), quote.get("competitor"), quote.get("side"), quote.get("label"), quote.get("name"))
    values = [norm(v) for v in fields if v is not None]
    h, a = norm(home), norm(away)
    def matches(team_key):
        return bool(team_key) and any(value == team_key or (len(team_key) >= 5 and team_key in value) for value in values)
    hm, am = matches(h), matches(a)
    if hm == am:
        return None
    return home if hm else away

def get_pick(match):
    if match.get("in_play") is True:
        return None
    try:
        kickoff = datetime.fromisoformat(str(match.get("kickoff_utc")).replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    now_utc = datetime.now(timezone.utc)
    if kickoff <= now_utc or kickoff > now_utc + timedelta(hours=48):
        return None
    home, away = str(match.get("home") or ""), str(match.get("away") or "")
    if not home or not away:
        return None
    picks = []
    for quote in match.get("quotes") or []:
        if str(quote.get("group_id")) != GROUP_ID or quote.get("suspended") is True:
            continue
        if str(quote.get("phase") or "prematch").lower() != "prematch" or not is_over(quote):
            continue
        line = parse_line(quote)
        if line not in PREFERRED_LINES:
            continue
        team = team_for_quote(quote, home, away)
        if not team:
            continue
        try:
            price = float(quote.get("odds_dec"))
        except (TypeError, ValueError):
            continue
        if MIN_ODDS <= price <= MAX_ODDS:
            picks.append({"team": team, "line": line, "odds": price, "quote": quote})
    if not picks:
        return None
    picks.sort(key=lambda x: (abs(x["odds"] - 2.00), -x["line"]))
    return picks[0]

def send(message):
    if not TOKEN or not CHAT:
        logging.error("Brak sekretów TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        return False
    response = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id": CHAT, "text": message, "disable_web_page_preview": True}, timeout=25)
    try:
        data = response.json()
        return bool(response.ok and data.get("ok"))
    except Exception:
        return False

def main():
    if not TOKEN or not CHAT:
        logging.error("Brak sekretów Telegrama")
        return
    now = datetime.now(TZ)
    day = now.date().isoformat()
    connection = sqlite3.connect(DB)
    connection.execute("""CREATE TABLE IF NOT EXISTS sent_shots_ft(event_id TEXT PRIMARY KEY, sent_at TEXT NOT NULL, home TEXT, away TEXT, team TEXT, line REAL, odds REAL, kickoff TEXT)""")
    sent = {row[0] for row in connection.execute("SELECT event_id FROM sent_shots_ft WHERE substr(sent_at,1,10)=?", (day,))}
    logging.info("Celne strzały drużyny FT: wysłane dziś %d/%d", len(sent), MAX_DAILY)
    if len(sent) >= MAX_DAILY:
        logging.info("Osiągnięto dzienny limit")
        connection.close()
        return
    try:
        matches, payload = load_board()
    except Exception:
        logging.exception("Nie udało się pobrać kursów z MoviOdds")
        connection.close()
        return
    logging.info("MoviOdds: %d meczów na tablicy; rynek group_id=%s", len(matches), GROUP_ID)
    candidates, matched_quotes = [], 0
    for match in matches:
        event_id = str(match.get("id") or "")
        if not event_id or event_id in sent:
            continue
        try:
            match_kickoff = datetime.fromisoformat(str(match.get("kickoff_utc")).replace("Z", "+00:00"))
            if match_kickoff.tzinfo is None:
                match_kickoff = match_kickoff.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if match_kickoff <= datetime.now(timezone.utc) or match_kickoff > datetime.now(timezone.utc) + timedelta(hours=48):
            continue
        matched_quotes += sum(1 for q in (match.get("quotes") or []) if str(q.get("group_id")) == GROUP_ID and q.get("suspended") is not True)
        pick = get_pick(match)
        if pick:
            kickoff = datetime.fromisoformat(str(match["kickoff_utc"]).replace("Z", "+00:00"))
            candidates.append((match, pick, kickoff))
    candidates.sort(key=lambda x: (abs(x[1]["odds"] - 2.00), -x[1]["line"], x[2]))
    selected = candidates[:max(0, MAX_DAILY - len(sent))]
    logging.info("Rynki Team Shots on Target FT: %d selekcji; typy w zakresie kursów: %d; wybrane: %d", matched_quotes, len(candidates), len(selected))
    for match, pick, kickoff in selected:
        event_id = str(match["id"])
        try:
            fresh_matches, _ = load_board()
            fresh = next((m for m in fresh_matches if str(m.get("id")) == event_id), None)
        except Exception:
            logging.exception("Nie udało się odświeżyć kursu dla %s", event_id)
            continue
        current = get_pick(fresh) if fresh else None
        if not current or current["team"] != pick["team"] or current["line"] != pick["line"]:
            logging.info("Pominięto %s: rynek zniknął lub zmienił się", event_id)
            continue
        number = len(sent) + 1
        local_kickoff = kickoff.astimezone(TZ)
        links = (fresh or {}).get("links") or {}
        link = links.get("prematch") or links.get("match")
        message = ("🎯 CELNE STRZAŁY DRUŻYNY — FT\n" f"🔢 TYP NR.{number}\n" f"📅 {local_kickoff:%d.%m.%Y} | 🕒 {local_kickoff:%H:%M}\n\n" f"⚔️ {match.get('home')} – {match.get('away')}\n" f"🏹 Drużyna: {current['team']}\n" f"📌 Typ: {current['team']} — over {current['line']:.1f} celnych strzałów (FT)\n" f"💹 Kurs bet365: {current['odds']:.2f}")
        if link:
            message += f"\n🔗 Otwórz mecz: {link}"
        if send(message):
            connection.execute("INSERT OR IGNORE INTO sent_shots_ft VALUES(?,?,?,?,?,?,?,?)", (event_id, now.isoformat(), match.get("home"), match.get("away"), current["team"], current["line"], current["odds"], local_kickoff.isoformat()))
            connection.commit()
            sent.add(event_id)
            logging.info("Wysłano TYP NR.%d: %s - %s | %s over %.1f | kurs %.2f", number, match.get("home"), match.get("away"), current["team"], current["line"], current["odds"])
        else:
            logging.error("Telegram nie potwierdził wysłania eventu %s", event_id)
    connection.close()

if __name__ == "__main__":
    main()