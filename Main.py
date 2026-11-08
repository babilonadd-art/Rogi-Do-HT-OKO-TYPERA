import os, time, sqlite3, logging, re, unicodedata, csv, io
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests

SUPERBET_API="https://production-superbet-offer-pl.freetls.fastly.net"
TZ=ZoneInfo("Europe/Warsaw")
DB="okotypera_ht.db"
TOKEN=os.getenv("TELEGRAM_BOT_TOKEN")
CHAT=os.getenv("TELEGRAM_CHAT_ID")
STAKE=10
MAX_DAILY_PICKS=2
# Jednorazowe wyzerowanie licznika dziennego od tej chwili; historia typów pozostaje zachowana.
DAILY_LIMIT_RESET_AT="2026-11-07T14:41:00+01:00"
SHORTLIST_SIZE=100  # maksymalnie 100 meczów wybieranych do szczegółowej analizy
HISTORY=8
MIN_SAMPLE=3
MIN_TEAM_AVG=2.2
MIN_COMBINED_AVG=5.5
MIN_HIT_RATE=0.60
HT_CORNERS_LINE=4.5
MIN_ODDS=1.70
MAX_ODDS=2.60
SB_TOURNAMENT_NAMES=None
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
sb=requests.Session()
sb.headers.update({
    "User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
    "Accept":"application/json,*/*",
    "Accept-Language":"pl-PL,pl;q=0.9,en;q=0.8",
    "Origin":"https://superbet.pl",
    "Referer":"https://superbet.pl/",
})

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
    raw=str(name or "").replace("ł","l").replace("Ł","L")
    value=unicodedata.normalize("NFKD",raw).encode("ascii","ignore").decode().lower()
    tokens=re.findall(r"[a-z0-9]+",value)
    tokens=[t for t in tokens if t not in {"fc","cf","sc","afc","club","the"}]
    compact="".join(tokens)
    # Common provider spellings and short names; canonical forms prevent false
    # "no history" results for the same club (e.g. Man City / Manchester City).
    aliases={
        "manchestercity":"mancity","mancity":"mancity",
        "manchesterunited":"manutd","manutd":"manutd",
        "nottinghamforest":"nottinghamforest","nottmforest":"nottinghamforest",
        "tottenhamhotspur":"spurs","tottenham":"spurs","spurs":"spurs",
        "parissaintgermain":"psg","psg":"psg",
        "istanbulbasaksehir":"basaksehir","basaksehir":"basaksehir",
        "wolverhamptonwanderers":"wolves","wolves":"wolves",
        "westhamunited":"westham","westham":"westham",
        "newcastleunited":"newcastle","newcastle":"newcastle",
        "atleticodemadrid":"atleticomadrid","atleticomadrid":"atleticomadrid",
        "borussiadortmund":"dortmund","dortmund":"dortmund",
        "bayernmunich":"bayernmunchen","bayernmunchen":"bayernmunchen",
        "internazionale":"inter","intermilan":"inter","inter":"inter",
        "realbetisbalompie":"realbetis","realbetis":"realbetis",
        "athleticbilbao":"athleticclub","athleticclub":"athleticclub",
    }
    return aliases.get(compact,compact)

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
    """Return a price only when this exact event has an active HT total-corners Over line."""
    payload=superbet_api("/v3/pl-PL/events",{"events":str(event_id)})
    events=payload.get("events",[])
    if not events:
        logging.info("Superbet: brak szczegółów wydarzenia %s",event_id)
        return None
    # Superbet's /events?events=<event_id> detail response may omit its own ID.
    # When it returns exactly one event, that response is scoped to the requested ID.
    # If an ID is present, however, it must match the requested event.
    if len(events)==1:
        event=events[0]
        returned_id=event.get("event_id") or event.get("id")
        if returned_id is not None and str(returned_id)!=str(event_id):
            logging.warning("Superbet: żądano event_id=%s, odpowiedź zawiera %s",event_id,returned_id)
            return None
    else:
        event=next((e for e in events if str(e.get("event_id") or e.get("id"))==str(event_id)),None)
        if event is None:
            logging.warning("Superbet: odpowiedź nie zawiera żądanego event_id=%s",event_id)
            return None
    fixture=event.get("fixture",{})
    offer_states=fixture.get("offer_state_status") or {}
    if offer_states and offer_states.get("1") not in (1,"1"):
        return None
    inplay=(event.get("inplay_stats_metadata") or {}).get("status")
    if inplay not in (None,"","NOT_STARTED"):
        return None

    for market in event.get("markets",[]):
        raw_name=str(market.get("name") or "")
        market_name=re.sub(r"\s+"," ",raw_name.strip().lower())
        # Market itself must explicitly describe corners AND the first half.
        has_corners=any(term in market_name for term in (
            "roż","rzutów rożnych","corner","corners"
        ))
        has_first_half=any(term in market_name for term in (
            "1. połowa","1 połowa","pierwsza połowa","1h","1st half",
            "first half","first-half","half 1"
        ))
        if not has_corners or not has_first_half:
            continue
        # Reject match-total and team-specific markets; no inferred period/market.
        if any(term in market_name for term in (
            "cały mecz","pełny mecz","full time","match total",
            "gospodarze","goście","home","away","drużyn"
        )):
            continue
        if market.get("status") not in (None,1,"1","ACTIVE","active"):
            continue

        for odd in market.get("odds",[]):
            meta=odd.get("metadata") or {}
            # The line must come from this exact selection's Superbet market data.
            raw_line=meta.get("special_bet_value")
            if raw_line is None:
                raw_line=odd.get("special_bet_value")
            try:
                odd_line=float(str(raw_line).replace(",","."))
                price=float(odd.get("price"))
            except (TypeError,ValueError):
                continue
            selection=" ".join(str(x or "") for x in (
                odd.get("name"),meta.get("name"),meta.get("info"),odd.get("label")
            )).lower().replace(",",".")
            if not re.search(r"\b(powyżej|over)\b",selection):
                continue
            if abs(odd_line-float(line))>0.001:
                continue
            if odd.get("status") not in (1,"1","ACTIVE","active"):
                continue
            if odd.get("display") is False:
                continue
            if str(meta.get("offer_state_id",1))!="1":
                continue
            if not MIN_ODDS <= price <= MAX_ODDS:
                logging.info("Superbet: dokładny rynek HT Over %.1f znaleziony, ale kurs %.2f poza zakresem (event %s)",line,price,event_id)
                continue
            logging.info("Superbet: potwierdzono event %s | rynek=%r | wybór=%r | linia=%.1f | kurs=%.2f",
                         event_id,raw_name,selection,odd_line,price)
            return price

    logging.info("Superbet: event %s nie ma aktywnego, dokładnego rynku HT rożne Over %.1f w kursie %.2f–%.2f",
                 event_id,line,MIN_ODDS,MAX_ODDS)
    return None


