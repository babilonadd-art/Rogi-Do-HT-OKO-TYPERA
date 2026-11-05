import os, time, sqlite3, logging, re, unicodedata, csv, io, json
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

API="https://www.sofascore.com/api/v1"
API_FALLBACK="https://www.sofascore.com/api/v1"
SUPERBET_API="https://production-superbet-offer-pl.freetls.fastly.net"
TZ=ZoneInfo("Europe/Warsaw")
DB="okotypera_rogi.db"
SENT_IDS_FILE="sent_event_ids.json"
TOKEN=os.getenv("TELEGRAM_BOT_TOKEN")
CHAT=os.getenv("TELEGRAM_CHAT_ID")
STAKE=50
MAX_DAILY=10
# Jednorazowy reset dzisiejszego licznika: pomijamy 20 wcześniejszych typów, aby umożliwić test do 20 nowych wysyłek.
RESET_BASELINE_DATE="2026-11-05"
RESET_BASELINE_COUNT=20
TAX_RATE=0.12
FT_CORNERS_LINE=9.5
MIN_ODDS=1.70
MAX_ODDS=2.50
SB_TOURNAMENT_NAMES = None
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
        r=sb.get(SUPERBET_API+path,params=params,timeout=20)
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

def superbet_tournament_names():
    """Load Superbet's official Polish tournament-name map once per run."""
    global SB_TOURNAMENT_NAMES
    if SB_TOURNAMENT_NAMES is not None:
        return SB_TOURNAMENT_NAMES
    SB_TOURNAMENT_NAMES={}
    payload=superbet_api("/v2/pl-PL/struct")
    data=payload.get("data") or {}
    for item in data.get("tournaments",[]) or []:
        if not isinstance(item,dict):
            continue
        tournament_id=str(item.get("id","")).strip()
        names=item.get("localNames") or {}
        name=names.get("pl-PL") or names.get("en-GB") or names.get("en")
        if tournament_id and isinstance(name,str) and name.strip():
            SB_TOURNAMENT_NAMES[tournament_id]=name.strip()
    logging.info("Superbet: załadowano %d nazw turniejów",len(SB_TOURNAMENT_NAMES))
    return SB_TOURNAMENT_NAMES

def extract_league_name(event):
    """Resolve full competition name from Superbet's own event and structure data."""
    if not isinstance(event,dict):
        return ""
    fixture=event.get("fixture") or {}
    containers=[fixture,event]
    # Superbet payloads use both snake_case and camelCase names, and nest
    # competition details under category/tournament/competition objects.
    keys=("tournament","league","competition","championship","category",
          "sport_category","sportCategory","tournament_info","tournamentInfo")
    seen=set()
    while containers:
        parent=containers.pop(0)
        if not isinstance(parent,dict) or id(parent) in seen:
            continue
        seen.add(id(parent))
        for key in keys:
            value=parent.get(key)
            if isinstance(value,dict):
                containers.append(value)
        for key in ("tournament_name","tournamentName","league_name","leagueName",
                    "competition_name","competitionName","championship_name",
                    "championshipName","displayName","display_name","name","title","label"):
            value=parent.get(key)
            if isinstance(value,str) and value.strip():
                # Do not mistake a fixture/event title for a competition name.
                if key in ("name","title","label") and parent is fixture:
                    continue
                return value.strip()
    tournament_id=fixture.get("tournament_id") or fixture.get("tournamentId")
    if tournament_id:
        name=superbet_tournament_names().get(str(tournament_id))
        if name:
            return name
    return ""

def resolve_full_league(event,home,away,start_ts):
    """Use Superbet competition metadata first, SofaScore only if absent."""
    league=extract_league_name(event)
    if league and league!="Liga niepodana przez Superbet":
        return league
    match=sofascore_event_for_pick(home,away,start_ts)
    if match:
        unique=match.get("uniqueTournament") or {}
        tournament=match.get("tournament") or {}
        category=match.get("category") or {}
        name=unique.get("name") or tournament.get("name")
        country=category.get("name")
        if name and country and country.lower() not in name.lower():
            return f"{name} — {country}"
        if name:
            return name
    return ""

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

