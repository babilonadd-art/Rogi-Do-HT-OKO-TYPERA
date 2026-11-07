import os, re, sqlite3, logging
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

SOURCE_URL = "https://www.iddaaorantahmin.com/iddaa-oran-analiz/kg-var-analizi"
TZ = ZoneInfo("Europe/Warsaw")
SOURCE_TZ = ZoneInfo("Europe/Istanbul")
DB = "okotypera_rogi.db"
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")
MIN_ODDS = 1.70
MAX_ODDS = 2.60
MAX_PICKS = 3
STAKE = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
http = requests.Session()
http.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
    "Referer": "https://www.iddaaorantahmin.com/"
})

def fetch_rows():
    response = http.get(SOURCE_URL, timeout=35)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    parsed = []
    for row in soup.select("table tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) < 5:
            continue
        texts = [c.get_text(" ", strip=True) for c in cells]
        joined = " | ".join(texts)
        date_match = re.search(r"(\d{1,2}:\d{2})\s+(\d{4}-\d{2}-\d{2})", joined)
        if not date_match:
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})", joined)
            if date_match:
                date_text, time_text = date_match.group(1), date_match.group(2)
            else:
                continue
        else:
            time_text, date_text = date_match.group(1), date_match.group(2)
        percent = None
        for value in texts:
            pm = re.search(r"(\d{1,3})\s*%", value)
            if pm:
                percent = int(pm.group(1))
                break
        odds = None
        for value in reversed(texts):
            om = re.fullmatch(r"\s*(\d{1,2}(?:[.,]\d{1,2})?)\s*", value)
            if om:
                try:
                    odds = float(om.group(1).replace(",", "."))
                    break
                except ValueError:
                    pass
        if percent is None or odds is None or not (MIN_ODDS <= odds <= MAX_ODDS):
            continue
        match_link = None
        match_name = ""
        for a in row.find_all("a", href=True):
            label = a.get_text(" ", strip=True)
            if re.search(r"\s[-–—]\s", label) and len(label) > 5:
                match_link = urljoin(SOURCE_URL, a["href"])
                match_name = label
                break
        if not match_name:
            for value in texts:
                if re.search(r"\s[-–—]\s", value) and len(value) > 5:
                    match_name = value
                    break
        if not match_name:
            continue
        try:
            source_start = datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M").replace(tzinfo=SOURCE_TZ)
            start = source_start.astimezone(TZ)
        except ValueError:
            continue
        if start <= datetime.now(TZ):
            continue
        key_url = match_link or (match_name + "|" + start.isoformat())
        parsed.append({
            "key": key_url,
            "home_away": match_name,
            "start": start,
            "odds": odds,
            "percent": percent,
            "url": match_link or SOURCE_URL
        })
    logging.info("Iddaa KG VAR: znaleziono %d przedmeczowych wierszy z kursem %.2f–%.2f", len(parsed), MIN_ODDS, MAX_ODDS)
    return parsed

def send(message):
    if not TOKEN or not CHAT:
        raise RuntimeError("Brak sekretów Telegrama")
    response = requests.post(
        "https://api.telegram.org/bot" + TOKEN + "/sendMessage",
        data={"chat_id": CHAT, "text": message, "disable_web_page_preview": True},
        timeout=20
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError("Telegram nie potwierdził wysłania wiadomości")
    return True

def main():
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS prematch_btts_sent (
        pick_key TEXT PRIMARY KEY, event_id TEXT NOT NULL, sent_at TEXT NOT NULL,
        home TEXT NOT NULL, away TEXT NOT NULL, odds REAL NOT NULL, kickoff TEXT NOT NULL
    )""")
    conn.commit()
    try:
        candidates = fetch_rows()
        candidates.sort(key=lambda x: (-x["percent"], x["start"]))
        picks = []
        for item in candidates:
            if conn.execute("SELECT 1 FROM prematch_btts_sent WHERE pick_key=?", (item["key"],)).fetchone():
                continue
            picks.append(item)
            if len(picks) >= MAX_PICKS:
                break
        logging.info("BTTS Iddaa: kandydaci=%d, wybrane=%d", len(candidates), len(picks))
        # Re-read the public source immediately before sending to avoid stale or started matches.
        fresh_rows = {x["key"]: x for x in fetch_rows()}
        for item in picks:
            fresh = fresh_rows.get(item["key"])
            if not fresh or fresh["start"] <= datetime.now(TZ):
                logging.info("Pominięto nieaktualny/rozpoczęty mecz: %s", item["home_away"])
                continue
            message = (
                "🟡 PRZEDMECZOWE BTTS — IDDAA\n"
                f"⚽ {fresh['home_away']}\n"
                f"🕒 Start: {fresh['start']:%d.%m.%Y %H:%M}\n"
                "📌 Typ: BTTS — TAK (KG VAR)\n"
                f"📊 Procent Iddaa: {fresh['percent']}%\n"
                f"💹 Kurs Iddaa: {fresh['odds']:.2f}\n"
                f"💰 Stawka: {STAKE} zł\n"
                f"🔗 Mecz: {fresh['url']}"
            )
            if send(message):
                conn.execute(
                    "INSERT OR IGNORE INTO prematch_btts_sent VALUES(?,?,?,?,?,?,?)",
                    (fresh["key"], fresh["key"], datetime.now(TZ).isoformat(),
                     fresh["home_away"], "", fresh["odds"], fresh["start"].isoformat())
                )
                conn.commit()
                logging.info("Wysłano BTTS Iddaa: %s @ %.2f (%d%%)", fresh["home_away"], fresh["odds"], fresh["percent"])
    finally:
        conn.close()

if __name__ == "__main__":
    main()
