import os, time, sqlite3, logging, re, unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests

API="https://www.sofascore.com/api/v1"
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
s.headers.update({"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125 Safari/537.36","Accept":"application/json"})
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
        r=s.get(API+path,timeout=18)
        if r.status_code in (403,404,429):
            logging.warning("SofaScore HTTP %s %s",r.status_code,path); return {}
        r.raise_for_status(); return r.json()
    except Exception as e:
        logging.warning("SofaScore API %s: %s",path,e); return {}

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
    c.execute("""CREATE TABLE IF NOT EXISTS monthly_reports(month TEXT PRIMARY KEY,sent_at TEXT NOT NULL)""")
    c.execute("UPDATE picks SET odds=1.92 WHERE event_id=15562733 AND odds IS NULL")
    c.execute("UPDATE picks SET odds=1.97 WHERE event_id=14572637 AND odds IS NULL")
    c.commit()
    return c

def first_half_corners(event_id):
    root=api(f"/event/{event_id}/statistics").get("statistics",{})
    blocks=root if isinstance(root,list) else root.get("statistics",[root] if isinstance(root,dict) else [])
    for b in blocks:
        if str(b.get("period","")).upper() not in ("1ST","1H","FIRST_HALF","1"): continue
        for g in b.get("groups",[]):
            for item in g.get("statisticsItems",[]):
                if "corner" in str(item.get("name","")).lower():
                    try: return int(float(item["home"])),int(float(item["away"]))
                    except (KeyError,ValueError,TypeError): pass
    return None

def history(team_id):
    events=api(f"/team/{team_id}/events/last/0").get("events",[])
    out=[]
    for e in events:
        if e.get("status",{}).get("type")!="finished": continue
        stats=first_half_corners(e.get("id"))
        if stats:
            if e.get("homeTeam",{}).get("id")==team_id: out.append(stats[0])
            elif e.get("awayTeam",{}).get("id")==team_id: out.append(stats[1])
        if len(out)>=HISTORY: break
        time.sleep(.15)
    return out

def dates(a,b):
    d=a.date()
    while d<=b.date():
        yield d.isoformat(); d+=timedelta(days=1)

def settle(c,now):
    # Settle Over 4.5 HT as soon as the fifth corner is recorded in the first half.
    rows=c.execute("SELECT event_id,league,home,away FROM picks WHERE status='PENDING'").fetchall()
    for eid,league,home,away in rows:
        ev=api(f"/event/{eid}").get("event",{})
        status_obj=ev.get("status") or {}
        status=str(status_obj.get("type","")).lower()
        status_code=status_obj.get("code")
        description=str(status_obj.get("description","")).lower()
        period_obj=ev.get("period") or {}
        period=" ".join(str(v).lower() for v in period_obj.values()) if isinstance(period_obj,dict) else str(period_obj).lower()
        phase=description+" "+period
        # SofaScore status codes: 6 = first half, 7 = half-time, 8 = second half, 100 = finished.
        is_first_half=(status_code==6 or str(status_code)=="6" or
                       any(x in phase for x in ("1st half","first half","1 poł","pierwsza poł")))
        is_halftime=(status_code==7 or str(status_code)=="7" or
                     any(x in phase for x in ("halftime","half-time","half time","break","przerwa")) or
                     status in ("halftime","halftimebreak"))
        is_second_half=(status_code==8 or str(status_code)=="8" or
                        any(x in phase for x in ("2nd half","second half","2 poł","druga poł")))
        if status in ("canceled","postponed"):
            msg=f"⚠️ Mecz {status.upper()}\n🏆 {league}\n⚽ {home} – {away}\nTyp anulowany — bez rozliczenia."
            c.execute("UPDATE picks SET status='VOID',settled_at=? WHERE event_id=?",(datetime.now(TZ).isoformat(),eid))
        else:
            stats_payload=api(f"/event/{eid}/statistics").get("statistics",{})
            stat_blocks=stats_payload if isinstance(stats_payload,list) else stats_payload.get("statistics",[])
            if isinstance(stat_blocks,dict):
                stat_blocks=[stat_blocks]
            first_half_stats=None
            current_stats=None
            for stat_block in stat_blocks:
                p=str(stat_block.get("period","")).upper()
                if p not in ("1ST","1H","FIRST_HALF","1","ALL",""):
                    continue
                for group in stat_block.get("groups",[]):
                    for item in group.get("statisticsItems",[]):
                        if "corner" not in str(item.get("name","")).lower() and "roż" not in str(item.get("name","")).lower():
                            continue
                        try:
                            pair=(int(float(item["home"])),int(float(item["away"])))
                        except (KeyError,ValueError,TypeError):
                            continue
                        if p in ("1ST","1H","FIRST_HALF","1"):
                            first_half_stats=pair
                        else:
                            current_stats=pair
            if status=="finished":
                stats=first_half_corners(eid) or first_half_stats
                if stats is None:
                    logging.info("Statystyki HT jeszcze niedostępne: %s",eid)
                    continue
                n=sum(stats)
                won=n>=5
            elif is_first_half:
                # During the first half, ALL/current corner stats are the HT total so far.
                stats=first_half_stats if first_half_stats is not None else current_stats
                if stats is None:
                    logging.info("Brak bieżących statystyk rożnych HT: %s",eid)
                    continue
                n=sum(stats)
                if n<5:
                    continue
                won=True
            elif is_halftime:
                stats=first_half_stats if first_half_stats is not None else current_stats
                if stats is None:
                    logging.info("Brak statystyk po przerwie: %s",eid)
                    continue
                n=sum(stats)
                won=n>=5
            elif is_second_half:
                # Never use ALL stats in the second half: they include second-half corners.
                stats=first_half_corners(eid) or first_half_stats
                if stats is None:
                    logging.info("Brak potwierdzonych statystyk pierwszej połowy: %s",eid)
                    continue
                n=sum(stats)
                won=n>=5
            else:
                continue
            msg=f"📊 ROZLICZENIE — ROGI DO HT\n\n🏆 {league}\n⚽ {home} – {away}\n🚩 Rożne do przerwy: {n}\n🎯 Over 4.5 HT: {'🟢 WYGRANY' if won else '🔴 PRZEGRANY'}\n💰 Stawka: {STAKE} zł"
            c.execute("UPDATE picks SET status=?,ht_corners=?,settled_at=? WHERE event_id=?",("WON" if won else "LOST",n,datetime.now(TZ).isoformat(),eid))
        c.commit()
        if not send(msg):
            logging.error("Nie udało się wysłać rozliczenia Telegram dla event %s",eid)
        time.sleep(.2)

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
        logging.info("Dzienny limit osiągnięty: %s/%s",count,MAX_DAILY)
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
        league=str(fx.get("tournament_name") or fx.get("league_name") or "Superbet — liga")
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
    c.close()

if __name__=="__main__": main()