def superbet_prematch_event_url(event,home,away,event_id):
    """Build a pre-match Superbet event URL opened on the corners market."""
    fixture=(event or {}).get("fixture") or {}
    url=None
    for key in ("url","event_url","eventUrl","slug","seo_url","seoUrl"):
        value=fixture.get(key) or (event or {}).get(key)
        if isinstance(value,str) and value.strip():
            value=value.strip()
            if value.startswith(("https://","http://")):
                url=value
                break
            if value.startswith("/"):
                url="https://superbet.pl"+value
                break
    if not url:
        def slug_part(value):
            value=unicodedata.normalize("NFKD",str(value or "")).encode("ascii","ignore").decode().lower()
            return re.sub(r"-+","-",re.sub(r"[^a-z0-9]+","-",value)).strip("-")
        slug=slug_part(home)+"-vs-"+slug_part(away)+"-"+str(int(event_id))
        url="https://superbet.pl/kursy/pilka-nozna/"+slug
    parts=urlsplit(url)
    query=dict(parse_qsl(parts.query,keep_blank_values=True))
    query["marketGroup"]="111"
    return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),parts.fragment))

def superbet_ft_event_data(event_id,line=FT_CORNERS_LINE):
    """Return (price, detailed Superbet event) for a pre-match FT corners offer."""
    payload=superbet_api("/v3/pl-PL/events",{"events":str(event_id)})
    events=payload.get("events",[])
    if not events:
        return (None,None)
    event=events[0]
    fixture=event.get("fixture",{})
    offer_states=fixture.get("offer_state_status") or {}
    if offer_states and offer_states.get("1") not in (1,"1"):
        return (None,event)
    inplay=(event.get("inplay_stats_metadata") or {}).get("status")
    if inplay not in (None,"","NOT_STARTED"):
        return (None,event)
    for market in event.get("markets",[]):
        market_name=re.sub(r"\s+"," ",str(market.get("name","")).strip().lower())
        if any(term in market_name for term in ("połowa","1h","2h","drużyn")):
            continue
        if not re.fullmatch(r"(?:cały mecz\s*-\s*)?liczba rzutów rożnych(?:\s*-\s*cały mecz)?",market_name):
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
            return (price,event)
    return (None,event)

def superbet_ft_over_odds(event_id,line=FT_CORNERS_LINE):
    """Compatibility wrapper returning only the pre-match FT Over 9.5 price."""
    price,_event=superbet_ft_event_data(event_id,line)
    return price


# Open Matchday: darmowe, dzienne CSV z historią rożnych 1. połowy.
# Splity HT są dostępne tylko dla 10 wskazanych lig; bez próbki bot nie typuje.
# Open Matchday history is not used for FT tip selection.

def send(msg):
    if not TOKEN or not CHAT:
        logging.error("Brak TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID"); return False
    try:
        r=requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",data={"chat_id":CHAT,"text":msg,"disable_web_page_preview":True},timeout=20)
        if not r.ok: logging.error("Telegram %s %s",r.status_code,r.text[:250])
        return r.ok
    except Exception as e: logging.error("Telegram: %s",e); return False

def load_sent_event_ids():
    try:
        with open(SENT_IDS_FILE,"r",encoding="utf-8") as handle:
            values=json.load(handle)
        return {int(value) for value in values}
    except (FileNotFoundError,ValueError,TypeError):
        return set()

def save_sent_event_ids(event_ids):
    with open(SENT_IDS_FILE,"w",encoding="utf-8") as handle:
        json.dump(sorted({int(value) for value in event_ids}),handle,ensure_ascii=False)