# Open Matchday: darmowe, dzienne CSV z historią rożnych 1. połowy.
# Splity HT są dostępne tylko dla 10 wskazanych lig; bez próbki bot nie typuje.
MATCHDAY_BASE="https://raw.githubusercontent.com/oladeeayo/open-matchday/master/data"
MATCHDAY_SEASON="2627"
MATCHDAY_LEAGUES={
    "angliapremierleague":"ENG_1_premier-league","englandpremierleague":"ENG_1_premier-league","premierleague":"ENG_1_premier-league",
    "angliachampionship":"ENG_2_championship","englandchampionship":"ENG_2_championship","championship":"ENG_2_championship",
    "anglialeagueone":"ENG_3_league-one","leagueone":"ENG_3_league-one","anglialeaguetwo":"ENG_4_league-two","leaguetwo":"ENG_4_league-two",
    "anglianationalleague":"ENG_5_national-league","nationalleagueengland":"ENG_5_national-league",
    "anglianationalleaguenorth":"ENG_6_national-league-north","nationalleaguenorth":"ENG_6_national-league-north",
    "anglianationalleaguesouth":"ENG_6_national-league-south","nationalleaguesouth":"ENG_6_national-league-south",
    "northernpremierleague":"ENG_7_northern-premier-league","eflcup":"ENG_CUP_efl-cup","facup":"ENG_CUP_fa-cup",
    "hiszpaniaprimeradivision":"ESP_1_la-liga","hiszpianalaliga":"ESP_1_la-liga","spainlaliga":"ESP_1_la-liga","laliga":"ESP_1_la-liga","primera":"ESP_1_la-liga",
    "hiszpaniasegundadivision":"ESP_2_segunda-division","segunda":"ESP_2_segunda-division","copadelrey":"ESP_CUP_copa-del-rey",
    "francjaligue1":"FRA_1_ligue-1","franceligue1":"FRA_1_ligue-1","ligue1":"FRA_1_ligue-1",
    "francjaligue2":"FRA_2_ligue-2","franceligue2":"FRA_2_ligue-2","ligue2":"FRA_2_ligue-2",
    "niemcybundesliga":"GER_1_bundesliga","germanybundesliga":"GER_1_bundesliga","bundesliga":"GER_1_bundesliga",
    "niemcy2bundesliga":"GER_2_2-bundesliga","zweitebundesliga":"GER_2_2-bundesliga","2bundesliga":"GER_2_2-bundesliga",
    "niemcy3liga":"GER_3_3-liga","drittliga":"GER_3_3-liga","3liga":"GER_3_3-liga","dfbpokal":"GER_CUP_dfb-pokal",
    "wlochyseriea":"ITA_1_serie-a","italyseriea":"ITA_1_serie-a","seriea":"ITA_1_serie-a",
    "wlochyserieb":"ITA_2_serie-b","italyserieb":"ITA_2_serie-b","serieb":"ITA_2_serie-b","coppaitalia":"ITA_CUP_coppa-italia",
    "holandiaeredivisie":"NED_1_eredivisie","netherlandseredivisie":"NED_1_eredivisie","eredivisie":"NED_1_eredivisie",
    "holandiaeerstedivisie":"NED_2_eerste-divisie","eerstedivisie":"NED_2_eerste-divisie","knvbeker":"NED_CUP_knvb-beker",
    "portugaliaprimeiraliga":"POR_1_primeira-liga","portugalprimeiraliga":"POR_1_primeira-liga","primeiraliga":"POR_1_primeira-liga","tacadeportugal":"POR_CUP_taca-de-portugal",
    "belgiaproleague":"BEL_1_pro-league","belgiumproleague":"BEL_1_pro-league","jupilerproleague":"BEL_1_pro-league",
    "belgiau21proleague":"BEL_U21_pro-league","u21proleague":"BEL_U21_pro-league",
    "grecjasuperleague":"GRE_1_super-league","greecesuperleague":"GRE_1_super-league","superleaguegreece":"GRE_1_super-league",
    "turcjasuperlig":"TUR_1_super-lig","turkeysuperlig":"TUR_1_super-lig","superlig":"TUR_1_super-lig",
    "polskaekstraklasa":"POL_1_ekstraklasa","polandekstraklasa":"POL_1_ekstraklasa","ekstraklasa":"POL_1_ekstraklasa",
    "polska1liga":"POL_2_division-1","poland1liga":"POL_2_division-1","polskadivision1":"POL_2_division-1",
    "polska2liga":"POL_3_division-2","poland2liga":"POL_3_division-2","polskadivision2":"POL_3_division-2",
    "daniasuperliga":"DEN_1_superliga","denmarksuperliga":"DEN_1_superliga",
    "austriabundesliga":"AUT_1_bundesliga","austriacup":"AUT_CUP_ofb-cup",
    "czechchanceliga":"CZE_1_chance-liga","czechiachanceliga":"CZE_1_chance-liga","chanceliga":"CZE_1_chance-liga",
    "swisssuperleague":"SUI_1_super-league","switzerlandsuperleague":"SUI_1_super-league","szwajcariasuperleague":"SUI_1_super-league","swisscup":"SUI_CUP_swiss-cup",
    "slovaknikeliga":"SVK_1_nike-liga","nikeliga":"SVK_1_nike-liga","slovenianprvaliga":"SVN_1_prva-liga","prvaliga":"SVN_1_prva-liga",
    "swedenallsvenskan":"SWE_1_allsvenskan","szwecjaallsvenskan":"SWE_1_allsvenskan","allsvenskan":"SWE_1_allsvenskan","norwayeliteserien":"NOR_1_eliteserien","norwegiaeliteserien":"NOR_1_eliteserien","eliteserien":"NOR_1_eliteserien",
    "finlandveikkausliiga":"FIN_1_veikkausliiga","veikkausliiga":"FIN_1_veikkausliiga",
    "scotlandpremiership":"SCO_1_premiership","scottishpremiership":"SCO_1_premiership","scotlandchampionship":"SCO_2_championship",
    "scotlandleagueone":"SCO_3_league-one","scotlandleaguetwo":"SCO_4_league-two","scottishcup":"SCO_CUP_scottish-cup","scottishleaguecup":"SCO_CUP_league-cup",
    "irelandfirstdivision":"IRE_2_first-division","irelandnationalleague":"IRE_3_national-league",
    "walescymrupremier":"WAL_1_cymru-premier","cymrupremier":"WAL_1_cymru-premier","cymrunorth":"WAL_2_cymru-north","cymrusouth":"WAL_3_cymru-south",
    "argentinaprimeradivision":"ARG_1_liga-profesional","argentinaprofesional":"ARG_1_liga-profesional","ligaprofesional":"ARG_1_liga-profesional",
    "argentinaprimeranacional":"ARG_2_primera-nacional","primeranacional":"ARG_2_primera-nacional",
    "brasilseriea":"BRA_1_serie-a","brasilbrasileirao":"BRA_1_serie-a","brasileirao":"BRA_1_serie-a","brasilserieb":"BRA_2_serie-b",
    "mexicoligamx":"MEX_1_liga-mx","ligamx":"MEX_1_liga-mx","mexicoligaexpansion":"MEX_2_liga-expansion","ligaexpansion":"MEX_2_liga-expansion",
    "usamls":"USA_1_mls","mls":"USA_1_mls","japanj1league":"JPN_1_j1-league","j1league":"JPN_1_j1-league",
    "australiaaleague":"AUS_1_a-league","aleague":"AUS_1_a-league","chinachinasuperleague":"CHN_1_super-league",
    "croatiahnl":"CRO_1_hnl","hnl":"CRO_1_hnl","cyprusfirstdivision":"CYP_1_first-division",
    "romanialiga1":"ROU_1_liga-1","liga1romania":"ROU_1_liga-1",
    "russiapremierliga":"RUS_1_premier-liga","ukrainepremierleague":"UKR_1_premier-league",
    "kazakhstanpremierleague":"KAZ_1_premier-league",
    "uefachampionsleague":"UEFA_1_champions-league","championsleague":"UEFA_1_champions-league",
    "uefaeuropaleague":"UEFA_1_europa-league","europaleague":"UEFA_1_europa-league",
    "uefaconferenceleague":"UEFA_1_conference-league","conferenceleague":"UEFA_1_conference-league",
}
_matchday_cache={}
_matchday_history_cache={}

