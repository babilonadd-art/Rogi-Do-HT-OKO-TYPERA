import os, time, sqlite3, logging, re, unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests

API="https://www.sofascore.com/api/v1"
API_FALLBACK="https://www.sofascore.com/api/v1"
SUPERBET_API="https://production-superbet-offer-pl.freetls.fastly.net"
TZ=ZoneInfo("Europe/Warsaw")
DB="okotypera_rogi.db"
TOKEN=os.getenv("TELEGRAM_BOT_TOKEN")
CHAT=os.getenv("TELEGRAM_CHAT_ID")
STAKE=50
MAX_DAILY=2
HISTORY=8
MIN_SAMPLE=5
MIN_TEAM_AVG=2.2
MIN_COMBINED_AVG=5.5
MIN_HIT_RATE=0.60
HT_CORNERS_LINE=4.5
MIN_ODDS=1.90
MAX_ODDS=3.50
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
s=requests.Session()
s.headers.update({
    "User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept":"application/json, text/plain, */*",
    "Accept-Language":"pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin":"https://www.sofascore.com",
    "Referer":"https://www.sofascore.com/",
})
sb=requests.Session()
sb.headers.update({
    "User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
    "Accept":"application/json,*/*",
    "Accept-Language":"pl-PL,pl;q=0.9,en;q=0.8",
    "Origin":"https://superbet.pl",
    "Referer":"https://superbet.pl/",
})

def api(path):
    try:
        r=s.get(API+path,timeout=12)
        if r.status_code == 403:
            logging.warning("SofaScore HTTP 403 %s — ponawiam zapytanie",path)
            time.sleep(0.5)
            r=s.get(API+path,headers={"Referer":"https://www.sofascore.com/","Origin":"https://www.sofascore.com","Accept":"application/json, text/plain, */*"},timeout=12)
        if r.status_code in (403,404,429):
            logging.warning("SofaScore HTTP %s %s — brak danych do rozliczenia",r.status_code,path)
            return {}
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logging.warning("SofaScore API %s: %s",path,e)
        return {}

def superbet_api(path,params=None):
    try:
        r=sb.get(SUPERBET_API+path,params=params,timeout=45)
        if r.status_code in (403,404,429):
            logging.warning("Superbet HTTP %s %s",r.status_code,path); return {}
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logging.warning("Superbet API %s: %s",path,e); return {}

def utc_hour(dt,round_up=False):
    value=dt.astimezone(timezone.utc).replace(minute=0,second=0,microsecond=0)
    if round_up and dt.astimezone(timezone.utc)>value:
        value+=timedelta(hours=1)
    return value

def superbet_football_events(start,end):
    start_utc=utc_hour(start)
    end_utc=utc_hour(end,round_up=True)
    payload=superbet_api("/v3/pl-PL/events",{
        "startDate":start_utc.isoformat().replace("+00:00","Z"),
        "endDate":end_utc.isoformat().replace("+00:00","Z"),
    })
    events=payload.get("events",[])
    result=[]
    for event in events:
        fixture=event.get("fixture",{})
        if str(fixture.get("sport_id"))!="5":
            continue
        if fixture.get("offer_state_status",{}).get("1") not in (1,"1"):
            continue
        if event.get("inplay_stats_metadata",{}).get("status") not in ("NOT_STARTED",""):
            continue
        result.append(event)
    logging.info("Superbet: wydarzenia %s, piłka nożna przedmeczowo %s",len(events),len(result))
    return result

def normalized_team(name):
    value=unicodedata.normalize("NFKD",str(name or "")).encode("ascii","ignore").decode().lower()
    tokens=re.findall(r"[a-z0-9]+",value)
    tokens=[t for t in tokens if t not in {"fc","cf","sc","afc","club","the"}]
    return "".join(tokens)

def team_similarity(a,b):
    x=normalized_team(a); y=normalized_team(b)
    if not x or not y: return 0.0
    if x==y: return 1.0
    if x in y or y in x: return max(0.78,min(len(x),len(y))/max(len(x),len(y)))
    return SequenceMatcher(None,x,y).ratio()

