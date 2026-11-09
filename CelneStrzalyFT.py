import os, re, sqlite3, logging, unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

API = "https://production-superbet-offer-pl.freetls.fastly.net"
TZ = ZoneInfo("Europe/Warsaw")
DB = "celne_strzaly_ft.db"
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")
MAX_DAILY = 10
MIN_ODDS = 1.70
MAX_ODDS = 2.50
PREFERRED_LINES = (2.5, 3.5, 4.5)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
    "Accept": "application/json,*/*",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    "Origin": "https://superbet.pl",
    "Referer": "https://superbet.pl/",
})

def api(path, params=None):
    response = session.get(API + path, params=params, timeout=35)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}

def event_list(start, end):
    # Superbet wymaga zakresów zaokrąglonych do pełnych godzin UTC.
    start_utc = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    end_utc = end.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    if end.astimezone(timezone.utc) > end_utc:
        end_utc += timedelta(hours=1)
    payload = api("/v3/pl-PL/events", {
        "startDate": start_utc.isoformat().replace("+00:00", "Z"),
        "endDate": end_utc.isoformat().replace("+00:00", "Z"),
    })
    events = payload.get("events") or payload.get("data") or []
    if isinstance(events, dict):
        events = list(events.values())
    return [x for x in events if isinstance(x, dict)]

def event_detail(event_id):
    payload = api("/v3/pl-PL/events", {"events": str(event_id)})
    events = payload.get("events") or payload.get("data") or []
    if isinstance(events, dict):
        events = list(events.values())
    if not isinstance(events, list):
        return None
    return next((x for x in events if str(x.get("event_id") or x.get("id")) == str(event_id)), None)

def normalize(value):
    # Litera ł nie rozkłada się w NFKD, więc mapujemy ją jawnie.
    raw = str(value or "").lower().replace("ł", "l")
    value = unicodedata.normalize("NFKD", raw)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", value)

def text_fields(obj):
    """Zbiera etykiety zarówno z rynku, jak i z selekcji Superbet."""
    meta = obj.get("metadata") or {}
    values = [
        obj.get("name"), obj.get("label"), obj.get("market_name"), obj.get("marketName"),
        obj.get("info"), obj.get("tags"),
        meta.get("name"), meta.get("info"), meta.get("label"), meta.get("team"),
        meta.get("participant"), meta.get("competitor"), meta.get("tags"),
    ]
    for key in ("extra", "specifiers"):
        nested = meta.get(key)
        if isinstance(nested, dict):
            values.extend(nested.values())
    return " ".join(str(v or "") for v in values if not isinstance(v, (dict, list)))

def is_active(obj):
    status = str(obj.get("status") or "").upper()
    return status not in {"0", "SUSPENDED", "CLOSED", "INACTIVE", "DISABLED", "HIDDEN"} and obj.get("display") is not False

def over_line(label):
    raw = str(label or "").lower().replace(",", ".")
    match = re.search(r"(?:over|above|more than|powyzej|ponad|wiecej|\+)\s*(\d+(?:\.\d+)?)", raw)
    if not match:
        match = re.search(r"(\d+(?:\.\d+)?)\s*\+", raw)
    if not match:
        return None
    try:
        line = float(match.group(1))
        return line if line in PREFERRED_LINES else None
    except ValueError:
        return None