def matchday_folder(league):
    # Dopasowanie wyłącznie pełnej, znormalizowanej nazwy ligi.
    # Dopasowanie fragmentu powodowało fałszywe przypisanie np.
    # "Malta - Premier League - Top 6" do angielskiej Premier League.
    key=normalized_team(league)
    return MATCHDAY_LEAGUES.get(key)

def load_matchday_results(league):
    folder=matchday_folder(league)
    if not folder:
        logging.info("Open Matchday: brak mapowania ligi z wynikami dla %s",league)
        return None
    if folder in _matchday_cache:
        return _matchday_cache[folder]
    url=f"{MATCHDAY_BASE}/{folder}/results_{MATCHDAY_SEASON}.csv"
    try:
        response=requests.get(url,headers={"User-Agent":"OkoTypera/1.0","Accept":"text/csv"},timeout=25)
        response.raise_for_status()
        rows=list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        _matchday_cache[folder]=rows
        logging.info("Open Matchday: pobrano %s historycznych meczów dla %s",len(rows),league)
        return rows
    except Exception as exc:
        logging.warning("Open Matchday: nie udało się pobrać historii %s: %s",league,exc)
        return None

def recent_team_ht_corner_history(team,rows,fixture_date):
    cache_key=(id(rows),normalized_team(team),fixture_date)
    if cache_key in _matchday_history_cache:
        return _matchday_history_cache[cache_key]
    matched=[]
    for row in rows:
        match_date=str(row.get("date") or "")
        if not match_date or match_date>=fixture_date:
            continue
        try:
            home_c=int(row["home_corners_1h"])
            away_c=int(row["away_corners_1h"])
        except (KeyError,TypeError,ValueError):
            continue
        hsim=team_similarity(team,row.get("home",""))
        assim=team_similarity(team,row.get("away",""))
        if hsim>=0.74 and hsim>assim+0.05:
            matched.append((match_date,home_c,away_c,home_c+away_c))
        elif assim>=0.74 and assim>hsim+0.05:
            matched.append((match_date,away_c,home_c,home_c+away_c))
    matched.sort(key=lambda item:item[0],reverse=True)
    result=matched[:HISTORY]
    _matchday_history_cache[cache_key]=result
    return result

def evaluate_matchday_quality(league,home,away,start_ts):
    rows=load_matchday_results(league)
    if rows is None:
        return None,"brak danych Open Matchday dla ligi"
    fixture_date=datetime.fromtimestamp(start_ts,timezone.utc).date().isoformat()
    home_history=recent_team_ht_corner_history(home,rows,fixture_date)
    away_history=recent_team_ht_corner_history(away,rows,fixture_date)
    if len(home_history)<MIN_SAMPLE or len(away_history)<MIN_SAMPLE:
        return None,(f"za mała próba: {home} {len(home_history)}/{HISTORY}, "
                     f"{away} {len(away_history)}/{HISTORY}")
    home_avg=sum(x[1] for x in home_history)/len(home_history)
    away_avg=sum(x[1] for x in away_history)/len(away_history)
    combined=[x[3] for x in home_history]+[x[3] for x in away_history]
    combined_avg=sum(combined)/len(combined)
    hit_rate=sum(1 for total in combined if total>HT_CORNERS_LINE)/len(combined)
    passed=(home_avg>=MIN_TEAM_AVG and away_avg>=MIN_TEAM_AVG
            and combined_avg>=MIN_COMBINED_AVG and hit_rate>=MIN_HIT_RATE)
    detail=(f"próba {len(home_history)}+{len(away_history)}, "
            f"średnie rożne HT drużyn {home_avg:.2f}/{away_avg:.2f}, "
            f"średnia łączna {combined_avg:.2f}, over 4.5 w {hit_rate*100:.0f}%")
    return passed,detail

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
    if "market_type" not in columns:
        c.execute("ALTER TABLE picks ADD COLUMN market_type TEXT NOT NULL DEFAULT 'HT'")
    if "ft_corners" not in columns:
        c.execute("ALTER TABLE picks ADD COLUMN ft_corners INTEGER")
    c.execute("UPDATE picks SET status='VOID',settled_at=? WHERE status='PENDING' AND market_type='FT'",
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
             f"📌 Typ: Over {HT_CORNERS_LINE:.1f} rzutów rożnych do HT\n"
             f"💹 Kurs Superbet: {float(odds):.2f}\n"
             f"💰 Stawka: {STAKE} zł")
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