def extract_league_name(event):
    """Resolve the competition name from Superbet's event/fixture metadata."""
    fixture=event.get("fixture") or {}
    containers=[fixture,event]
    for parent in list(containers):
        for key in ("tournament","league","competition","championship"):
            value=parent.get(key)
            if isinstance(value,dict):
                containers.append(value)
    for parent in containers:
        for key in ("tournament_name","league_name","competition_name","championship_name"):
            value=parent.get(key)
            if isinstance(value,str) and value.strip():
                return value.strip()
        for key in ("tournament","league","competition","championship"):
            value=parent.get(key)
            if isinstance(value,dict):
                name=value.get("name") or value.get("title") or value.get("label")
                if isinstance(name,str) and name.strip():
                    return name.strip()
    return "Liga niepodana przez Superbet"

def superbet_event_for_match(home,away,start_ts,events):
    best=None
    for event in events:
        fixture=event.get("fixture",{})
        try:
            event_ts=int(datetime.fromisoformat(fixture["utc_date"].replace("Z","+00:00")).timestamp())
        except Exception:
            continue
        if abs(event_ts-int(start_ts))>90*60:
            continue
        names=str(fixture.get("event_name","")).split("·",1)
        if len(names)!=2:
            continue
        direct=(team_similarity(home,names[0]),team_similarity(away,names[1]))
        reverse=(team_similarity(home,names[1]),team_similarity(away,names[0]))
        scores=[direct,reverse]
        hscore,ascore=max(scores,key=lambda pair:pair[0]+pair[1])
        if hscore<0.68 or ascore<0.68:
            continue
        score=hscore+ascore
        if best is None or score>best[0]:
            best=(score,event)
    return best[1] if best else None

def superbet_ht_over_odds(event_id,line=HT_CORNERS_LINE):
    """Fetch and validate the actual Superbet PL pre-match total HT-corners price."""
    payload=superbet_api("/v3/pl-PL/events",{"events":str(event_id)})
    events=payload.get("events",[])
    if not events:
        return None
    event=events[0]
    fixture=event.get("fixture",{})
    # Superbet marks the pre-match offer state as 1; reject started/in-play events.
    offer_states=fixture.get("offer_state_status") or {}
    if offer_states and offer_states.get("1") not in (1,"1"):
        return None
    inplay=(event.get("inplay_stats_metadata") or {}).get("status")
    if inplay not in (None,"","NOT_STARTED"):
        return None
    for market in event.get("markets",[]):
        market_name=re.sub(r"\s+"," ",str(market.get("name","")).strip().lower())
        # This is the match total market, not either team's individual corner total.
        if not re.fullmatch(r"1\.?\s*połowa\s*-\s*liczba rzutów rożnych",market_name):
            continue
        if market.get("status") not in (None,1,"1","ACTIVE","active"):
            continue
        for odd in market.get("odds",[]):
            meta=odd.get("metadata") or {}
            try:
                odd_line=float(meta.get("special_bet_value",odd.get("special_bet_value")))
                price=float(odd.get("price"))
            except (TypeError,ValueError):
                continue
            label=" ".join(str(x or "") for x in (
                odd.get("name"),meta.get("name"),meta.get("info"),odd.get("label")
            )).lower()
            if not re.search(r"\bpowyżej\b",label):
                continue
            if abs(odd_line-float(line))>0.001:
                continue
            if odd.get("status") not in (1,"1","ACTIVE","active"):
                continue
            if odd.get("display") is False:
                continue
            if meta.get("offer_state_id",1) not in (1,"1"):
                continue
            if not MIN_ODDS <= price <= MAX_ODDS:
                logging.info("Odrzucono kurs poza zakresem %.2f–%.2f: %.2f (event %s)",MIN_ODDS,MAX_ODDS,price,event_id)
                continue
            return price
    return None