def team_shots_market(event, home, away):
    picks = []
    diagnostics = []
    for market in event.get("markets") or []:
        market_name = text_fields(market)
        outcomes = market.get("odds") or []
        # Superbet nie zawsze umieszcza określenie "celne" w nazwie rynku.
        # Czasem jest ono dopiero w opisie selekcji (metadata.info).
        outcome_labels = " ".join(text_fields(x) for x in outcomes if isinstance(x, dict))
        market_blob = market_name + " " + outcome_labels
        compact = normalize(market_blob)
        has_shot_term = (
            ("strzal" in compact and "celn" in compact)
            or ("strzal" in compact and "bramk" in compact)
            or any(term in compact for term in (
                "shotsontarget", "shotsongoal", "teamshotsontarget",
                "teamshotsongoal", "ontarget"
            ))
        )
        if not has_shot_term:
            continue
        # Odrzucamy rynki HT/1. połowy oraz rynki zawodników.
        if any(term in compact for term in (
            "1polowa", "pierwszopol", "firsthalf", "1sthalf", "zawodnik", "player"
        )):
            continue
        for outcome in outcomes:
            if not is_active(outcome):
                continue
            outcome_text = text_fields(outcome)
            combined = normalize(market_name + " " + outcome_text)
            line = over_line(market_name + " " + outcome_text)
            if line is None:
                continue
            # Wymagamy jednoznacznego przypisania rynku do jednej z drużyn.
            home_key, away_key = normalize(home), normalize(away)
            meta = outcome.get("metadata") or {}
            market_meta = market.get("metadata") or {}
            team_hint = normalize(" ".join(str(v or "") for v in (
                meta.get("team"), meta.get("participant"), meta.get("competitor"),
                market_meta.get("team"), market_meta.get("participant"), market_meta.get("competitor")
            )))
            home_match = (home_key and home_key in combined) or (home_key and home_key in team_hint)
            away_match = (away_key and away_key in combined) or (away_key and away_key in team_hint)
            if home_match == away_match:
                continue
            try:
                price = float(outcome.get("price") or meta.get("price"))
            except (TypeError, ValueError):
                continue
            if not MIN_ODDS <= price <= MAX_ODDS:
                continue
            team = home if home_match else away
            picks.append({
                "team": team, "line": line, "odds": price,
                "market": str(market.get("name") or market.get("label") or "Celne strzały drużyny FT")
            })
        if len(diagnostics) < 4:
            diagnostics.append(market_name[:180])
    if not picks:
        return None, diagnostics
    # Preferujemy wyższą linię przy zbliżonym kursie; kurs nadal musi być realnie dostępny.
    picks.sort(key=lambda x: (abs(x["odds"] - 2.00), -x["line"]))
    return picks[0], diagnostics