def db_open():
    c=sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS picks(event_id INTEGER PRIMARY KEY,league TEXT,home TEXT,away TEXT,start_ts INTEGER,sent_at TEXT,status TEXT DEFAULT 'PENDING',ht_corners INTEGER,settled_at TEXT)""")
    columns={row[1] for row in c.execute("PRAGMA table_info(picks)").fetchall()}
    if "odds" not in columns:
        c.execute("ALTER TABLE picks ADD COLUMN odds REAL")
    if "market_type" not in columns:
        c.execute("ALTER TABLE picks ADD COLUMN market_type TEXT NOT NULL DEFAULT 'HT'")
    if "ft_corners" not in columns:
        c.execute("ALTER TABLE picks ADD COLUMN ft_corners INTEGER")
    if "league_corrected_at" not in columns:
        c.execute("ALTER TABLE picks ADD COLUMN league_corrected_at TEXT")
    c.execute("""CREATE TABLE IF NOT EXISTS monthly_reports(month TEXT PRIMARY KEY,sent_at TEXT NOT NULL)""")
    c.execute("UPDATE picks SET odds=1.92 WHERE event_id=15562733 AND odds IS NULL")
    c.execute("UPDATE picks SET odds=1.97 WHERE event_id=14572637 AND odds IS NULL")
    c.execute("UPDATE picks SET status='VOID',settled_at=? WHERE status='PENDING' AND market_type='HT'",
              (datetime.now(TZ).isoformat(),))
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
        msg=("✏️ KOREKTA TYPU — NAZWA LIGI\n\n"
             f"🏆 {league}\n⚽ {home} – {away}\n"
             f"🕒 Start: {kickoff:%d.%m.%Y %H:%M}\n\n"
             f"📌 Typ: Over {FT_CORNERS_LINE:.1f} rzutów rożnych FT\n"
             f"💹 Kurs Superbet: {float(odds):.2f}\n"
             f"💰 Stawka: {STAKE} zł\n"
             f"💸 Podatek 12%: {STAKE*TAX_RATE:.2f} zł\n"
             f"💵 Możliwa wypłata po podatku: {STAKE*(1-TAX_RATE)*float(odds):.2f} zł\n"
             f"📈 Możliwy zysk: {STAKE*(1-TAX_RATE)*float(odds)-STAKE:.2f} zł")
        if send(msg):
            c.execute("UPDATE picks SET league=?,league_corrected_at=? WHERE event_id=?",
                      (league,datetime.now(TZ).isoformat(),eid))
            c.commit()
            logging.info("Wysłano korektę nazwy ligi dla event %s: %s",eid,league)
        time.sleep(.2)

def superbet_live_event(event_id):
    """Fetch one event directly from Superbet, independently of market availability."""
    payload=superbet_api("/v3/pl-PL/events",{"events":str(event_id)})
    events=payload.get("events") or payload.get("data") or []
    if isinstance(events,dict):
        events=list(events.values())
    if not events:
        logging.info("Superbet: brak danych live dla event %s; pola odpowiedzi=%s",event_id,list(payload)[:12])
        return None
    return events[0]

def _first_value(obj,keys):
    for key in keys:
        if key in obj and obj[key] is not None:
            value=obj[key]
            if isinstance(value,dict):
                value=value.get("value",value.get("current",value.get("total")))
            if value is not None:
                return value
    return None

def _corner_pair_from_object(obj):
    """Read a home/away corner pair only from an explicitly named corner statistic."""
    if not isinstance(obj,dict):
        return None
    label=" ".join(str(obj.get(k,"")) for k in ("name","label","type","statistic","key","description","title","code")).lower()
    if not any(x in label for x in ("corner","corners","corner kick","rzut rożny","rzuty rożne","rożne")):
        return None
    home=_first_value(obj,("home","home_value","homeTeam","home_team","home_team_value","homeValue","team1","participant1"))
    away=_first_value(obj,("away","away_value","awayTeam","away_team","away_team_value","awayValue","team2","participant2"))
    try:
        return int(float(home)),int(float(away))
    except (TypeError,ValueError):
        return None

def superbet_ft_corners(event):
    """Read full-match corner statistics, excluding explicitly half-specific totals."""
    roots=[(k,event[k]) for k in ("inplay_stats","statistics","stats","event_stats","live_statistics") if event.get(k) is not None]
    found=[]
    def walk(node,path=()):
        if isinstance(node,dict):
            label=" ".join(path+(str(node.get("period","")),str(node.get("period_name","")),str(node.get("period_label","")),str(node.get("half","")),str(node.get("name","")),str(node.get("label","")),str(node.get("type","")),str(node.get("key","")))).lower()
            if any(x in label for x in ("first half","1st half","1h","first_half","first-half","second half","2nd half","2h","second_half","second-half","1 poł","2 poł","pierwsza poł","druga poł","period 1","period 2","half 1","half 2")): return
            pair=_corner_pair_from_object(node)
            if pair is not None: found.append((pair,label))
            keys={str(k).lower():v for k,v in node.items()}
            hk=next((k for k in keys if ("corner" in k or "roż" in k) and ("home" in k or "host" in k)),None)
            ak=next((k for k in keys if ("corner" in k or "roż" in k) and ("away" in k or "guest" in k)),None)
            if hk and ak:
                try: found.append(((int(float(keys[hk])),int(float(keys[ak]))),label+" "+hk+" "+ak))
                except (TypeError,ValueError): pass
            for key,value in node.items(): walk(value,path+(str(key).lower(),))
        elif isinstance(node,list):
            for item in node: walk(item,path)
    for name,root in roots: walk(root,(name,))
    if found:
        pair,label=max(found,key=lambda x:len(x[1]))
        logging.info("Superbet: rożne FT %s:%s (%s)",pair[0],pair[1],label[:150])
        return pair
    logging.info("Superbet: brak jawnych statystyk rożnych FT")
    return None

def superbet_event_phase(event):
    meta=event.get("inplay_stats_metadata") or {}
    fixture=event.get("fixture") or {}
    candidates=[]
    for source in (meta,fixture,event):
        if isinstance(source,dict):
            candidates.extend(str(source.get(k,"")) for k in (
                "event_status_label","period_status","status","status_name","period",
                "period_name","period_label","match_status","state","phase"
            ))
    labels=" ".join(candidates).lower().replace("_"," ").replace("-"," ")
    if any(x in labels for x in ("first half","1st half","1h","1 poł","pierwsza poł")):
        return "FIRST_HALF"
    if any(x in labels for x in ("half time","halftime","break","przerwa","ht")):
        return "HALFTIME"
    if any(x in labels for x in ("second half","2nd half","2h","2 poł","druga poł")):
        return "SECOND_HALF"
    if any(x in labels for x in ("finished","ended","full time","ft","complete")):
        return "FINISHED"
    # Some Superbet responses expose a numeric/integer period instead of a label.
    for source in (meta,fixture,event):
        if isinstance(source,dict):
            value=source.get("period_id",source.get("period_number"))
            if str(value)=="1": return "FIRST_HALF"
            if str(value)=="2": return "SECOND_HALF"
    return "UNKNOWN"


def sofascore_event_for_pick(home,away,start_ts):
    """Find the matching SofaScore fixture by teams and kickoff date (Superbet IDs are not SofaScore IDs)."""
    kickoff=datetime.fromtimestamp(int(start_ts),timezone.utc).astimezone(TZ)
    dates={kickoff.date().isoformat(),(kickoff.date()-timedelta(days=1)).isoformat()}
    best=None
    for day in dates:
        payload=api(f"/sport/football/scheduled-events/{day}")
        for item in payload.get("events",[]):
            h=(item.get("homeTeam") or {}).get("name","")
            a=(item.get("awayTeam") or {}).get("name","")
            ts=item.get("startTimestamp")
            if not h or not a or not ts or abs(int(ts)-int(start_ts))>4*3600:
                continue
            direct=team_similarity(home,h)+team_similarity(away,a)
            reverse=team_similarity(home,a)+team_similarity(away,h)
            score=max(direct,reverse)
            if score>=1.45 and (best is None or score>best[0]):
                best=(score,item)
    return best[1] if best else None

def sofascore_ft_corners(home,away,start_ts):
    """Read SofaScore ALL/full-match corner statistics."""
    event=sofascore_event_for_pick(home,away,start_ts)
    if not event: return None,None,None
    payload=api(f"/event/{event.get('id')}/statistics")
    stats=payload.get("statistics") or []
    if isinstance(stats,dict): stats=[stats]
    for period in stats:
        pname=str(period.get("period","")).lower().replace("_","").replace(" ","")
        if pname not in ("all","ft","fulltime","match","total"): continue
        for group in period.get("groups",[]):
            for item in group.get("statisticsItems",[]):
                name=str(item.get("name","")).lower()
                if "corner" not in name and "roż" not in name: continue
                try: return int(float(item.get("home"))),int(float(item.get("away"))),event
                except (TypeError,ValueError): continue
    return None,None,event

def settle(c,now):
    """Settle FT Over 9.5 at 10 corners immediately, otherwise at full time."""
    rows=c.execute("SELECT event_id,league,home,away,start_ts FROM picks WHERE status='PENDING' AND market_type='FT'").fetchall()
    for eid,league,home,away,start_ts in rows:
        event=superbet_live_event(eid)
        phase=superbet_event_phase(event) if event else "UNKNOWN"
        stats=superbet_ft_corners(event) if event else None
        ss_event=None
        if stats is not None:
            home_corners,away_corners=stats
        else:
            h,a,ss_event=sofascore_ft_corners(home,away,start_ts)
            if h is None or a is None:
                logging.info("Brak wiarygodnych statystyk FT dla %s — pozostaje PENDING",eid)
                continue
            home_corners,away_corners=h,a
        total=home_corners+away_corners
        if total<10:
            status=(ss_event or {}).get("status") or {}
            desc=str(status.get("description","")).lower()
            finished=(phase=="FINISHED" or str(status.get("type","")).lower()=="finished" or any(x in desc for x in ("finished","ended","full time")))
            if not finished: continue
        won=total>=10
        msg=(f"📊 ROZLICZENIE — ROGI FT\n\n🏆 {league}\n⚽ {home} – {away}\n"
             f"🚩 Rożne w całym meczu: {total}\n🎯 Over 9.5 FT: {'🟢 WYGRANY' if won else '🔴 PRZEGRANY'}\n"
             f"💰 Stawka: {STAKE} zł")
        c.execute("UPDATE picks SET status=?,ft_corners=?,settled_at=? WHERE event_id=?",
                  ("WON" if won else "LOST",total,datetime.now(TZ).isoformat(),eid))
        c.commit()
        if not send(msg): logging.error("Nie udało się wysłać rozliczenia Telegram dla event %s",eid)
        else: logging.info("Rozliczono typ FT event %s: %s rożnych — %s",eid,total,"WON" if won else "LOST")
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
        msg=(f"📅 MIESIĘCZNY BILANS — {month}\n\n"
             f"📊 Rozliczone typy: {decided}\n"
             f"🟢 Wygrane: {wins}\n🔴 Przegrane: {losses}\n"
             f"⚪ Anulowane: {voids}\n"
             f"🎯 Skuteczność: {accuracy:.1f}%\n"
             f"📈 Yield: {yield_pct:+.2f}%\n"
             f"💰 Stawka: {STAKE} zł / typ\n"
             f"💵 Obrót z kursami: {turnover:.2f} zł\n"
             f"🏦 Wynik: {profit:+.2f} zł ({result})")
        if missing:
            msg+=f"\n⚠️ Typy bez zapisanego kursu, pominięte w yield: {missing}"
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
    # Jednorazowy reset licznika na 05.11.2026 bez kasowania typów ani historii.
    reset_baseline=RESET_BASELINE_COUNT if key==RESET_BASELINE_DATE else 0
    count=max(0,count-reset_baseline)
    if count>=MAX_DAILY:
        logging.info("Dzienny limit osiągnięty: %s/%s — rozliczenie ponowi harmonogram co 5 minut",count,MAX_DAILY)
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
    # Load the official competition dictionary once before parallel event checks.
    superbet_tournament_names()
    resend_tips_with_corrected_league(c,sb_events)
    logging.info("Superbet: %d meczów piłkarskich przedmeczowych",len(sb_events))
    # Skanuj kursy równolegle: sekwencyjne odpytanie każdego z setek meczów
    # powodowało przekroczenie limitu czasu GitHub Actions.
    existing_ids={int(row[0]) for row in c.execute("SELECT event_id FROM picks").fetchall()}
    existing_ids.update(load_sent_event_ids())
    candidates=[]
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
        if int(event_id) in existing_ids:
            continue
        home,away=(x.strip() for x in parts)
        candidates.append((listed,int(event_id),st,home,away))

    logging.info("Do sprawdzenia kursów Superbet: %d meczów",len(candidates))
    def inspect_candidate(item):
        listed,event_id,st,home,away=item
        price,detailed_event=superbet_ft_event_data(event_id,FT_CORNERS_LINE)
        if price is None:
            return None
        # The detailed Superbet response may contain competition metadata
        # omitted from the daily fixture list. Prefer it, then use SofaScore.
        league=resolve_full_league(detailed_event or listed,home,away,st)
        if not league:
            league=resolve_full_league(listed,home,away,st)
        if not league:
            logging.warning("Pominięto %s - %s: nie udało się ustalić pełnej nazwy ligi",home,away)
            return None
        logging.info("KWALIFIKACJA O%.1f FT: %s - %s | %s @ %.2f",FT_CORNERS_LINE,home,away,league,price)
        return (price,st,event_id,home,away,price,league,detailed_event or listed)

    available=[]
    checked=len(candidates)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures=[pool.submit(inspect_candidate,item) for item in candidates]
        for future in as_completed(futures):
            try:
                result=future.result()
                if result is not None:
                    available.append(result)
            except Exception:
                logging.exception("Błąd podczas sprawdzania kursu jednego z meczów")
    market_candidates=len(available)
    logging.info("Skan kursów zakończony: sprawdzono %d meczów, znaleziono %d kandydatów z kursem O%.1f FT",
                 checked,market_candidates,FT_CORNERS_LINE)

    available.sort(key=lambda x:(x[0],x[1]))
    sent=0
    for price,st,event_id,home,away,fx,league,event_data in available:
        if count>=MAX_DAILY:
            break
        kickoff=datetime.fromtimestamp(st,timezone.utc).astimezone(TZ)
        tax=STAKE*TAX_RATE
        payout=STAKE*(1-TAX_RATE)*price
        profit=payout-STAKE
        prematch_url=superbet_prematch_event_url(event_data,home,away,event_id)
        msg=(f"🎯 ROGI FT\n"
             f"🏆 {league}\n"
             f"⚽ {home} – {away}\n"
             f"🕒 {kickoff:%d.%m.%Y} | {kickoff:%H:%M}\n"
             f"Over {FT_CORNERS_LINE:.1f} rożnych – kurs {price:.2f}\n"
             f"🔗 Superbet PRE-MATCH: {prematch_url}\n"
             f"Stawka: {STAKE} zł\n"
             f"Podatek 12%: {tax:.2f} zł\n"
             f"Możliwa wypłata po podatku: {payout:.2f} zł\n"
             f"Możliwy zysk: {profit:.2f} zł")
        if send(msg):
            c.execute("INSERT OR IGNORE INTO picks(event_id,league,home,away,start_ts,sent_at,status,odds,market_type) VALUES(?,?,?,?,?,?,'PENDING',?,'FT')",
                      (event_id,league,home,away,st,local.isoformat(),price))
            c.commit()
            existing_ids.add(int(event_id))
            save_sent_event_ids(existing_ids)
            count+=1
            sent+=1
            logging.info("Wysłano typ Superbet event %s kurs %.2f",event_id,price)
        else:
            logging.error("Nie udało się wysłać typu do Telegrama dla event %s",event_id)
        time.sleep(.3)

    logging.info("Skan zakończony — wysłano %s typów",sent)
    # GitHub Actions uruchamia skan co 5 minut; nie blokuj joba dodatkowym
    # 270-sekundowym pollingiem, który powodował nakładanie i anulowanie runów.
    c.close()

if __name__=="__main__": main()

# Manual activation: trigger the scheduled FT corners scan via GitHub Actions push.