def superbet_ht_corners(event):
    """Return only an explicitly identified first-half corner score; never use match totals."""
    roots=[]
    for key in ("inplay_stats","statistics","stats","event_stats","live_statistics"):
        value=event.get(key)
        if value is not None:
            roots.append((key,value))
    found=[]
    period_terms=("1h","1st","first half","first_half","first-half","period 1","period_1","half 1","1 poł","pierwsza poł","1. poł")
    def is_first_half(label):
        label=label.lower()
        return any(term in label for term in period_terms)
    def walk(node,path=(),period_hint=""):
        if isinstance(node,dict):
            period=str(node.get("period") or node.get("period_name") or node.get("period_label") or node.get("half") or period_hint).lower()
            here=" ".join(path+(period,str(node.get("name","")),str(node.get("label",""))))
            pair=_corner_pair_from_object(node)
            if pair is not None and is_first_half(here):
                found.append((pair,here))
            keys={str(k).lower():v for k,v in node.items()}
            home_key=next((k for k in keys if ("corner" in k or "roż" in k) and ("home" in k or "host" in k)),None)
            away_key=next((k for k in keys if ("corner" in k or "roż" in k) and ("away" in k or "guest" in k)),None)
            if home_key and away_key:
                try:
                    pair=(int(float(keys[home_key])),int(float(keys[away_key])))
                    label=" ".join(path+(period,home_key,away_key))
                    if is_first_half(label):
                        found.append((pair,label))
                except (TypeError,ValueError):
                    pass
            # Handle arrays of named stats and nested home/away value objects.
            for key,value in node.items():
                walk(value,path+(str(key).lower(),),period)
        elif isinstance(node,list):
            for item in node:
                walk(item,path,period_hint)
    for root_name,root in roots:
        walk(root,(root_name,))
    if found:
        # If duplicate representations exist, use the most deeply labelled first-half record.
        pair,label=max(found,key=lambda item:len(item[1]))
        logging.info("Superbet: odczytano rożne HT %s:%s (%s)",pair[0],pair[1],label[:160])
        return pair[0],pair[1],True
    logging.info("Superbet: nie znaleziono jawnych statystyk rożnych pierwszej połowy; dostępne pola=%s",[k for k in event if "stat" in k.lower()])
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


def superbet_match_url(event_id,home,away,start_ts):
    """Use a match URL supplied by Superbet; never query SofaScore for a fixture."""
    payload=superbet_api("/v3/pl-PL/events",{"events":str(event_id)})
    events=payload.get("events") or []
    event=events[0] if len(events)==1 else next(
        (e for e in events if str(e.get("event_id") or e.get("id"))==str(event_id)),None)
    if event:
        for obj in (event,event.get("fixture") or {}):
            for key in ("url","event_url","eventUrl","permalink","web_url","webUrl"):
                value=obj.get(key) if isinstance(obj,dict) else None
                if isinstance(value,str) and value.startswith("https://") and "superbet" in value.lower():
                    return value
    return "https://superbet.pl/zaklady-bukmacherskie/pilka-nozna"


def matchday_candidate_profile(league,home,away,start_ts):
    """Rank a fixture from recent HT-corner history; require a minimal sample only."""
    rows=load_matchday_results(league)
    if rows is None:
        return None,"brak danych Open Matchday dla ligi"
    fixture_date=datetime.fromtimestamp(start_ts,timezone.utc).date().isoformat()
    hh=recent_team_ht_corner_history(home,rows,fixture_date)
    ah=recent_team_ht_corner_history(away,rows,fixture_date)
    if len(hh)<MIN_SAMPLE or len(ah)<MIN_SAMPLE:
        return None,f"za mała próba: {home} {len(hh)}/{HISTORY}, {away} {len(ah)}/{HISTORY}"
    home_avg=sum(x[1] for x in hh)/len(hh)
    away_avg=sum(x[1] for x in ah)/len(ah)
    totals=[x[3] for x in hh]+[x[3] for x in ah]
    combined_avg=sum(totals)/len(totals)
    hit_rate=sum(1 for total in totals if total>HT_CORNERS_LINE)/len(totals)
    score=(hit_rate*100)+(min(home_avg,away_avg)*4)+(combined_avg*2)+(min(len(hh),len(ah))*0.5)
    detail=(f"próba {len(hh)}+{len(ah)}, średnie HT {home_avg:.2f}/{away_avg:.2f}, "
            f"średnia łączna {combined_avg:.2f}, over 4.5 w {hit_rate*100:.0f}%")
    return score,detail




# --- Kupony AKO: zwycięstwo gospodarzy/gości w całym meczu (1/2) ---
COUPON_MIN_ODDS=5.0
COUPON_MAX_ODDS=10.0
COUPON_LEGS=4
MIN_WINNER_ODDS=1.50
MAX_WINNER_ODDS=4.00


_FORM_CACHE = {}

