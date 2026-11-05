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
MIN_LEAD_HOURS=3
LOOKAHEAD_HOURS=48
HISTORY=8
MIN_SAMPLE=5
MIN_TEAM_AVG=2.2
MIN_COMBINED_AVG=5.5
MIN_HIT_RATE=0.60
HT_CORNERS_LINE=4.5
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
    payload=superbet_api("/v3/pl-PL/events",{"events":str(event_id)})
    events=payload.get("events",[])
    if not events:
        return None
    for market in events[0].get("markets",[]):
        name=re.sub(r"\s+"," ",str(market.get("name","")).strip().lower())
        if not re.fullmatch(r"1\.?\s*połowa\s*-\s*liczba rzutów rożnych",name):
            continue
        for odd in market.get("odds",[]):
            meta=odd.get("metadata",{})
            try:
                odd_line=float(meta.get("special_bet_value"))
                price=float(odd.get("price"))
            except (TypeError,ValueError):
                continue
            label=(str(meta.get("name",""))+" "+str(meta.get("info",""))).lower()
            if abs(odd_line-float(line))>0.001 or "powyżej" not in label:
                continue
            if odd.get("status")!=1 or not odd.get("display",False):
                continue
            if meta.get("offer_state_id") not in (1,"1") or price<=1.0:
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
    rows=c.execute("SELECT event_id,league,home,away FROM picks WHERE status='PENDING' AND start_ts<?",(now-35*60,)).fetchall()
    for eid,league,home,away in rows:
        ev=api(f"/event/{eid}").get("event",{})
        status=ev.get("status",{}).get("type","")
        if status not in ("finished","canceled","postponed"): continue
        if status=="finished":
            stats=first_half_corners(eid)
            if stats is None:
                logging.info("Statystyki HT jeszcze niedostępne: %s",eid); continue
            n=sum(stats); won=n>=5
            msg=f"📊 ROZLICZENIE — ROGI DO HT\n\n🏆 {league}\n⚽ {home} – {away}\n🚩 Rożne do przerwy: {n}\n🎯 Over 4.5 HT: {'WYGRANY ✅' if won else 'PRZEGRANY ❌'}\n💰 Stawka: {STAKE} zł"
            c.execute("UPDATE picks SET status=?,ht_corners=?,settled_at=? WHERE event_id=?",("WON" if won else "LOST",n,datetime.now(TZ).isoformat(),eid))
        else:
            msg=f"⚠️ Mecz {status.upper()}\n🏆 {league}\n⚽ {home} – {away}\nTyp anulowany — bez rozliczenia."
            c.execute("UPDATE picks SET status='VOID',settled_at=? WHERE event_id=?",(datetime.now(TZ).isoformat(),eid))
        c.commit(); send(msg); time.sleep(.2)

def main():
    if not TOKEN or not CHAT:
        logging.error("Dodaj sekrety TELEGRAM_BOT_TOKEN i TELEGRAM_CHAT_ID w Settings → Secrets and variables → Actions"); return
    c=db_open(); now=datetime.now(timezone.utc); ts=int(now.timestamp())
    settle(c,ts)
    local=datetime.now(TZ); key=local.date().isoformat()
    count=c.execute("SELECT COUNT(*) FROM picks WHERE substr(sent_at,1,10)=?",(key,)).fetchone()[0]
    if count>=MAX_DAILY:
        logging.info("Dzienny limit osiągnięty: %s/%s",count,MAX_DAILY); c.close(); return
    start=now+timedelta(hours=MIN_LEAD_HOURS); end=now+timedelta(hours=LOOKAHEAD_HOURS)
    sb_events=superbet_football_events(start,end)
    if not sb_events:
        logging.warning("Superbet nie zwrócił przedmeczowych meczów piłkarskich — bez nowych typów")
    events=[]
    for day in dates(start,end):
        events.extend(api(f"/sport/football/scheduled-events/{day}").get("events",[])); time.sleep(.2)
    unique={}
    for e in events:
        eid=e.get("id"); st=e.get("startTimestamp")
        if not eid or not st or not int(start.timestamp())<=st<=int(end.timestamp()): continue
        if e.get("status",{}).get("type") not in ("notstarted",""): continue
        if c.execute("SELECT 1 FROM picks WHERE event_id=?",(eid,)).fetchone(): continue
        h=e.get("homeTeam",{}); a=e.get("awayTeam",{})
        if h.get("id") and a.get("id"): unique[eid]=(st,e)
    odds_cache={}
    for eid,(st,e) in sorted(unique.items(),key=lambda x:x[1][0]):
        if count>=MAX_DAILY: break
        h=e["homeTeam"]; a=e["awayTeam"]
        hh=history(h["id"]); ah=history(a["id"])
        if len(hh)<MIN_SAMPLE or len(ah)<MIN_SAMPLE:
            logging.info("Pominięto %s — za mało danych",eid); continue
        avh=sum(hh)/len(hh); ava=sum(ah)/len(ah)
        hit=sum(x>=3 for x in hh+ah)/len(hh+ah)
        combined=avh+ava
        if avh<MIN_TEAM_AVG or ava<MIN_TEAM_AVG or combined<MIN_COMBINED_AVG or hit<MIN_HIT_RATE: continue
        sb_event=superbet_event_for_match(h.get("name",""),a.get("name",""),st,sb_events)
        if not sb_event:
            logging.info("Pominięto %s — brak pewnego dopasowania meczu w Superbet",eid); continue
        sb_id=sb_event.get("event_id")
        if sb_id not in odds_cache:
            odds_cache[sb_id]=superbet_ht_over_odds(sb_id)
        price=odds_cache[sb_id]
        if price is None:
            logging.info("Pominięto %s — brak aktywnego kursu Over %.1f rożnych HT w Superbet",eid,HT_CORNERS_LINE); continue
        league=e.get("tournament",{}).get("name") or e.get("uniqueTournament",{}).get("name") or "Liga nieznana"
        kickoff=datetime.fromtimestamp(st,timezone.utc).astimezone(TZ)
        left=(st-ts)/3600
        msg=("🎯 OKOTYPERA — ROGI DO HT\n\n"
             f"🏆 {league}\n⚽ {h.get('name')} – {a.get('name')}\n"
             f"🕒 Start: {kickoff:%d.%m.%Y %H:%M}\n⏳ Do meczu: {left:.1f} godz.\n\n"
             "📌 Typ: Over 4.5 rzutów rożnych do HT\n"
             f"💹 Kurs Superbet: {price:.2f}\n"
             f"📊 Średnia 1H obu drużyn: {combined:.1f}\n📈 Próba: {len(hh)} + {len(ah)} meczów\n💰 Stawka: {STAKE} zł")
        if send(msg):
            c.execute("INSERT OR IGNORE INTO picks(event_id,league,home,away,start_ts,sent_at,status) VALUES(?,?,?,?,?,?,'PENDING')",(eid,league,h.get("name","?"),a.get("name","?"),st,local.isoformat()))
            c.commit(); count+=1; logging.info("Wysłano typ %s, Superbet event %s, kurs %.2f",eid,sb_id,price)
        time.sleep(.3)
    logging.info("Skan zakończony — %s/%s typów dziś",count,MAX_DAILY)
    c.close()

if __name__=="__main__": main()