def send(message):
    if not TOKEN or not CHAT:
        logging.error("Brak sekretów TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        return False
    response = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT, "text": message, "disable_web_page_preview": True},
        timeout=25
    )
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
    # Przeszukujemy najbliższe 48 godzin, ale limit 10 typów liczymy osobno
    # dla bieżącego dnia w strefie Europe/Warsaw.
    start = now
    today_end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    tomorrow_end = today_end + timedelta(days=1)
    connection = sqlite3.connect(DB)
    connection.execute("""CREATE TABLE IF NOT EXISTS sent_shots_ft(
        event_id TEXT PRIMARY KEY, sent_at TEXT NOT NULL, home TEXT, away TEXT,
        team TEXT, line REAL, odds REAL, kickoff TEXT
    )""")
    sent = {row[0] for row in connection.execute(
        "SELECT event_id FROM sent_shots_ft WHERE substr(sent_at,1,10)=?", (day,)
    )}
    logging.info("Celne strzały FT: wysłane dziś %d/%d", len(sent), MAX_DAILY)
    if len(sent) >= MAX_DAILY:
        logging.info("Osiągnięto dzienny limit")
        connection.close()
        return

    # Superbet odrzuca zakresy dłuższe niż doba, dlatego pobieramy
    # pozostałą część dzisiaj i jutro jako dwa osobne zapytania.
    events = event_list(start, today_end)
    events.extend(event_list(today_end, tomorrow_end))
    unique_events = {}
    for event in events:
        event_id = str(event.get("event_id") or event.get("id") or "")
        if event_id:
            unique_events[event_id] = event
    events = list(unique_events.values())
    candidates = []
    for event in events:
        fixture = event.get("fixture") or {}
        if str(fixture.get("sport_id", event.get("sport_id", "5"))) != "5":
            continue
        phase = str((event.get("inplay_stats_metadata") or {}).get("status") or "").upper()
        if phase not in ("", "NOT_STARTED"):
            continue
        event_id = str(event.get("event_id") or event.get("id") or "")
        if not event_id or event_id in sent:
            continue
        name = str(fixture.get("event_name") or "")
        parts = re.split(r"\s*[·–-]\s*", name, maxsplit=1)
        if len(parts) != 2:
            continue
        home, away = (x.strip() for x in parts)
        try:
            kickoff = datetime.fromisoformat(str(fixture.get("utc_date")).replace("Z", "+00:00"))
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if kickoff <= datetime.now(timezone.utc):
            continue
        candidates.append((event_id, home, away, kickoff, event))
    logging.info("Superbet: %d meczów przedmeczowych do sprawdzenia", len(candidates))

    def inspect(item):
        event_id, home, away, kickoff, listed = item
        detail = event_detail(event_id) or listed
        if not (detail.get("markets") or []):
            detail = listed
        pick, diagnostics = team_shots_market(detail, home, away)
        return (item, pick, diagnostics)

    qualified = []
    market_count = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(inspect, item) for item in candidates]
        for future in as_completed(futures):
            try:
                item, pick, diagnostics = future.result()
                if diagnostics:
                    market_count += len(diagnostics)
                if pick:
                    qualified.append((item, pick))
            except Exception:
                logging.exception("Błąd analizy rynku celnych strzałów")
    qualified.sort(key=lambda pair: (abs(pair[1]["odds"] - 2.00), -pair[1]["line"], pair[0][3]))
    selected = qualified[:max(0, MAX_DAILY - len(sent))]
    logging.info("Rynki celnych strzałów FT rozpoznane: %d; typy spełniające kurs: %d; wybrane: %d",
                 market_count, len(qualified), len(selected))

    for item, pick in selected:
        event_id, home, away, kickoff, _ = item
        # Ostatnia kontrola świeżego rynku i kursu bezpośrednio przed publikacją.
        fresh = event_detail(event_id)
        if not fresh:
            continue
        current, _ = team_shots_market(fresh, home, away)
        if not current or current["team"] != pick["team"] or current["line"] != pick["line"]:
            logging.info("Pominięto %s: rynek zniknął lub zmienił się", event_id)
            continue
        if not MIN_ODDS <= current["odds"] <= MAX_ODDS:
            continue
        number = len(sent) + 1
        local_kickoff = kickoff.astimezone(TZ)
        link = f"https://superbet.pl/zaklady-bukmacherskie/pilka-nozna/{event_id}"
        message = (
            "🎯 CELNE STRZAŁY DRUŻYNY — FT\n"
            f"🔢 TYP NR.{number}\n"
            f"📅 {local_kickoff:%d.%m.%Y} | 🕒 {local_kickoff:%H:%M}\n\n"
            f"⚔️ {home} – {away}\n"
            f"🏹 Drużyna: {current['team']}\n"
            f"📌 Typ: {current['team']} — over {current['line']:.1f} celnych strzałów (FT)\n"
            f"💹 Kurs Superbet: {current['odds']:.2f}\n"
            f"🔗 Otwórz mecz w Superbet: {link}"
        )
        if send(message):
            connection.execute(
                "INSERT OR IGNORE INTO sent_shots_ft VALUES(?,?,?,?,?,?,?,?)",
                (event_id, now.isoformat(), home, away, current["team"],
                 current["line"], current["odds"], local_kickoff.isoformat())
            )
            connection.commit()
            sent.add(event_id)
            logging.info("Wysłano TYP NR.%d: %s - %s | %s over %.1f | kurs %.2f",
                         number, home, away, current["team"], current["line"], current["odds"])
        else:
            logging.error("Telegram nie potwierdził wysłania eventu %s", event_id)
    connection.close()

if __name__ == "__main__":
    main()