def football_data_recent_form(league, team, fixture_ts):
    key = normalized_team(league)
    codes = {
        "premierleague":"E0", "championship":"E1", "leagueone":"E2", "leaguetwo":"E3",
        "bundesliga":"D1", "2bundesliga":"D2", "seriea":"I1", "laliga":"SP1",
        "ligue1":"F1", "eredivisie":"N1", "primeiraliga":"P1", "scotlandpremiership":"SC0",
        "englandpremierleague":"E0", "englandchampionship":"E1", "englandleagueone":"E2",
        "englandleaguetwo":"E3", "germanybundesliga":"D1", "germany2bundesliga":"D2",
        "italyseriea":"I1", "spainlaliga":"SP1", "franceligue1":"F1",
        "netherlandseredivisie":"N1", "portugalprimeiraliga":"P1"
    }
    code = next((v for k, v in codes.items() if k in key or key in k), None)
    if not code:
        return None, "liga poza bazą formy"
    d = datetime.fromtimestamp(fixture_ts, timezone.utc).date()
    y = d.year if d.month >= 7 else d.year - 1
    season = f"{y%100:02d}{(y+1)%100:02d}"
    cache_key = (code, season)
    if cache_key not in _FORM_CACHE:
        try:
            url = f"https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
            response = requests.get(url, headers={"User-Agent":"Mozilla/5.0","Accept":"text/csv"}, timeout=15)
            response.raise_for_status()
            _FORM_CACHE[cache_key] = list(csv.DictReader(io.StringIO(response.content.decode("latin-1"))))
            logging.info("Forma: pobrano %s wyników dla %s", len(_FORM_CACHE[cache_key]), code)
        except Exception as exc:
            logging.warning("Forma: brak danych %s/%s: %s", code, season, exc)
            _FORM_CACHE[cache_key] = None
    rows = _FORM_CACHE[cache_key]
    if not rows:
        return None, "brak aktualnych wyników"
    history = []
    for row in rows:
        raw = str(row.get("Date") or "").strip()
        parsed = None
        for fmt in ("%d/%m/%Y", "%d/%m/%y"):
            try:
                parsed = datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                pass
        if not parsed or parsed >= d:
            continue
        try:
            hg, ag = int(float(row.get("FTHG"))), int(float(row.get("FTAG")))
        except (TypeError, ValueError):
            continue
        home_name, away_name = row.get("HomeTeam") or "", row.get("AwayTeam") or ""
        if team_similarity(team, home_name) >= 0.84:
            result = "W" if hg > ag else ("D" if hg == ag else "L")
        elif team_similarity(team, away_name) >= 0.84:
            result = "W" if ag > hg else ("D" if ag == hg else "L")
        else:
            continue
        history.append((parsed, result))
    history.sort(key=lambda x: x[0], reverse=True)
    recent = history[:5]
    if len(recent) < 3:
        return None, f"za mała próba {len(recent)}/5"
    wins = sum(1 for _, result in recent if result == "W")
    draws = sum(1 for _, result in recent if result == "D")
    losses = sum(1 for _, result in recent if result == "L")
    return {"wins": wins, "draws": draws, "losses": losses}, f"{wins}Z-{draws}R-{losses}P z {len(recent)}"

_MATCHDAY_FORM_CACHE = {}

def matchday_recent_form(league, team, fixture_ts):
    """Read recent FT results from Open Matchday's broad 75-competition CSV set."""
    fixture_date = datetime.fromtimestamp(fixture_ts, timezone.utc).date().isoformat()
    cache_key = (normalized_team(league), normalized_team(team), fixture_date)
    if cache_key in _MATCHDAY_FORM_CACHE:
        return _MATCHDAY_FORM_CACHE[cache_key]
    rows = load_matchday_results(league)
    if not rows:
        result = (None, "brak danych Open Matchday")
        _MATCHDAY_FORM_CACHE[cache_key] = result
        return result

    history = []
    for row in rows:
        match_date = str(row.get("date") or "")[:10]
        if not match_date or match_date >= fixture_date:
            continue
        try:
            home_goals = int(float(row.get("ft_home")))
            away_goals = int(float(row.get("ft_away")))
        except (TypeError, ValueError):
            continue
        home_name = row.get("home") or ""
        away_name = row.get("away") or ""
        home_similarity = team_similarity(team, home_name)
        away_similarity = team_similarity(team, away_name)
        if home_similarity >= 0.82 and home_similarity > away_similarity + 0.04:
            outcome = "W" if home_goals > away_goals else ("D" if home_goals == away_goals else "L")
        elif away_similarity >= 0.82 and away_similarity > home_similarity + 0.04:
            outcome = "W" if away_goals > home_goals else ("D" if away_goals == home_goals else "L")
        else:
            continue
        history.append((match_date, outcome))

    history.sort(key=lambda item: item[0], reverse=True)
    recent = history[:5]
    if len(recent) < 3:
        result = (None, f"Open Matchday: za mała próba {len(recent)}/5")
    else:
        wins = sum(1 for _, outcome in recent if outcome == "W")
        draws = sum(1 for _, outcome in recent if outcome == "D")
        losses = sum(1 for _, outcome in recent if outcome == "L")
        result = ({"wins": wins, "draws": draws, "losses": losses},
                  f"{wins}Z-{draws}R-{losses}P z {len(recent)} (Open Matchday)")
    _MATCHDAY_FORM_CACHE[cache_key] = result
    return result


def recent_team_form(league, team, fixture_ts):
    # Broad Open Matchday results first; use football-data.co.uk as a fallback.
    form, detail = matchday_recent_form(league, team, fixture_ts)
    if form:
        return form, detail
    fallback, fallback_detail = football_data_recent_form(league, team, fixture_ts)
    if fallback:
        return fallback, f"{fallback_detail} (football-data.co.uk)"
    return None, f"{detail}; fallback: {fallback_detail}"


def winner_form_passes(league, home, away, start_ts, selection):
    chosen = home if selection == "1" else away
    opponent = away if selection == "1" else home
    cf, cd = recent_team_form(league, chosen, start_ts)
    of, od = recent_team_form(league, opponent, start_ts)
    if not cf or not of:
        return False, f"brak danych: {chosen} {cd}; rywal {od}"
    if cf["wins"] < 2:
        return False, f"{chosen}: forma za słaba — mniej niż 2 wygrane ({cd})"
    if cf["wins"] < of["wins"]:
        return False, f"rywal ma lepszą formę ({cd}; rywal {od})"
    return True, f"{chosen} {cd}; rywal {od}"