def send(msg):
    if not TOKEN or not CHAT:
        logging.error("Brak TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID"); return False
    try:
        r=requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",data={"chat_id":CHAT,"text":msg,"disable_web_page_preview":True},timeout=20)
        if not r.ok: logging.error("Telegram %s %s",r.status_code,r.text[:250])
        return r.ok
    except Exception as e: logging.error("Telegram: %s",e); return False

def db_open():
    c=sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS picks(event_id INTEGER PRIMARY KEY,league TEXT,home TEXT,away TEXT,start_ts INTEGER,sent_at TEXT,status TEXT DEFAULT 'PENDING',ht_corners INTEGER,settled_at TEXT)""")
    columns={row[1] for row in c.execute("PRAGMA table_info(picks)").fetchall()}
    if "odds" not in columns:
        c.execute("ALTER TABLE picks ADD COLUMN odds REAL")
    if "league_corrected_at" not in columns:
        c.execute("ALTER TABLE picks ADD COLUMN league_corrected_at TEXT")
    c.execute("""CREATE TABLE IF NOT EXISTS monthly_reports(month TEXT PRIMARY KEY,sent_at TEXT NOT NULL)""")
    c.execute("UPDATE picks SET odds=1.92 WHERE event_id=15562733 AND odds IS NULL")
    c.execute("UPDATE picks SET odds=1.97 WHERE event_id=14572637 AND odds IS NULL")
    c.commit()
    return c

def resend_tips_with_corrected_league(c,events):
    """Re-send already-issued picks once if Superbet now exposes their real competition name."""
    by_id={str(e.get("event_id")):e for e in events if e.get("event_id") is not None}
    rows=c.execute("""SELECT event_id,home,away,start_ts,odds,league FROM picks
                      WHERE status='PENDING' AND league_corrected_at IS NULL""").fetchall()
    for eid,home,away,start_ts,odds,old_league in rows:
        event=by_id.get(str(eid))
        if not event:
            continue
        league=extract_league_name(event)
        if not league or league=="Liga niepodana przez Superbet":
            continue
        if league==old_league or old_league not in ("Superbet — liga","Liga niepodana przez Superbet","liga"):
            continue
        kickoff=datetime.fromtimestamp(start_ts,timezone.utc).astimezone(TZ)
        msg=("✏️ KOREKTA TYPU — NAZWA LIGI\\n\\n"
             f"🏆 {league}\\n⚽ {home} – {away}\\n"
             f"🕒 Start: {kickoff:%d.%m.%Y %H:%M}\\n\\n"
             f"📌 Typ: Over {HT_CORNERS_LINE:.1f} rzutów rożnych do HT\\n"
             f"💹 Kurs Superbet: {float(odds):.2f}\\n"
             f"💰 Stawka: {STAKE} zł")
        if send(msg):
            c.execute("UPDATE picks SET league=?,league_corrected_at=? WHERE event_id=?",
                      (league,datetime.now(TZ).isoformat(),eid))
            c.commit()
            logging.info("Wysłano korektę nazwy ligi dla event %s: %s",eid,league)
        time.sleep(.2)

def superbet_live_event(event_id):
    """Fetch one event directly from Superbet, independently of whether its markets remain open."""
    payload=superbet_api("/v3/pl-PL/events",{"events":str(event_id)})
    events=payload.get("events",[])
    if not events:
        logging.info("Superbet: brak danych live dla event %s",event_id)
        return None
    return events[0]

def _corner_pair_from_object(obj):
    """Read a home/away corner pair only when Superbet explicitly labels it as corners."""
    if not isinstance(obj,dict):
        return None
    label=" ".join(str(obj.get(k,"")) for k in ("name","label","type","statistic","key","description")).lower()
    if not any(x in label for x in ("corner","corners","rzut rożny","rzuty rożne","rożne")):
        return None
    home=next((obj[k] for k in ("home","home_value","homeTeam","home_team","home_team_value") if k in obj),None)
    away=next((obj[k] for k in ("away","away_value","awayTeam","away_team","away_team_value") if k in obj),None)
    try:
        return int(float(home)),int(float(away))
    except (TypeError,ValueError):
        return None

def superbet_ht_corners(event):
    """Return (home, away, is_first_half) from explicit Superbet in-play corner stats."""
    root=event.get("inplay_stats") or {}
    found=[]
    def walk(node,path=(),period_hint=""):
        if isinstance(node,dict):
            period=str(node.get("period") or node.get("period_name") or node.get("period_label") or period_hint).lower()
            pair=_corner_pair_from_object(node)
            if pair is not None:
                label=" ".join(path+(period,))
                is_ht=any(x in label for x in ("1h","1st","first half","first_half","1 poł","pierwsza poł"))
                found.append((pair,is_ht,label))
            # Some feeds expose the stat as separate home/away corner keys.
            keys={str(k).lower():v for k,v in node.items()}
            home_key=next((k for k in keys if ("corner" in k or "roż" in k) and ("home" in k or "host" in k)),None)
            away_key=next((k for k in keys if ("corner" in k or "roż" in k) and ("away" in k or "guest" in k)),None)
            if home_key and away_key:
                try:
                    pair=(int(float(keys[home_key])),int(float(keys[away_key])))
                    label=" ".join(path+(period,home_key,away_key))
                    is_ht=any(x in label for x in ("1h","1st","first half","first_half","1 poł","pierwsza poł"))
                    found.append((pair,is_ht,label))
                except (TypeError,ValueError):
                    pass
            for key,value in node.items():
                walk(value,path+(str(key).lower(),),period)
        elif isinstance(node,list):
            for item in node:
                walk(item,path,period_hint)
    walk(root)
    # Prefer explicit first-half values; never mistake full-match totals for HT totals.
    ht=[item for item in found if item[1]]
    if ht:
        return ht[-1][0][0],ht[-1][0][1],True
    return None

def superbet_event_phase(event):
    meta=event.get("inplay_stats_metadata") or {}
    labels=" ".join(str(meta.get(k,"")) for k in ("event_status_label","period_status","status")).lower()
    if any(x in labels for x in ("1h","1st","first half","1 poł")):
        return "FIRST_HALF"
    if any(x in labels for x in ("ht","half-time","halftime","break","przerwa")):
        return "HALFTIME"
    if any(x in labels for x in ("2h","2nd","second half","2 poł")):
        return "SECOND_HALF"
    if any(x in labels for x in ("ft","finished","ended","full time")):
        return "FINISHED"
    return "UNKNOWN"

def settle(c,now):
    # Superbet is the sole source for live settlement; market availability is irrelevant.
    rows=c.execute("SELECT event_id,league,home,away FROM picks WHERE status='PENDING'").fetchall()
    for eid,league,home,away in rows:
        event=superbet_live_event(eid)
        if not event:
            continue
        phase=superbet_event_phase(event)
        stats=superbet_ht_corners(event)
        if phase=="UNKNOWN":
            logging.info("Superbet: faza meczu nieznana dla %s — bez rozliczenia",eid)
            continue
        if stats is None:
            logging.info("Superbet: brak jednoznacznych statystyk rożnych HT dla %s (faza %s)",eid,phase)
            continue
        home_corners,away_corners,_=stats
        n=home_corners+away_corners
        if phase=="FIRST_HALF" and n<5:
            continue
        if phase not in ("FIRST_HALF","HALFTIME","SECOND_HALF","FINISHED"):
            continue
        won=n>=5
        msg=(f"📊 ROZLICZENIE — ROGI DO HT\n\n🏆 {league}\n⚽ {home} – {away}\n"
             f"🚩 Rożne do przerwy: {n}\n🎯 Over 4.5 HT: {'🟢 WYGRANY' if won else '🔴 PRZEGRANY'}\n"
             f"💰 Stawka: {STAKE} zł")
        c.execute("UPDATE picks SET status=?,ht_corners=?,settled_at=? WHERE event_id=?",
                  ("WON" if won else "LOST",n,datetime.now(TZ).isoformat(),eid))
        c.commit()
        if not send(msg):
            logging.error("Nie udało się wysłać rozliczenia Telegram dla event %s",eid)
        else:
            logging.info("Rozliczono typ Superbet event %s: %s rożnych HT — %s",eid,n,"WON" if won else "LOST")
        time.sleep(.2)

def watch_pending_picks(c,duration_seconds=270,interval_seconds=5):
    """Poll live first-half corner counts frequently so a winning pick settles in-play."""
    deadline=time.monotonic()+duration_seconds
    while time.monotonic()<deadline:
        settle(c,int(datetime.now(timezone.utc).timestamp()))
        remaining=deadline-time.monotonic()
        if remaining>0:
            time.sleep(min(interval_seconds,remaining))

def send_monthly_reports(c,local):
    current_month=local.strftime("%Y-%m")
    months=c.execute("SELECT DISTINCT substr(sent_at,1,7) FROM picks WHERE substr(sent_at,1,7)<? ORDER BY 1",(current_month,)).fetchall()
    for (month,) in months:
        if c.execute("SELECT 1 FROM monthly_reports WHERE month=?",(month,)).fetchone():
            continue
        pending=c.execute("SELECT COUNT(*) FROM picks WHERE substr(sent_at,1,7)=? AND status='PENDING'",(month,)).fetchone()[0]
        if pending:
            logging.info("Raport %s czeka na rozliczenie %s typów",month,pending)
            continue
        rows=c.execute("SELECT status,odds FROM picks WHERE substr(sent_at,1,7)=?",(month,)).fetchall()
        wins=sum(1 for status,_ in rows if status=="WON")
        losses=sum(1 for status,_ in rows if status=="LOST")
        voids=sum(1 for status,_ in rows if status=="VOID")
        decided=wins+losses
        accuracy=(wins/decided*100) if decided else 0.0
        priced=[(status,float(odds)) for status,odds in rows if status in ("WON","LOST") and odds is not None and float(odds)>1.0]
        missing=sum(1 for status,odds in rows if status in ("WON","LOST") and (odds is None or float(odds)<=1.0))
        turnover=len(priced)*STAKE
        profit=sum(STAKE*(odds-1) if status=="WON" else -STAKE for status,odds in priced)
        yield_pct=(profit/turnover*100) if turnover else 0.0
        result="ZYSK" if profit>0 else ("STRATA" if profit<0 else "NA ZERO")
        msg=(f"📅 MIESIĘCZNY BILANS — {month}\\n\\n"
             f"📊 Rozliczone typy: {decided}\\n"
             f"🟢 Wygrane: {wins}\\n🔴 Przegrane: {losses}\\n"
             f"⚪ Anulowane: {voids}\\n"
             f"🎯 Skuteczność: {accuracy:.1f}%\\n"
             f"📈 Yield: {yield_pct:+.2f}%\\n"
             f"💰 Stawka: {STAKE} zł / typ\\n"
             f"💵 Obrót z kursami: {turnover:.2f} zł\\n"
             f"🏦 Wynik: {profit:+.2f} zł ({result})")
        if missing:
            msg+=f"\\n⚠️ Typy bez zapisanego kursu, pominięte w yield: {missing}"
        if send(msg):
            c.execute("INSERT OR IGNORE INTO monthly_reports(month,sent_at) VALUES(?,?)",(month,local.isoformat()))
            c.commit()
            logging.info("Wysłano miesięczny raport %s",month)
        else:
            logging.error("Nie udało się wysłać miesięcznego raportu %s",month)

def main():
    if not TOKEN or not CHAT:
        logging.error("Brak TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        return
    c=db_open()
    now=datetime.now(timezone.utc)
    ts=int(now.timestamp())
    settle(c,ts)
    local=datetime.now(TZ)
    send_monthly_reports(c,local)
    key=local.date().isoformat()
    count=c.execute("SELECT COUNT(*) FROM picks WHERE substr(sent_at,1,10)=?",(key,)).fetchone()[0]
    if count>=MAX_DAILY:
        logging.info("Dzienny limit osiągnięty: %s/%s — monitoruję nierozliczone typy",count,MAX_DAILY)
        watch_pending_picks(c)
        c.close()
        return
    day_start=datetime.combine(local.date(),datetime.min.time(),TZ)
    day_end=datetime.combine(local.date(),datetime.max.time(),TZ)
    start=max(now,day_start.astimezone(timezone.utc))
    end=day_end.astimezone(timezone.utc)
    if start>=end:
        logging.info("Brak pozostałych godzin dzisiejszego dnia")
        c.close()
        return

    # Superbet is the source of fixtures and prices; don't discard niche leagues
    # because a second provider has no matching fixture or historical statistics.
    sb_events=superbet_football_events(start,end)
    resend_tips_with_corrected_league(c,sb_events)
    logging.info("Superbet: %d meczów piłkarskich przedmeczowych",len(sb_events))
    available=[]
    checked=0
    for listed in sb_events:
        fx=listed.get("fixture",{})
        event_id=listed.get("event_id")
        if not event_id:
            continue
        try:
            st=int(datetime.fromisoformat(str(fx["utc_date"]).replace("Z","+00:00")).timestamp())
        except Exception:
            continue
        if not int(start.timestamp())<=st<=int(end.timestamp()):
            continue
        parts=str(fx.get("event_name","")).split("·",1)
        if len(parts)!=2:
            continue
        home,away=(x.strip() for x in parts)
        if c.execute("SELECT 1 FROM picks WHERE event_id=?",(int(event_id),)).fetchone():
            continue
        checked+=1
        price=superbet_ht_over_odds(event_id,HT_CORNERS_LINE)
        if price is not None:
            available.append((st,int(event_id),home,away,price,fx))
            logging.info("Znaleziono O%.1f HT: %s - %s @ %.2f",HT_CORNERS_LINE,home,away,price)

    available.sort(key=lambda x:x[0])
    sent=0
    for st,event_id,home,away,price,fx in available:
        if count>=MAX_DAILY:
            break
        league=extract_league_name(listed)
        kickoff=datetime.fromtimestamp(st,timezone.utc).astimezone(TZ)
        msg=("🎯 OKOTYPERA — ROGI DO HT\n\n"
             f"🏆 {league}\n⚽ {home} – {away}\n"
             f"🕒 Start: {kickoff:%d.%m.%Y %H:%M}\n\n"
             f"📌 Typ: Over {HT_CORNERS_LINE:.1f} rzutów rożnych do HT\n"
             f"💹 Kurs Superbet: {price:.2f}\n"
             f"💰 Stawka: {STAKE} zł")
        if send(msg):
            c.execute("INSERT OR IGNORE INTO picks(event_id,league,home,away,start_ts,sent_at,status,odds) VALUES(?,?,?,?,?,?,'PENDING',?)",
                      (event_id,league,home,away,st,local.isoformat(),price))
            c.commit()
            count+=1
            sent+=1
            logging.info("Wysłano typ Superbet event %s kurs %.2f",event_id,price)
        else:
            logging.error("Nie udało się wysłać typu do Telegrama dla event %s",event_id)
        time.sleep(.3)

    if sent==0:
        send("🤖 ROGI DO HT — RAPORT SKANU\n\n"
             f"📅 {local:%d.%m.%Y}\n"
             f"🔎 Mecze Superbet sprawdzone: {checked}\n"
             f"🎯 Potwierdzone rynki Over {HT_CORNERS_LINE:.1f} HT: {len(available)}\n"
             "ℹ️ Nie wysłano typu, bo nie znaleziono aktywnego kursu. Nie generuję fikcyjnych typów.")
    logging.info("Skan zakończony — wysłano %s typów",sent)
    watch_pending_picks(c)
    c.close()

if __name__=="__main__": main()
