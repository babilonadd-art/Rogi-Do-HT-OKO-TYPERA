import os, re, sqlite3, logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

API="https://production-superbet-offer-pl.freetls.fastly.net"
TZ=ZoneInfo("Europe/Warsaw")
DB="okotypera_rogi.db"
TOKEN=os.getenv("TELEGRAM_BOT_TOKEN")
CHAT=os.getenv("TELEGRAM_CHAT_ID")
MAX_DAILY=10
MIN_FAV_ODDS=1.15
MAX_FAV_ODDS=1.75
MIN_PICK_ODDS=1.50
MAX_PICK_ODDS=5.00
STAKE=10
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
s=requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0","Accept":"application/json,*/*","Origin":"https://superbet.pl","Referer":"https://superbet.pl/"})

def api(path,params=None):
    r=s.get(API+path,params=params,timeout=30); r.raise_for_status()
    p=r.json()
    return p if isinstance(p,dict) else {}

def events_today():
    now=datetime.now(timezone.utc)
    local=datetime.now(TZ)
    start=local.replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc)
    end=(local.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)).astimezone(timezone.utc)
    p=api("/v3/pl-PL/events",{"startDate":start.isoformat().replace("+00:00","Z"),"endDate":end.isoformat().replace("+00:00","Z")})
    arr=p.get("events") or p.get("data") or []
    return [x for x in arr if isinstance(x,dict)]

def active(x):
    return x.get("status") in (1,"1","ACTIVE","active",None) and x.get("display") is not False

def words(x):
    return re.sub(r"[^a-z0-9ąćęłńóśźż]+","",str(x or "").lower())

def market_text(m):
    return " ".join(str(m.get(k) or "") for k in ("name","label","market_name","marketName")).lower()

def get_odds(o):
    try: return float(o.get("price"))
    except (TypeError,ValueError): return None

def fav_side(event,home,away):
    found=[]
    hname,aname=words(home),words(away)
    for m in event.get("markets") or []:
        name=market_text(m)
        if any(k in name for k in ("half","połowa","polowa","roż","corner","double chance","handicap")): continue
        # Dopuszczamy pełny rynek 1X2 także wtedy, gdy Superbet opisuje
        # selekcje nazwami drużyn zamiast cyframi 1/X/2.
        for o in m.get("odds") or []:
            if not active(o): continue
            meta=o.get("metadata") or {}
            if str(meta.get("offer_state_id",1))!="1": continue
            price=get_odds(o)
            if price is None or not 1.01<=price<=20: continue
            label=words(" ".join(str(v or "") for v in (o.get("name"),o.get("label"),meta.get("name"),meta.get("info"))))
            if label in ("1","1gospodarze","gospodarze","gospodarz","home","hometeam") or (hname and hname in label and "wygra" in label): side="home"
            elif label in ("2","2goscie","goscie","gosc","away","awayteam") or (aname and aname in label and "wygra" in label): side="away"
            else: continue
            found.append((price,side))
    # Muszą być dostępne kursy na obie drużyny, a nie tylko na jedną.
    home_prices=[x[0] for x in found if x[1]=="home"]
    away_prices=[x[0] for x in found if x[1]=="away"]
    if not home_prices or not away_prices: return None
    h=min(home_prices); a=min(away_prices)
    fav=("home",h) if h<a else ("away",a)
    opp=a if fav[0]=="home" else h
    if not MIN_FAV_ODDS<=fav[1]<=MAX_FAV_ODDS or opp/fav[1]<1.45: return None
    return {"side":fav[0],"odds":fav[1],"opponent_odds":opp}

def both_halves_market(event,side,home,away):
    team=home if side=="home" else away
    team_key=words(team)
    for m in event.get("markets") or []:
        name=market_text(m)
        compact_market=words(name)
        # W Superbet rynek nazywa się np. "Sabadell zdobędzie gola w obu połowach".
        # Nazwa drużyny jest w tytule rynku, a selekcja TAK ma metadane "tak".
        if not any(k in compact_market for k in ("obupolowach","obu polowach")): continue
        if not team_key or team_key not in compact_market: continue
        if not active(m): continue
        for o in m.get("odds") or []:
            if not active(o): continue
            meta=o.get("metadata") or {}
            if str(meta.get("offer_state_id",1))!="1": continue
            label=words(" ".join(str(v or "") for v in (o.get("name"),o.get("label"),meta.get("name"),meta.get("info"))))
            if label not in ("tak","yes"): continue
            price=get_odds(o)
            if price is not None and MIN_PICK_ODDS<=price<=MAX_PICK_ODDS:
                return price,str(m.get("name") or f"{team} zdobędzie gola w obu połowach")
    return None

def detail(eid):
    p=api("/v3/pl-PL/events",{"events":str(eid)})
    a=p.get("events") or p.get("data") or []
    if isinstance(a,dict): a=list(a.values())
    if len(a)==1: return a[0]
    return next((x for x in a if str(x.get("event_id") or x.get("id"))==str(eid)),None)

def send(msg):
    if not TOKEN or not CHAT: return False
    r=requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",data={"chat_id":CHAT,"text":msg,"disable_web_page_preview":True},timeout=20)
    try: return bool(r.ok and r.json().get("ok"))
    except Exception: return False

def main():
    if not TOKEN or not CHAT:
        logging.error("Brak sekretów Telegrama"); return
    now=datetime.now(TZ); day=now.date().isoformat()
    c=sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS favorite_both_halves(
      event_id TEXT PRIMARY KEY, sent_at TEXT NOT NULL, home TEXT, away TEXT,
      favorite TEXT, odds REAL, kickoff TEXT)""")
    sent={x[0] for x in c.execute("SELECT event_id FROM favorite_both_halves WHERE substr(sent_at,1,10)=?",(day,))}
    logging.info("Faworyt oba półrocza: wysłane dziś %d/%d",len(sent),MAX_DAILY)
    listed=events_today()
    candidates=[]
    for e in listed:
        fx=e.get("fixture") or {}
        if str(fx.get("sport_id",e.get("sport_id","5")))!="5": continue
        status=str((e.get("inplay_stats_metadata") or {}).get("status") or "").upper()
        if status not in ("","NOT_STARTED"): continue
        eid=str(e.get("event_id") or e.get("id") or "")
        if not eid or eid in sent: continue
        parts=re.split(r"\s*[·–-]\s*",str(fx.get("event_name") or ""),maxsplit=1)
        if len(parts)!=2: continue
        home,away=[x.strip() for x in parts]
        try: kickoff=datetime.fromisoformat(str(fx.get("utc_date")).replace("Z","+00:00"))
        except ValueError: continue
        if kickoff<=datetime.now(timezone.utc): continue
        candidates.append((eid,home,away,kickoff,e))
    logging.info("Superbet: %d meczów przedmeczowych do sprawdzenia",len(candidates))
    # Diagnostyka rzeczywistej struktury oferty Superbet — tylko kilka meczów,
    # aby ustalić nazwy rynków i selekcji zamiast zgadywać etykiety.
    for sample in candidates[:5]:
        sid, shome, saway, _, listed_event = sample
        se = detail(sid) or listed_event
        smarkets = se.get("markets") or []
        logging.info("DIAG %s - %s | event_keys=%s | markets=%d",
                     shome, saway, list(se.keys())[:20], len(smarkets))
        for market in smarkets:
            mt = market_text(market)
            if not any(k in mt for k in ("1x2", "wynik meczu", "obu poł", "obu pol", "strzeli", "gole w każdej", "gole w kazdej")):
                continue
            selections = []
            for odd in (market.get("odds") or [])[:12]:
                meta = odd.get("metadata") or {}
                selections.append({
                    "name": odd.get("name"), "label": odd.get("label"),
                    "price": odd.get("price"), "meta_name": meta.get("name"),
                    "meta_info": meta.get("info")
                })
            logging.info("DIAG MARKET %s | %s", mt[:140], selections)
    qualified=[]
    def inspect(item):
        eid,home,away,kickoff,listed_event=item
        e=detail(eid) or listed_event
        fav=fav_side(e,home,away)
        if not fav: return None
        price,market=both_halves_market(e,fav["side"],home,away)
        if price is None: return None
        team=home if fav["side"]=="home" else away
        return {"id":eid,"home":home,"away":away,"kickoff":kickoff,"team":team,
                "fav_odds":fav["odds"],"odds":price,"market":market}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures=[pool.submit(inspect,x) for x in candidates]
        for f in as_completed(futures):
            try:
                x=f.result()
                if x: qualified.append(x)
            except Exception: logging.exception("Błąd analizy rynku faworyta")
    qualified.sort(key=lambda x:(x["fav_odds"],x["odds"],x["kickoff"]))
    selected=qualified[:max(0,MAX_DAILY-len(sent))]
    logging.info("Potwierdzone rynki: %d; wybrane: %d",len(qualified),len(selected))
    for x in selected:
        # Ostatnia kontrola rynku i kursów na świeżych danych przed publikacją.
        e=detail(x["id"])
        if not e: continue
        fav=fav_side(e,home,away)
        if not fav or (x["team"]!=(x["home"] if fav["side"]=="home" else x["away"])): continue
        price,market=both_halves_market(e,fav["side"],x["home"],x["away"])
        if price is None: continue
        number=len(sent)+1
        kickoff=x["kickoff"].astimezone(TZ)
        url=f"https://superbet.pl/zaklady-bukmacherskie/pilka-nozna/{x['id']}"
        msg=("⚽ TYP — FAWORYT STRZELI W OBU POŁOWACH\n"
             f"🔢 TYP NR.{number}\n📅 {kickoff:%d.%m.%Y} | 🕒 {kickoff:%H:%M}\n\n"
             f"⚔️ {x['home']} – {x['away']}\n"
             f"⭐ Faworyt: {x['team']} (kurs 1X2: {fav['odds']:.2f})\n"
             f"🎯 Typ: {x['team']} strzeli gola w 1. i 2. połowie — TAK\n"
             f"💹 Kurs Superbet: {price:.2f}\n💰 Stawka informacyjna: {STAKE} zł\n"
             f"🔗 Mecz w Superbet: {url}")
        if send(msg):
            c.execute("INSERT OR IGNORE INTO favorite_both_halves VALUES(?,?,?,?,?,?,?)",
                      (x["id"],now.isoformat(),x["home"],x["away"],x["team"],price,kickoff.isoformat()))
            c.commit(); sent.add(x["id"])
            logging.info("Wysłano: %s – %s | faworyt %s | kurs %.2f",x["home"],x["away"],x["team"],price)
    c.close()

if __name__=="__main__": main()