def winner_market_selection(event_id,home,away,league,start_ts):
    """Return an active pre-match 1X2 home/away selection from the exact Superbet event."""
    payload=superbet_api("/v3/pl-PL/events",{"events":str(event_id)})
    events=payload.get("events") or []
    if not events:
        return None
    if len(events)==1:
        event=events[0]
        returned_id=event.get("event_id") or event.get("id")
        if returned_id is not None and str(returned_id)!=str(event_id):
            return None
    else:
        event=next((e for e in events if str(e.get("event_id") or e.get("id"))==str(event_id)),None)
        if event is None:
            return None
    fixture=event.get("fixture") or {}
    offer_states=fixture.get("offer_state_status") or {}
    if offer_states and offer_states.get("1") not in (1,"1"):
        return None
    phase=(event.get("inplay_stats_metadata") or {}).get("status")
    if phase not in (None,"","NOT_STARTED"):
        return None

    found=[]
    for market in event.get("markets",[]) or []:
        raw=str(market.get("name") or "")
        name=re.sub(r"\s+"," ",raw.strip().lower())
        if market.get("status") not in (None,1,"1","ACTIVE","active"):
            continue
        if any(x in name for x in ("1. poł","1 poł","pierwsza poł","first half","1st half","2. poł","second half","2nd half","roż","corner","rzutów rożnych","double chance","podwójna szansa","draw no bet","bez remisu","handicap")):
            continue
        is_result=("1x2" in name or "wynik meczu" in name or "match result" in name or
                   "full time result" in name or "full-time result" in name or
                   ("zwycięzca" in name and "meczu" in name) or
                   ("zwyciezca" in name and "meczu" in name))
        if not is_result:
            continue
        for odd in market.get("odds",[]) or []:
            meta=odd.get("metadata") or {}
            if odd.get("status") not in (1,"1","ACTIVE","active") or odd.get("display") is False:
                continue
            if str(meta.get("offer_state_id",1))!="1":
                continue
            try:
                price=float(odd.get("price"))
            except (TypeError,ValueError):
                continue
            if not MIN_WINNER_ODDS<=price<=MAX_WINNER_ODDS:
                continue
            labels=[odd.get("name"),odd.get("label"),meta.get("name"),meta.get("info")]
            labels=[str(x).strip().lower() for x in labels if x is not None]
            selection=None
            for label in labels:
                compact=re.sub(r"[^a-z0-9ąćęłńóśźż]+","",label)
                if compact in ("1","1.","gospodarze","gospodarz","home","hometeam"):
                    selection="1"; break
                if compact in ("2","2.","goście","goscie","gość","gosc","away","awayteam"):
                    selection="2"; break
                if team_similarity(label,home)>=0.84:
                    selection="1"; break
                if team_similarity(label,away)>=0.84:
                    selection="2"; break
            if selection:
                found.append({"selection":selection,"odds":price})
    if not found:
        return None
    qualified=[]
    for option in found:
        passed,detail=winner_form_passes(league,home,away,start_ts,option["selection"])
        logging.info("Forma 1X2 %s - %s | %s | %s",home,away,option["selection"],detail)
        if passed:
            option["form"]=detail
            qualified.append(option)
    if not qualified:
        return None
    # When both sides pass the form check, prefer the lower price:
    # it is the more conservative leg and helps keep the 4-fold inside 5–10.
    qualified.sort(key=lambda x:(x["odds"],x["selection"]))
    return qualified[0]


def choose_four_leg_coupon(candidates):
    """Find four distinct fixtures whose combined decimal odds are within 5–10."""
    from itertools import combinations
    valid=[]
    for group in combinations(candidates,COUPON_LEGS):
        product=1.0
        for item in group:
            product*=item["odds"]
        if COUPON_MIN_ODDS<=product<=COUPON_MAX_ODDS:
            # Aim near 8.5 within the requested 5–10 range, then prefer earlier kickoff and major leagues.
            score=abs(product-8.5)
            score+=sum(item.get("priority",20) for item in group)*0.001
            score+=sum(item["start_ts"] for item in group)*1e-12
            valid.append((score,product,group))
    if not valid:
        return None
    valid.sort(key=lambda x:x[0])
    _,product,group=valid[0]
    return list(group),round(product,2)


