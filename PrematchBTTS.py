import os, re, sqlite3, logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import requests

API="https://production-superbet-offer-pl.freetls.fastly.net"
TZ=ZoneInfo("Europe/Warsaw")
DB="okotypera_rogi.db"
TOKEN=os.getenv("TELEGRAM_BOT_TOKEN")
CHAT=os.getenv("TELEGRAM_CHAT_ID")
MIN_ODDS=1.70
MAX_ODDS=2.60
MAX_PICKS=3
STAKE=10
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
http=requests.Session()
http.headers.update({"User-Agent":"Mozilla/5.0 OkoTypera/1.0","Accept":"application/json,*/*","Origin":"https://superbet.pl","Referer":"https://superbet.pl/"})

def api(path,params=None):
    r=http.get(API+path,params=params,timeout=30)
    r.raise_for_status()
    data=r.json()
    if not isinstance(data,dict): raise ValueError("Nieprawidłowa odpowiedź Superbet")
    return data

def kickoff(e):
    raw=(e.get("fixture") or {}).get("utc_date")
    try: return datetime.fromisoformat(str(raw).replace("Z","+00:00")).astimezone(TZ)
    except (ValueError,TypeError): return None

def teams(e):
    name=str((e.get("fixture") or {}).get("event_name") or "")
    for sep in ("·"," – "," - "," — "):
        if sep in name:
            a,b=name.split(sep,1)
            return a.strip(),b.strip()
    return "",""

def todays_events():
    now=datetime.now(TZ)
    start=now.replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc)
    end=(now.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)).astimezone(timezone.utc)
    data=api("/v3/pl-PL/events",{"startDate":start.isoformat().replace("+00:00","Z"),"endDate":end.isoformat().replace("+00:00","Z")})
    result=[]
    for e in data.get("events") or []:
        f=e.get("fixture") or {}
        status=str((e.get("inplay_stats_metadata") or {}).get("status") or "").upper()
        if str(f.get("sport_id"))!="5" or status not in ("","NOT_STARTED","PREMATCH","PRE_MATCH"): continue
        result.append(e)
    logging.info("Superbet: przedmeczowe mecze piłkarskie: %d",len(result))
    return result

def details(eid):
    data=api("/v3/pl-PL/events",{"events":str(eid)})
    es=data.get("events") or data.get("data") or []
    if isinstance(es,dict): es=list(es.values())
    if len(es)==1:
        got=es[0].get("event_id") or es[0].get("id")
        if got is not None and str(got)!=str(eid): return None
        return es[0]
    return next((e for e in es if str(e.get("event_id") or e.get("id"))==str(eid)),None)

def btts_yes(e):
    found=[]
    for m in e.get("markets") or []:
        name=str(m.get("name") or "").lower()
        compact=re.sub("[^a-z0-9]","",name)
        explicit=("bothteamstoscore" in compact or "bothteamsscore" in compact or
                  "obie drużyny strzelą" in name or "obie druzyny strzela" in name or
                  "obie drużyny zdobędą gola" in name or "obie druzyny zdobeda gola" in name)
        if not explicit or m.get("status") not in (None,1,"1","ACTIVE","active"): continue
        for o in m.get("odds") or []:
            meta=o.get("metadata") or {}
            label=" ".join(str(x or "") for x in (o.get("name"),o.get("label"),meta.get("name"),meta.get("info"))).lower()
            norm=re.sub("[^a-z]","",label)
            if norm not in ("yes","tak","bothteamstoscoreyes"): continue
            if o.get("status") not in (1,"1","ACTIVE","active") or o.get("display") is False: continue
            if str(meta.get("offer_state_id",1))!="1": continue
            try: price=float(o.get("price"))
            except (TypeError,ValueError): continue
            if MIN_ODDS<=price<=MAX_ODDS: found.append(price)
    return max(found) if found else None

def event_url(e):
    for obj in (e,e.get("fixture") or {}):
        for k in ("url","event_url","eventUrl","permalink","web_url","webUrl"):
            v=obj.get(k) if isinstance(obj,dict) else None
            if isinstance(v,str) and v.startswith("https://") and "superbet" in v.lower(): return v
    return "https://superbet.pl/zaklady-bukmacherskie/pilka-nozna"

def send(msg):
    if not TOKEN or not CHAT: raise RuntimeError("Brak sekretów Telegrama")
    r=requests.post("https://api.telegram.org/bot"+TOKEN+"/sendMessage",data={"chat_id":CHAT,"text":msg,"disable_web_page_preview":True},timeout=20)
    r.raise_for_status()
    return bool(r.json().get("ok"))

def main():
    conn=sqlite3.connect(DB)
    conn.execute("CREATE TABLE IF NOT EXISTS prematch_btts_sent (pick_key TEXT PRIMARY KEY,event_id TEXT NOT NULL,sent_at TEXT NOT NULL,home TEXT NOT NULL,away TEXT NOT NULL,odds REAL NOT NULL,kickoff TEXT NOT NULL)")
    conn.commit()
    try:
        candidates=[]
        for listed in todays_events():
            eid=listed.get("event_id") or listed.get("id")
            start=kickoff(listed)
            home,away=teams(listed)
            if not eid or not start or start<=datetime.now(TZ) or not home or not away: continue
            key=str(eid)+"|PREMATCH|BTTS|YES"
            if conn.execute("SELECT 1 FROM prematch_btts_sent WHERE pick_key=?",(key,)).fetchone(): continue
            e=details(eid)
            if not e: continue
            status=str((e.get("inplay_stats_metadata") or {}).get("status") or "").upper()
            if status not in ("","NOT_STARTED","PREMATCH","PRE_MATCH"): continue
            price=btts_yes(e)
            if price is not None: candidates.append((price,start,str(eid),key,home,away,event_url(e)))
        picks=sorted(candidates,key=lambda x:(-x[0],x[1]))[:MAX_PICKS]
        logging.info("BTTS: kandydaci=%d, wybrane=%d",len(candidates),len(picks))
        for price,start,eid,key,home,away,url in picks:
            fresh=details(eid)
            if not fresh: continue
            fresh_start=kickoff(fresh) or start
            status=str((fresh.get("inplay_stats_metadata") or {}).get("status") or "").upper()
            fresh_price=btts_yes(fresh)
            if status not in ("","NOT_STARTED","PREMATCH","PRE_MATCH") or fresh_start<=datetime.now(TZ) or fresh_price is None: continue
            h,a=teams(fresh)
            if h and a: home,away=h,a
            msg=("🟡 PRZEDMECZOWE BTTS\n"
                 f"⚽ {home} – {away}\n"
                 f"🕒 Start: {fresh_start:%d.%m.%Y %H:%M}\n"
                 "📌 Typ: BTTS — TAK (obie drużyny strzelą)\n"
                 f"💹 Kurs Superbet: {fresh_price:.2f}\n"
                 f"💰 Stawka: {STAKE} zł\n"
                 f"🔗 Mecz: {event_url(fresh)}")
            if send(msg):
                conn.execute("INSERT OR IGNORE INTO prematch_btts_sent VALUES(?,?,?,?,?,?,?)",(key,eid,datetime.now(TZ).isoformat(),home,away,fresh_price,fresh_start.isoformat()))
                conn.commit()
                logging.info("Wysłano BTTS %s - %s @ %.2f",home,away,fresh_price)
    finally: conn.close()

if __name__=="__main__": main()