def ensure_coupon_schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS coupons(
        coupon_id TEXT PRIMARY KEY,sent_at TEXT NOT NULL,odds REAL NOT NULL,
        stake REAL NOT NULL,status TEXT NOT NULL DEFAULT 'PENDING',settled_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS coupon_legs(
        event_id INTEGER PRIMARY KEY,coupon_id TEXT NOT NULL,league TEXT,home TEXT,away TEXT,
        start_ts INTEGER NOT NULL,selection TEXT NOT NULL,odds REAL NOT NULL,
        result TEXT,FOREIGN KEY(coupon_id) REFERENCES coupons(coupon_id))""")
    c.commit()


def settle_ako_coupons(c):
    """Settle FT 1X2 coupons from football-data.co.uk CSV; no SofaScore requests."""
    rows=c.execute("SELECT coupon_id,odds,stake,status FROM coupons WHERE status='PENDING'").fetchall()
    codes={"premierleague":"E0","championship":"E1","leagueone":"E2","leaguetwo":"E3",
           "bundesliga":"D1","2bundesliga":"D2","seriea":"I1","laliga":"SP1","ligue1":"F1",
           "eredivisie":"N1","primeiraliga":"P1","scotlandpremiership":"SC0",
           "englandpremierleague":"E0","englandchampionship":"E1","englandleagueone":"E2",
           "englandleaguetwo":"E3","germanybundesliga":"D1","germany2bundesliga":"D2",
           "italyseriea":"I1","spainlaliga":"SP1","franceligue1":"F1",
           "netherlandseredivisie":"N1","portugalprimeiraliga":"P1"}
    for coupon_id,total_odds,stake,status in rows:
        legs=c.execute("""SELECT event_id,league,home,away,start_ts,selection,result FROM coupon_legs
                          WHERE coupon_id=? ORDER BY start_ts""",(coupon_id,)).fetchall()
        lost=False; all_won=True
        for event_id,league,home,away,start_ts,selection,result in legs:
            if result in ("WON","LOST"):
                lost |= result=="LOST"; all_won &= result=="WON"; continue
            key=normalized_team(league)
            code=next((v for k,v in codes.items() if k in key or key in k),None)
            if not code:
                logging.info("Rozliczenie %s: brak kodu CSV ligi %s",coupon_id,league); all_won=False; continue
            d=datetime.fromtimestamp(start_ts,timezone.utc).date()
            y=d.year if d.month>=7 else d.year-1
            season=f"{y%100:02d}{(y+1)%100:02d}"; cache_key=(code,season)
            if cache_key not in _FORM_CACHE:
                try:
                    url=f"https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
                    response=requests.get(url,headers={"User-Agent":"Mozilla/5.0","Accept":"text/csv"},timeout=15)
                    response.raise_for_status()
                    _FORM_CACHE[cache_key]=list(csv.DictReader(io.StringIO(response.content.decode("latin-1"))))
                except Exception as exc:
                    logging.warning("CSV wyników %s: %s",league,exc); _FORM_CACHE[cache_key]=None
            data=_FORM_CACHE.get(cache_key)
            match=None
            for row in data or []:
                parsed=None
                for fmt in ("%d/%m/%Y","%d/%m/%y"):
                    try: parsed=datetime.strptime(str(row.get("Date") or "").strip(),fmt).date(); break
                    except ValueError: pass
                if parsed!=d: continue
                hname=row.get("HomeTeam") or ""; aname=row.get("AwayTeam") or ""
                direct=team_similarity(home,hname)+team_similarity(away,aname)
                reverse=team_similarity(home,aname)+team_similarity(away,hname)
                if max(direct,reverse)>=1.65: match=(row,direct>=reverse); break
            if not match:
                logging.info("Rozliczenie %s: brak wyniku CSV dla %s - %s; PENDING",coupon_id,home,away)
                all_won=False; continue
            row,direct=match
            try: hg=int(float(row["FTHG"])); ag=int(float(row["FTAG"]))
            except (KeyError,TypeError,ValueError): all_won=False; continue
            if not direct: hg,ag=ag,hg
            actual="1" if hg>ag else ("2" if ag>hg else "X")
            leg_result="WON" if actual==selection else "LOST"
            c.execute("UPDATE coupon_legs SET result=? WHERE event_id=?",(leg_result,event_id))
            lost |= leg_result=="LOST"; all_won &= leg_result=="WON"
        if lost: final="LOST"
        elif all_won and len(legs)==COUPON_LEGS: final="WON"
        else: c.commit(); continue
        c.execute("UPDATE coupons SET status=?,settled_at=? WHERE coupon_id=?",
                  (final,datetime.now(TZ).isoformat(),coupon_id)); c.commit()
        profit=float(stake)*(float(total_odds)-1.0) if final=="WON" else -float(stake)
        msg=(f"📊 ROZLICZENIE KUPONU AKO — {coupon_id}\n\n"
             f"🎯 4 zwycięstwa drużyn (1/2)\n💹 Kurs łączny: {float(total_odds):.2f}\n"
             f"💰 Stawka: {float(stake):.2f} zł\n"
             f"{'🟢 KUPON WYGRANY' if final=='WON' else '🔴 KUPON PRZEGRANY'}\n"
             f"🏦 Wynik: {profit:+.2f} zł")
        if not send(msg): logging.error("Nie udało się wysłać rozliczenia kuponu %s",coupon_id)


def settle_ht_picks(c):
    """Settle only HT corner picks from the matching Open Matchday result row."""
    pending=c.execute(
        "SELECT event_id,league,home,away,start_ts FROM picks WHERE status='PENDING' AND market_type='HT'"
    ).fetchall()
    now_ts=int(datetime.now(timezone.utc).timestamp())
    for event_id,league,home,away,start_ts in pending:
        # Give the match time to finish and the result feed time to update.
        if now_ts < int(start_ts)+3*60*60:
            continue
        rows=load_matchday_results(league)
        if not rows:
            continue
        match_date=datetime.fromtimestamp(start_ts,timezone.utc).date().isoformat()
        found=None
        for row in rows:
            if str(row.get("date") or "")[:10] != match_date:
                continue
            rh=row.get("home") or ""
            ra=row.get("away") or ""
            direct=team_similarity(home,rh)+team_similarity(away,ra)
            reverse=team_similarity(home,ra)+team_similarity(away,rh)
            if max(direct,reverse) < 1.65:
                continue
            try:
                hc=int(float(row.get("home_corners_1h")))
                ac=int(float(row.get("away_corners_1h")))
            except (TypeError,ValueError):
                continue
            found=(hc+ac,direct>=reverse)
            break
        if not found:
            logging.info("Rozliczenie HT: brak potwierdzonych danych dla %s - %s",home,away)
            continue
        total,_=found
        result="WON" if total>=5 else "LOST"
        c.execute("UPDATE picks SET status=?,ht_corners=?,settled_at=? WHERE event_id=?",
                  (result,total,datetime.now(TZ).isoformat(),event_id))
        c.commit()
        odds_row=c.execute("SELECT odds FROM picks WHERE event_id=?",(event_id,)).fetchone()
        odds=float(odds_row[0]) if odds_row and odds_row[0] is not None else 0.0
        profit=STAKE*(odds-1.0) if result=="WON" and odds>0 else (-STAKE if result=="LOST" else 0.0)
        msg=(f"📊 ROZLICZENIE — ROGI HT\n\n⚽ {home} – {away}\n"
             f"📌 Over 4.5 rożnych do przerwy\n"
             f"🚩 Rożne HT: {total}\n💹 Kurs: {odds:.2f}\n"
             f"{'🟢 WYGRANY' if result=='WON' else '🔴 PRZEGRANY'}\n"
             f"💰 Stawka: {STAKE} zł\n🏦 Wynik: {profit:+.2f} zł")
        if not send(msg):
            logging.error("Nie udało się wysłać rozliczenia HT event=%s",event_id)


def settle_ht_picks(c):
    """Settle only HT corner picks from the matching Open Matchday result row."""
    pending=c.execute(
        "SELECT event_id,league,home,away,start_ts FROM picks WHERE status='PENDING' AND market_type='HT'"
    ).fetchall()
    now_ts=int(datetime.now(timezone.utc).timestamp())
    for event_id,league,home,away,start_ts in pending:
        if now_ts < int(start_ts)+3*60*60:
            continue
        rows=load_matchday_results(league)
        if not rows:
            continue
        match_date=datetime.fromtimestamp(start_ts,timezone.utc).date().isoformat()
        found=None
        for row in rows:
            if str(row.get("date") or "")[:10] != match_date:
                continue
            rh=row.get("home") or ""
            ra=row.get("away") or ""
            direct=team_similarity(home,rh)+team_similarity(away,ra)
            reverse=team_similarity(home,ra)+team_similarity(away,rh)
            if max(direct,reverse) < 1.65:
                continue
            try:
                hc=int(float(row.get("home_corners_1h")))
                ac=int(float(row.get("away_corners_1h")))
            except (TypeError,ValueError):
                continue
            found=(hc+ac,direct>=reverse)
            break
        if not found:
            logging.info("Rozliczenie HT: brak potwierdzonych danych dla %s - %s",home,away)
            continue
        total,_=found
        result="WON" if total>=5 else "LOST"
        c.execute("UPDATE picks SET status=?,ht_corners=?,settled_at=? WHERE event_id=?",
                  (result,total,datetime.now(TZ).isoformat(),event_id))
        c.commit()
        odds_row=c.execute("SELECT odds FROM picks WHERE event_id=?",(event_id,)).fetchone()
        odds=float(odds_row[0]) if odds_row and odds_row[0] is not None else 0.0
        profit=STAKE*(odds-1.0) if result=="WON" and odds>0 else (-STAKE if result=="LOST" else 0.0)
        msg=(f"📊 ROZLICZENIE — ROGI HT\n\n⚽ {home} – {away}\n"
             f"📌 Over 4.5 rożnych do przerwy\n"
             f"🚩 Rożne HT: {total}\n💹 Kurs: {odds:.2f}\n"
             f"{'🟢 WYGRANY' if result=='WON' else '🔴 PRZEGRANY'}\n"
             f"💰 Stawka: {STAKE} zł\n🏦 Wynik: {profit:+.2f} zł")
        if not send(msg):
            logging.error("Nie udało się wysłać rozliczenia HT event=%s",event_id)


def main():
    if not TOKEN or not CHAT:
        logging.error("Brak TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        return
    c=db_open()
    settle_ht_picks(c)
    local=datetime.now(TZ)
    day_key=local.date().isoformat()
    sent_today=c.execute(
        "SELECT COUNT(*) FROM picks WHERE sent_at LIKE ? AND market_type='HT'",
        (day_key+"%",)
    ).fetchone()[0]
    if sent_today >= MAX_DAILY_PICKS:
        logging.info("Dzienny limit typów HT osiągnięty: %s/%s",sent_today,MAX_DAILY_PICKS)
        c.close()
        return

    day_start=datetime.combine(local.date(),datetime.min.time(),TZ)
    day_end=datetime.combine(local.date(),datetime.max.time(),TZ)
    now=datetime.now(timezone.utc)
    start=max(now,day_start.astimezone(timezone.utc))
    end=day_end.astimezone(timezone.utc)
    if start>=end:
        logging.info("Brak pozostałych meczów przedmeczowych na dziś")
        c.close()
        return

    events=superbet_football_events(start,end)
    superbet_tournament_names()
    league_priority={
        "ENG_1_premier-league":0,"ESP_1_la-liga":1,"GER_1_bundesliga":2,
        "ITA_1_serie-a":3,"FRA_1_ligue-1":4,"NED_1_eredivisie":5,
        "POR_1_primeira-liga":6,"UEFA_1_champions-league":7,
        "UEFA_1_europa-league":8,"UEFA_1_conference-league":9
    }
    used={int(row[0]) for row in c.execute("SELECT event_id FROM picks").fetchall()}
    raw=[]
    for listed in events:
        fx=listed.get("fixture") or {}
        event_id=listed.get("event_id")
        if not event_id:
            continue
        try:
            kickoff=int(datetime.fromisoformat(str(fx["utc_date"]).replace("Z","+00:00")).timestamp())
        except Exception:
            continue
        if not int(start.timestamp())<=kickoff<=int(end.timestamp()):
            continue
        parts=str(fx.get("event_name") or "").split("·",1)
        if len(parts)!=2:
            continue
        home,away=(x.strip() for x in parts)
        league=extract_league_name(listed) or "Liga niepodana przez Superbet"
        folder=matchday_folder(league)
        raw.append((league_priority.get(folder,20),kickoff,int(event_id),home,away,league))
    raw=[item for item in raw if item[2] not in used]
    logging.info("Superbet: %s meczów do oceny po odjęciu już użytych",len(raw))

    profiled=[]
    for priority,kickoff,eid,home,away,league in raw:
        quality,quality_detail=evaluate_matchday_quality(league,home,away,kickoff)
        if quality is not True:
            logging.info("Odrzucono %s - %s | %s",home,away,quality_detail)
            continue
        score,profile_detail=matchday_candidate_profile(league,home,away,kickoff)
        if score is None:
            continue
        profiled.append({
            "priority":priority,"start_ts":kickoff,"event_id":eid,"home":home,
            "away":away,"league":league,"score":score,"profile":profile_detail
        })
    profiled.sort(key=lambda x:(-x["score"],x["priority"],x["start_ts"]))
    shortlist=profiled[:SHORTLIST_SIZE]
    logging.info("Jakość historii: %s; do kontroli kursów TOP %s",len(profiled),len(shortlist))

    available=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures=[pool.submit(superbet_ht_over_odds,item["event_id"],HT_CORNERS_LINE) for item in shortlist]
        for item,future in zip(shortlist,futures):
            try:
                price=future.result()
            except Exception:
                logging.exception("Błąd pobierania kursu HT")
                price=None
            if price is not None:
                item["odds"]=price
                available.append(item)
    available.sort(key=lambda x:(-x["score"],x["priority"],x["start_ts"]))
    remaining=MAX_DAILY_PICKS-sent_today
    selected=available[:remaining]
    if not selected:
        logging.info("Brak meczów spełniających jakość historii i kurs 1.70–2.60; bez typowania na siłę")
        c.close()
        return

    for item in selected:
        fresh=superbet_ht_over_odds(item["event_id"],HT_CORNERS_LINE)
        if fresh is None:
            logging.info("Pominięto %s - %s: rynek HT zniknął lub kurs wyszedł poza zakres",item["home"],item["away"])
            continue
        kickoff=datetime.fromtimestamp(item["start_ts"],timezone.utc).astimezone(TZ)
        link=superbet_match_url(item["event_id"],item["home"],item["away"],item["start_ts"])
        msg=(f"🕒 {kickoff:%d.%m.%Y %H:%M}\n"
             f"🏆 {item['league']}\n"
             f"⚽ {item['home']} – {item['away']}\n"
             f"📌 Typ: Over 4.5 rzutów rożnych do HT\n"
             f"💹 Kurs Superbet: {fresh:.2f}\n"
             f"📊 Analiza HT: {item['profile']}\n"
             f"💰 Stawka informacyjna: {STAKE} zł\n"
             f"🔗 Mecz: {link}")
        if send(msg):
            c.execute("""INSERT OR IGNORE INTO picks
                (event_id,league,home,away,start_ts,sent_at,status,odds,market_type)
                VALUES(?,?,?,?,?,?,'PENDING',?,'HT')""",
                (item["event_id"],item["league"],item["home"],item["away"],
                 item["start_ts"],local.isoformat(),fresh))
            c.commit()
            logging.info("Wysłano typ HT %s - %s | kurs %.2f | score %.2f",
                         item["home"],item["away"],fresh,item["score"])
        else:
            logging.error("Nie udało się wysłać typu HT %s - %s",item["home"],item["away"])
        time.sleep(.3)
    c.close()


if __name__=="__main__": main()
