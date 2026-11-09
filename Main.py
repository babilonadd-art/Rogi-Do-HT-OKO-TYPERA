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
FT_CORNERS_LINE=8.5
FT_HISTORY=8
FT_MIN_SAMPLE=5
FT_MIN_TEAM_AVG=3.5
FT_MIN_COMBINED_AVG=9.5
FT_MIN_HIT_RATE=0.60
SHORTLIST_SIZE=100  # maksymalnie 100 meczów wybieranych do szczegółowej analizy
HISTORY=8
MIN_SAMPLE=3
MIN_TEAM_AVG=1.5
MIN_COMBINED_AVG=4.5
MIN_HIT_RATE=0.50
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

def superbet_events_from_payload(payload):
    """Normalize Superbet event-list envelopes without silently dropping valid fixtures."""
    events=(payload or {}).get("events") or (payload or {}).get("data") or []
    if isinstance(events,dict):
        events=list(events.values())
    return [event for event in events if isinstance(event,dict)] if isinstance(events,list) else []

def superbet_event_is_prematch(event):
    fixture=event.get("fixture") or {}
    sport_id=fixture.get("sport_id",event.get("sport_id"))
    if sport_id is not None and str(sport_id)!="5":
        return False
    # Some Superbet event-list responses omit these optional status fields entirely.
    # Missing status is not evidence that an event is closed or already live.
    offer_states=fixture.get("offer_state_status") or event.get("offer_state_status")
    if isinstance(offer_states,dict) and "1" in offer_states:
        if offer_states.get("1") not in (1,"1",True,"ACTIVE","active"):
            return False
    phase=(event.get("inplay_stats_metadata") or {}).get("status")
    phase=str(phase or "").strip().upper()
    if phase in {"LIVE","INPLAY","IN_PLAY","IN-PLAY","STARTED","IN_PROGRESS","FINISHED","ENDED"}:
        return False
    return True

def superbet_football_events(start,end):
    start_utc=utc_hour(start)
    end_utc=utc_hour(end,round_up=True)
    payload=superbet_api("/v3/pl-PL/events",{
        "startDate":start_utc.isoformat().replace("+00:00","Z"),
        "endDate":end_utc.isoformat().replace("+00:00","Z"),
    })
    events=superbet_events_from_payload(payload)
    result=[]
    for event in events:
        if not superbet_event_is_prematch(event):
            continue
        fixture=event.get("fixture") or {}
        try:
            kickoff=datetime.fromisoformat(str(fixture.get("utc_date") or "").replace("Z","+00:00"))
        except ValueError:
            continue
        if kickoff.tzinfo is None:
            kickoff=kickoff.replace(tzinfo=timezone.utc)
        if not start.astimezone(timezone.utc)<=kickoff.astimezone(timezone.utc)<=end.astimezone(timezone.utc):
            continue
        result.append(event)
    logging.info("Superbet: wydarzenia %s, piłka nożna przedmeczowo %s",len(events),len(result))
    if not result:
        logging.warning("Superbet: brak wydarzeń po filtrach; klucze odpowiedzi=%s; przykładowe pola fixture=%s",
                        list((payload or {}).keys()),
                        list((events[0].get("fixture") or {}).keys()) if events else "brak wydarzeń")
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

    # Compare meaningful name tokens as well as compact strings. Superbet often
    # shortens clubs (Schalke) while Open Matchday uses formal names (FC Schalke 04).
    noise={"fc","cf","sc","afc","club","the","tsg","sv","vfb","rb",
           "1899","1893","1900","1904","1907","04","07","1"}
    ta={t for t in re.findall(r"[a-z0-9]+",str(a).lower()) if t not in noise}
    tb={t for t in re.findall(r"[a-z0-9]+",str(b).lower()) if t not in noise}
    generic={"united","city","town","sporting","athletic","real"}
    if ta and tb and (ta.issubset(tb) or tb.issubset(ta)):
        shorter=ta if len(ta)<=len(tb) else tb
        if not (len(shorter)==1 and next(iter(shorter)) in generic):
            return 0.96
    if x in y or y in x:
        return max(0.78,min(len(x),len(y))/max(len(x),len(y)))
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

def superbet_ft_corners_over_odds(event_id,line=FT_CORNERS_LINE):
    """Return True only when the exact active FT match-corners Over line exists; never read odds."""
    payload=superbet_api("/v3/pl-PL/events",{"events":str(event_id)})
    events=payload.get("events",[])
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
    if (event.get("inplay_stats_metadata") or {}).get("status") not in (None,"","NOT_STARTED"):
        return None
    for market in event.get("markets") or []:
        name=re.sub(r"\s+"," ",str(market.get("name") or "").strip().lower())
        has_corners=any(term in name for term in ("corner","corners","roż","rzutów rożnych","suma rzutów"))
        if not has_corners:
            continue
        # Reject all half markets and team-only totals; accept only the match total.
        if any(term in name for term in ("1st half","first half","1. połowa","1 połowa","pierwsza połowa",
                                         "2nd half","second half","2. połowa","2 połowa","druga połowa",
                                         "home","away","gospodarze","goście","drużyn","team total")):
            continue
        if market.get("status") not in (None,1,"1","ACTIVE","active"):
            continue
        for odd in market.get("odds") or []:
            meta=odd.get("metadata") or {}
            raw_line=meta.get("special_bet_value",odd.get("special_bet_value"))
            try:
                odd_line=float(str(raw_line).replace(",","." ))
            except (TypeError,ValueError):
                continue
            selection=" ".join(str(x or "") for x in (odd.get("name"),meta.get("name"),meta.get("info"),odd.get("label"))).lower()
            if not re.search(r"\b(powyżej|powyzej|over)\b",selection):
                continue
            if abs(odd_line-float(line))>0.001:
                continue
            if odd.get("status") not in (1,"1","ACTIVE","active") or odd.get("display") is False:
                continue
            if str(meta.get("offer_state_id",1))!="1":
                continue
            logging.info("Superbet FT corners: event %s | active Over %.1f market (no odds read)",event_id,line)
            return True
    logging.info("Superbet: brak aktywnego rynku FT rożne Over %.1f dla event %s",line,event_id)
    return None


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
    "kazachstanpremierleague":"KAZ_1_premier-league","kazachstanpremierliga":"KAZ_1_premier-league",
    "australiamensaleague":"AUS_1_a-league","aleaguemen":"AUS_1_a-league",
    "chinasuperleague":"CHN_1_super-league","chinalegue":"CHN_1_super-league",
    "uefachampionsleague":"UEFA_1_champions-league","championsleague":"UEFA_1_champions-league",
    "uefaeuropaleague":"UEFA_1_europa-league","europaleague":"UEFA_1_europa-league",
    "uefaconferenceleague":"UEFA_1_conference-league","conferenceleague":"UEFA_1_conference-league",
    # Additional exact aliases for competitions returned by Superbet.
    "romaniasuperliga":"ROU_1_liga-1","superligaromania":"ROU_1_liga-1","romania1liga":"ROU_1_liga-1",
    "laliga2":"ESP_2_segunda-division","spainlaliga2":"ESP_2_segunda-division",
    "chinasuperleague":"CHN_1_super-league",
    "australianaleague":"AUS_1_a-league","australiaaleague":"AUS_1_a-league",
    "croatianhnl":"CRO_1_hnl","cyprusdivision1":"CYP_1_first-division",
    "irelandfirstdivision":"IRE_2_first-division","irelandnationalleague":"IRE_3_national-league",
    "russianpremierleague":"RUS_1_premier-liga","ukrainianpremierleague":"UKR_1_premier-league",
    "walespremierleague":"WAL_1_cymru-premier","welshpremierleague":"WAL_1_cymru-premier",
    "walescymrupremier":"WAL_1_cymru-premier","cymrupremier":"WAL_1_cymru-premier",
    "walescymrunorth":"WAL_2_cymru-north","walescymrusouth":"WAL_3_cymru-south",
    "argentinaprimeradivision":"ARG_1_liga-profesional","argentinaprofessionalleague":"ARG_1_liga-profesional",
    "brasilseriea":"BRA_1_serie-a","brazilseriea":"BRA_1_serie-a","brasileirao":"BRA_1_serie-a",
    "brazilserieb":"BRA_2_serie-b","mexicoligamx":"MEX_1_liga-mx","mexicoligaexpansion":"MEX_2_liga-expansion",
    "japanjleague":"JPN_1_j1-league","japanj1":"JPN_1_j1-league",
    # Polish display names used by Superbet (country + competition).
    "rumuniasuperliga":"ROU_1_liga-1","rumunialiga1":"ROU_1_liga-1",
    "hiszpianalaliga2":"ESP_2_segunda-division",
    "meksykligamx":"MEX_1_liga-mx","meksykligaexpansion":"MEX_2_liga-expansion",
    "brazylia1seria":"BRA_1_serie-a","brazyliaseriea":"BRA_1_serie-a",
    "brazyliaserieb":"BRA_2_serie-b",
    "szkocjapremiership":"SCO_1_premiership","szkocjachampionship":"SCO_2_championship",
    "szkocjaleagueone":"SCO_3_league-one","szkocjaleaguetwo":"SCO_4_league-two",
    "szwajcariasuperleague":"SUI_1_super-league",
    "belgiaproleague":"BEL_1_pro-league",
    "anglialeagueone":"ENG_3_league-one","angliachampionship":"ENG_2_championship",
    "angliapremierleague":"ENG_1_premier-league",
}
_matchday_cache={}
_matchday_history_cache={}

def matchday_folder(league):
    # Tylko jawne aliasy pełnych nazw — bez ryzykownego dopasowania fragmentów.
    return MATCHDAY_LEAGUES.get(normalized_team(league))

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

def recent_team_ft_corner_history(team,rows,fixture_date):
    """Recent full-time corner totals for a team, excluding the fixture date and later."""
    matched=[]
    for row in rows:
        match_date=str(row.get("date") or "")
        if not match_date or match_date>=fixture_date:
            continue
        try:
            home_c=int(float(row["home_corners"]))
            away_c=int(float(row["away_corners"]))
        except (KeyError,TypeError,ValueError):
            continue
        hsim=team_similarity(team,row.get("home",""))
        assim=team_similarity(team,row.get("away",""))
        if hsim>=0.74 and hsim>assim+0.05:
            matched.append((match_date,home_c,away_c,home_c+away_c))
        elif assim>=0.74 and assim>hsim+0.05:
            matched.append((match_date,away_c,home_c,home_c+away_c))
    matched.sort(key=lambda item:item[0],reverse=True)
    return matched[:FT_HISTORY]


def evaluate_ft_matchday_quality(league,home,away,start_ts):
    rows=load_matchday_results(league)
    if rows is None:
        return False,"brak danych Open Matchday FT dla ligi"
    fixture_date=datetime.fromtimestamp(start_ts,timezone.utc).date().isoformat()
    hh=recent_team_ft_corner_history(home,rows,fixture_date)
    ah=recent_team_ft_corner_history(away,rows,fixture_date)
    if len(hh)<FT_MIN_SAMPLE or len(ah)<FT_MIN_SAMPLE:
        return False,f"za mała próba FT: {home} {len(hh)}/{FT_HISTORY}, {away} {len(ah)}/{FT_HISTORY}"
    home_avg=sum(x[1] for x in hh)/len(hh)
    away_avg=sum(x[1] for x in ah)/len(ah)
    totals=[x[3] for x in hh]+[x[3] for x in ah]
    combined_avg=sum(totals)/len(totals)
    hit_rate=sum(1 for total in totals if total>=9)/len(totals)
    passed=(home_avg>=FT_MIN_TEAM_AVG and away_avg>=FT_MIN_TEAM_AVG
            and combined_avg>=FT_MIN_COMBINED_AVG and hit_rate>=FT_MIN_HIT_RATE)
    detail=(f"próba FT {len(hh)}+{len(ah)}, średnie rożne drużyn {home_avg:.2f}/{away_avg:.2f}, "
            f"średnia łączna {combined_avg:.2f}, Over 8.5 w {hit_rate*100:.0f}%")
    return passed,detail


def league_ft_corner_profile(rows,fixture_date,limit=80):
    """Recent league-wide FT corner baseline, excluding the fixture date and future."""
    matches=[]
    for row in rows or []:
        match_date=str(row.get("date") or "")
        if not match_date or match_date>=fixture_date:
            continue
        try:
            total=int(float(row["home_corners"]))+int(float(row["away_corners"]))
        except (KeyError,TypeError,ValueError):
            continue
        matches.append((match_date,total))
    matches.sort(key=lambda item:item[0],reverse=True)
    sample=matches[:limit]
    if len(sample)<20:
        return None,None,len(sample)
    average=sum(total for _,total in sample)/len(sample)
    hit_rate=sum(total>=9 for _,total in sample)/len(sample)
    return average,hit_rate,len(sample)


def ft_matchday_candidate_profile(league,home,away,start_ts):
    """Strictly qualify fixtures using each team's recent FT corner history."""
    rows=load_matchday_results(league)
    if rows is None:
        return None,"brak danych Open Matchday FT"
    fixture_date=datetime.fromtimestamp(start_ts,timezone.utc).date().isoformat()
    hh=recent_team_ft_corner_history(home,rows,fixture_date)
    ah=recent_team_ft_corner_history(away,rows,fixture_date)
    if len(hh)<FT_MIN_SAMPLE or len(ah)<FT_MIN_SAMPLE:
        return None,f"za mała próba FT ({len(hh)}+{len(ah)})"
    home_avg=sum(x[1] for x in hh)/len(hh)
    away_avg=sum(x[1] for x in ah)/len(ah)
    totals=[x[3] for x in hh]+[x[3] for x in ah]
    combined_avg=sum(totals)/len(totals)
    hit_rate=sum(1 for total in totals if total>=9)/len(totals)
    recent_totals=[x[3] for x in hh[:5]]+[x[3] for x in ah[:5]]
    recent_hit=sum(1 for total in recent_totals if total>=9)/len(recent_totals)
    league_avg,league_hit,league_n=league_ft_corner_profile(rows,fixture_date)
    if min(home_avg,away_avg)<FT_MIN_TEAM_AVG:
        return None,f"za mało rożnych własnych: {home_avg:.2f}/{away_avg:.2f}"
    if combined_avg<FT_MIN_COMBINED_AVG:
        return None,f"średnia łączna tylko {combined_avg:.2f}"
    if hit_rate<FT_MIN_HIT_RATE:
        return None,f"Over 8.5 historycznie tylko {hit_rate*100:.0f}%"
    if recent_hit<0.50:
        return None,f"ostatnie 5: Over 8.5 tylko {recent_hit*100:.0f}%"
    # Dynamiczna premia za ligi, w których ostatnie wyniki pokazują dużo rożnych.
    # Bez sztywnej białej listy — liczą się aktualne dane ligowe i forma obu drużyn.
    league_bonus=0.0 if league_avg is None else (league_avg*5)+(league_hit*40)
    score=(hit_rate*100)+(recent_hit*35)+(combined_avg*4)+(min(home_avg,away_avg)*5)+league_bonus
    league_detail=(f", liga ostatnie {league_n}: śr. {league_avg:.2f}, O8.5 {league_hit*100:.0f}%"
                   if league_avg is not None else ", brak pełnej próbki ligowej")
    detail=(f"próba {len(hh)}+{len(ah)}, rożne własne {home_avg:.2f}/{away_avg:.2f}, "
            f"średnia łączna {combined_avg:.2f}, Over 8.5: {hit_rate*100:.0f}% "
            f"(ostatnie 5: {recent_hit*100:.0f}%){league_detail}")
    return score,detail


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
    """Prefer a provider-supplied direct corners-market URL; otherwise the exact event URL."""
    payload=superbet_api("/v3/pl-PL/events",{"events":str(event_id)})
    events=superbet_events_from_payload(payload)
    event=events[0] if len(events)==1 else next(
        (e for e in events if str(e.get("event_id") or e.get("id"))==str(event_id)),None)
    if not event:
        return None
    url_keys=("url","event_url","eventUrl","permalink","web_url","webUrl","deep_link","deepLink")
    def valid_url(obj):
        if not isinstance(obj,dict):
            return None
        for key in url_keys:
            value=obj.get(key)
            if isinstance(value,str) and value.startswith("https://") and "superbet" in value.lower():
                return value
        return None
    for market in event.get("markets") or []:
        market_name=str(market.get("name") or "").lower()
        if not any(term in market_name for term in ("roż","rzutów rożnych","corner")):
            continue
        direct=valid_url(market)
        if direct:
            return direct
    supplied=valid_url(event) or valid_url(event.get("fixture") or {})
    if supplied:
        return supplied
    # Superbet's public event-page route uses /kursy/pilka-nozna/<home>-vs-<away>-<event_id>.
    def slug(value):
        raw=str(value or "").replace("ł","l").replace("Ł","L")
        ascii_name=unicodedata.normalize("NFKD",raw).encode("ascii","ignore").decode().lower()
        return re.sub(r"-+","-",re.sub(r"[^a-z0-9]+","-",ascii_name)).strip("-")
    home_slug,away_slug=slug(home),slug(away)
    if not home_slug or not away_slug:
        return None
    return f"https://superbet.pl/kursy/pilka-nozna/{home_slug}-vs-{away_slug}-{int(event_id)}"


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


def settle_ft_picks(c):
    """Settle FT corner picks only from confirmed full-time corner totals in Open Matchday."""
    pending=c.execute(
        "SELECT event_id,league,home,away,start_ts,odds FROM ft_picks WHERE status='PENDING'"
    ).fetchall()
    now_ts=int(datetime.now(timezone.utc).timestamp())
    for event_id,league,home,away,start_ts,odds in pending:
        if now_ts<int(start_ts)+3*60*60:
            continue
        rows=load_matchday_results(league)
        if not rows:
            continue
        match_date=datetime.fromtimestamp(start_ts,timezone.utc).date().isoformat()
        found=None
        for row in rows:
            if str(row.get("date") or "")[:10]!=match_date:
                continue
            rh=row.get("home") or ""; ra=row.get("away") or ""
            direct=team_similarity(home,rh)+team_similarity(away,ra)
            reverse=team_similarity(home,ra)+team_similarity(away,rh)
            if max(direct,reverse)<1.65:
                continue
            try:
                hc=int(float(row.get("home_corners")))
                ac=int(float(row.get("away_corners")))
            except (TypeError,ValueError):
                continue
            found=(hc+ac)
            break
        if found is None:
            logging.info("Rozliczenie FT: brak potwierdzonych danych dla %s - %s",home,away)
            continue
        result="WON" if found>=10 else "LOST"
        c.execute("UPDATE ft_picks SET status=?,ft_corners=?,settled_at=? WHERE event_id=?",
                  (result,found,datetime.now(TZ).isoformat(),event_id))
        c.commit()
        price=float(odds or 0)
        profit=STAKE*(price-1.0) if result=="WON" and price>0 else -STAKE
        msg=(f"📊 ROZLICZENIE — ROGI FT\n\n⚽ {home} – {away}\n"
             f"📌 Over 9.5 rzutów rożnych — CAŁY MECZ\n"
             f"🚩 Rożne FT: {found}\n💹 Kurs: {price:.2f}\n"
             f"{'🟢 WYGRANY' if result=='WON' else '🔴 PRZEGRANY'}\n"
             f"💰 Stawka: {STAKE} zł\n🏦 Wynik: {profit:+.2f} zł")
        if not send(msg):
            logging.error("Nie udało się wysłać rozliczenia FT event=%s",event_id)


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



# --- Monitoring anomalii kursów HT/FT przed meczem ---
HTFT_MONITOR_HOURS = 48
HTFT_MONITOR_MAX_EVENTS = 180
HTFT_ALERT_DROP_PCT = 0.15       # spadek kursu o co najmniej 15% od pierwszej obserwacji
HTFT_ALERT_PROBABILITY_PP = 0.05 # wzrost fair probability o 5 p.p. po zdjęciu marży
HTFT_MIN_SNAPSHOTS = 2
HTFT_MAX_DAILY_ALERTS = 2

def daily_htft_alert_allowed(sent_event_ids, event_id, limit=HTFT_MAX_DAILY_ALERTS):
    """One alert per match and no more than two distinct matches per Warsaw day."""
    return int(event_id) not in sent_event_ids and len(sent_event_ids) < limit

def fetch_superbet_event_detail(event_id):
    payload=superbet_api("/v3/pl-PL/events",{"events":str(event_id)})
    events=superbet_events_from_payload(payload)
    if len(events)==1:
        event=events[0]
        returned_id=event.get("event_id") or event.get("id")
        if returned_id is not None and str(returned_id)!=str(event_id):
            return None
        return event
    return next((e for e in events if str(e.get("event_id") or e.get("id"))==str(event_id)),None)

def htft_market_outcomes(event):
    """Read an explicitly labelled HT/FT market and normalize using all its active outcomes."""
    found=[]
    for market in event.get("markets") or []:
        raw_name=str(market.get("name") or "")
        name=re.sub(r"\s+"," ",raw_name.strip().lower())
        explicit_htft=(
            ("ht/ft" in name or "half time/full time" in name or
             "half-time/full-time" in name or "half time / full time" in name or
             "1. połowa/mecz" in name or "1.połowa/mecz" in name or
             "1. połowa / mecz" in name or "1.połowa / mecz" in name or
             ("1. połowa" in name and "mecz" in name) or
             ("1st half" in name and "full time" in name) or
             "do przerwy/koniec" in name or "do przerwy / wynik końcowy" in name or
             "wynik do przerwy/wynik końcowy" in name or
             "wynik do przerwy / wynik końcowy" in name)
        )
        if not explicit_htft or market.get("status") not in (None,1,"1","ACTIVE","active"):
            continue
        all_outcomes=[]
        for odd in market.get("odds") or []:
            meta=odd.get("metadata") or {}
            if odd.get("status") not in (1,"1","ACTIVE","active") or odd.get("display") is False:
                continue
            if str(meta.get("offer_state_id",1))!="1":
                continue
            try:
                price=float(odd.get("price"))
            except (TypeError,ValueError):
                continue
            labels=[odd.get("name"),odd.get("label"),meta.get("name"),meta.get("info")]
            label=" ".join(str(x).strip() for x in labels if x is not None)
            match=re.search(r"(?<![0-9])([12xX])\s*[/\-]\s*([12xX])(?![0-9])",label)
            if not match:
                continue
            selection=f"{match.group(1).upper()}/{match.group(2).upper()}"
            if price<=1.0 or price>1000:
                continue
            all_outcomes.append({"selection":selection,"odds":price,"label":label})
        if not all_outcomes:
            continue
        # Remove bookmaker margin across every available HT/FT outcome, not just 1/2 and 2/1.
        total=sum(1.0*x["odds"]**-1 for x in all_outcomes)
        complete_market=len({x["selection"] for x in all_outcomes})==9
        for item in all_outcomes:
            # Only calculate no-vig probability when all nine HT/FT outcomes are present.
            item["fair_probability"]=((1.0/item["odds"])/total
                                      if complete_market and total>0 else None)
            item["market_name"]=raw_name
            if item["selection"] in ("1/2","2/1"):
                found.append(item)
    # Superbet may expose HT/FT as a SuperBets combo either at event level
    # or nested in the markets collection. Inspect both containers.
    combo_sources=[]
    for source in (event.get("superbets") or [], event.get("markets") or []):
        if isinstance(source,dict):
            source=list(source.values())
        if isinstance(source,list):
            combo_sources.extend(x for x in source if isinstance(x,dict))
    seen_combo_ids=set()
    for combo_market in combo_sources:
        odds_rows=combo_market.get("odds") or combo_market.get("selections") or []
        if isinstance(odds_rows,dict):
            odds_rows=list(odds_rows.values())
        for odd in odds_rows:
            if not isinstance(odd,dict):
                continue
            meta=odd.get("metadata") or {}
            components=(odd.get("odd_components") or odd.get("components") or
                        meta.get("odd_components") or odd.get("oddComponents") or [])
            if isinstance(components,dict):
                components=list(components.values())
            if len(components)!=2:
                continue
            if odd.get("status") not in (None,1,"1","ACTIVE","active",True) or odd.get("display") is False:
                continue
            if str(meta.get("offer_state_id",1)) not in ("1","None"):
                continue
            def component_market(component):
                return " ".join(str(component.get(k) or "") for k in
                    ("market_line_name","market_name","marketName","market","name","marketLineName")).strip().lower()
            def component_pick(component):
                value=(component.get("odd_name") or component.get("selection") or
                       component.get("outcome") or component.get("label") or
                       component.get("oddName") or (component.get("metadata") or {}).get("name"))
                return str(value or "").strip().upper()
            first_half=next((x for x in components if isinstance(x,dict) and
                any(term in component_market(x) for term in
                    ("1st half","first half","1. połowa","pierwsza połowa","half time","half-time"))),None)
            full_time=next((x for x in components if isinstance(x,dict) and
                any(term in component_market(x) for term in
                    ("full time","full-time","wynik końcowy","wynik koncowy"))),None)
            if not first_half or not full_time:
                continue
            ht=component_pick(first_half)
            ft=component_pick(full_time)
            if ht not in ("1","2") or ft not in ("1","2") or ht==ft:
                continue
            try:
                price=float(odd.get("price") or meta.get("price"))
            except (TypeError,ValueError):
                continue
            if price<=1.0 or price>1000:
                continue
            unique=(ht,ft,round(price,4))
            if unique in seen_combo_ids:
                continue
            seen_combo_ids.add(unique)
            found.append({
                "selection":f"{ht}/{ft}","odds":price,
                "fair_probability":None,
                "label":str(meta.get("name") or odd.get("name") or ""),
                "market_name":"SuperBets — kombinacja HT/FT"
            })
    return found

def monitor_ft_corners(c):
    now=datetime.now(TZ)
    start=datetime.now(timezone.utc)
    end=start+timedelta(hours=HTFT_MONITOR_HOURS)
    events=superbet_football_events(start,end)
    events.sort(key=lambda e: (e.get("fixture") or {}).get("utc_date",""))
    events=events[:HTFT_MONITOR_MAX_EVENTS]
    c.execute("""CREATE TABLE IF NOT EXISTS htft_odds_snapshots(
        id INTEGER PRIMARY KEY AUTOINCREMENT,event_id INTEGER NOT NULL,
        selection TEXT NOT NULL,home TEXT,away TEXT,league TEXT,start_ts INTEGER,
        odds REAL NOT NULL,fair_probability REAL,observed_at TEXT NOT NULL)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_htft_snapshots
        ON htft_odds_snapshots(event_id,selection,observed_at)""")
    c.execute("""CREATE TABLE IF NOT EXISTS htft_odds_alerts(
        event_id INTEGER NOT NULL,selection TEXT NOT NULL,last_alert_odds REAL,
        last_alert_probability REAL,last_alert_at TEXT,PRIMARY KEY(event_id,selection))""")
    c.commit()
    # Restore the daily cap from the persisted SQLite database (Warsaw local date).
    day_key=now.date().isoformat()
    sent_event_ids={int(row[0]) for row in c.execute(
        "SELECT DISTINCT event_id FROM htft_odds_alerts WHERE substr(last_alert_at,1,10)=?",
        (day_key,)).fetchall()}
    logging.info("HT/FT: wysłane dziś mecze %s/%s",len(sent_event_ids),HTFT_MAX_DAILY_ALERTS)
    candidates=[]
    for listed in events:
        fx=listed.get("fixture") or {}
        eid=listed.get("event_id")
        if not eid:
            continue
        try:
            kickoff=int(datetime.fromisoformat(str(fx["utc_date"]).replace("Z","+00:00")).timestamp())
        except Exception:
            continue
        parts=str(fx.get("event_name") or "").split("·",1)
        if len(parts)!=2:
            continue
        home,away=(x.strip() for x in parts)
        candidates.append((int(eid),home,away,kickoff,extract_league_name(listed) or "Liga niepodana"))
    logging.info("HT/FT monitoring: sprawdzam %s spotkań z najbliższych %s godzin",len(candidates),HTFT_MONITOR_HOURS)
    details={}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures={pool.submit(fetch_superbet_event_detail,x[0]):x for x in candidates}
        for future,meta in futures.items():
            try:
                details[meta[0]]=future.result()
            except Exception:
                logging.exception("HT/FT: błąd pobierania szczegółów eventu %s",meta[0])
    checked=0
    diagnostics=0
    alerts_sent=0
    for eid,home,away,kickoff,league in candidates:
        event=details.get(eid)
        if not event:
            continue
        fixture=event.get("fixture") or {}
        offer_states=fixture.get("offer_state_status") or {}
        if offer_states and offer_states.get("1") not in (1,"1"):
            continue
        phase=(event.get("inplay_stats_metadata") or {}).get("status")
        if phase not in (None,"","NOT_STARTED"):
            continue
        # Prefer the detailed event, but fall back to the listing record if its
        # detail response is partial or lacks market collections.
        if not (event.get("markets") or event.get("superbets")):
            listed_event=next((x for x in events if str(x.get("event_id"))==str(eid)),None)
            if listed_event and (listed_event.get("markets") or listed_event.get("superbets")):
                event=listed_event
        outcomes=htft_market_outcomes(event)
        if not outcomes:
            if diagnostics<3:
                diagnostics+=1
                logging.info("HT/FT: brak rozpoznanego rynku event=%s; pola=%s; rynki=%s; superbets=%s",
                    eid,list(event.keys()),
                    [str(x.get("name") or "") for x in (event.get("markets") or [])[:12]],
                    len(event.get("superbets") or []))
            continue
        checked+=1
        for outcome in outcomes:
            selection=outcome["selection"]
            odds=outcome["odds"]
            fair=outcome["fair_probability"]
            previous=c.execute("""SELECT odds,fair_probability FROM htft_odds_snapshots
                WHERE event_id=? AND selection=? ORDER BY id DESC LIMIT 1""",(eid,selection)).fetchone()
            first=c.execute("""SELECT odds,fair_probability FROM htft_odds_snapshots
                WHERE event_id=? AND selection=? ORDER BY id ASC LIMIT 1""",(eid,selection)).fetchone()
            c.execute("""INSERT INTO htft_odds_snapshots
                (event_id,selection,home,away,league,start_ts,odds,fair_probability,observed_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (eid,selection,home,away,league,kickoff,odds,fair,now.isoformat()))
            if not previous or not first:
                continue
            count=c.execute("SELECT COUNT(*) FROM htft_odds_snapshots WHERE event_id=? AND selection=?",
                            (eid,selection)).fetchone()[0]
            if count<HTFT_MIN_SNAPSHOTS:
                continue
            base_odds=float(first[0])
            base_prob=first[1]
            drop=(base_odds-odds)/base_odds if base_odds>0 else 0
            prob_move=(fair-float(base_prob)) if fair is not None and base_prob is not None else 0
            # Trigger only on meaningful cumulative repricing, not tiny tick noise.
            anomaly=(drop>=HTFT_ALERT_DROP_PCT or prob_move>=HTFT_ALERT_PROBABILITY_PP)
            if not anomaly:
                continue
            prior_alert=c.execute("""SELECT last_alert_odds,last_alert_probability,last_alert_at
                FROM htft_odds_alerts WHERE event_id=? AND selection=?""",(eid,selection)).fetchone()
            if prior_alert:
                last_alert_odds,last_alert_prob,last_alert_at=prior_alert
                try:
                    age=(now-datetime.fromisoformat(last_alert_at)).total_seconds()/3600
                except Exception:
                    age=999
                further_drop=(last_alert_odds-odds)/last_alert_odds if last_alert_odds else 0
                further_prob=(fair-last_alert_prob) if fair is not None and last_alert_prob is not None else 0
                if age<2 or (further_drop<0.08 and further_prob<0.03):
                    continue
            kickoff_local=datetime.fromtimestamp(kickoff,timezone.utc).astimezone(TZ)
            link=superbet_match_url(eid,home,away,kickoff)
            reasons=[]
            if drop>=HTFT_ALERT_DROP_PCT:
                reasons.append(f"kurs spadł o {drop*100:.1f}% (z {base_odds:.2f} do {odds:.2f})")
            if prob_move>=HTFT_ALERT_PROBABILITY_PP:
                reasons.append(f"prawdopodobieństwo bez marży wzrosło o {prob_move*100:.1f} p.p.")
            probability_text=(f"fair probability: {fair*100:.1f}%" if fair is not None
                               else "prawdopodobieństwo bez marży: brak pełnego rynku")
            msg=("🚨 ANOMALIA KURSU HT/FT — PRZED MECZEM\n\n"
                 f"🏆 {league}\n⚽ {home} – {away}\n"
                 f"🕒 {kickoff_local:%d.%m.%Y %H:%M}\n"
                 f"🎯 Rynek: {outcome['market_name']}\n"
                 f"📌 Wynik HT/FT: {selection}\n"
                 f"💹 Kurs bazowy: {base_odds:.2f} → obecny: {odds:.2f}\n"
                 f"📈 Sygnał: {'; '.join(reasons)}\n"
                 f"📊 Obserwacje: {count}; {probability_text}\n"
                 "⚠️ To sygnał ruchu rynku, nie dowód ustawienia meczu.\n"
                 f"🔗 {link}")
            if not daily_htft_alert_allowed(sent_event_ids,eid):
                logging.info("HT/FT: limit dzienny osiągnięty lub mecz już zgłoszony (%s)",eid)
                continue
            if send(msg):
                sent_event_ids.add(eid)
                c.execute("""INSERT INTO htft_odds_alerts
                    (event_id,selection,last_alert_odds,last_alert_probability,last_alert_at)
                    VALUES(?,?,?,?,?)
                    ON CONFLICT(event_id,selection) DO UPDATE SET
                    last_alert_odds=excluded.last_alert_odds,
                    last_alert_probability=excluded.last_alert_probability,
                    last_alert_at=excluded.last_alert_at""",
                    (eid,selection,odds,fair,now.isoformat()))
                alerts_sent+=1
                logging.warning("HT/FT ANOMALIA %s %s-%s %s: %.2f -> %.2f",
                                selection,home,away,eid,base_odds,odds)
            c.commit()
    c.commit()
    logging.info("HT/FT monitoring zakończony: rynki=%s, alerty wysłane=%s",checked,alerts_sent)



FT_DAILY_TARGET=10
FT_DAILY_MAX=10

def excluded_corner_league(league):
    name=normalized_team(league)
    return any(token in name for token in ("ukraine","ukrain","ukraina","ukrainska","ukr"))


def monitor_ft_corners(c):
    """Wybiera do 10 najmocniejszych przedmeczowych spotkań FT Over 8.5 bez filtra kursowego."""
    now=datetime.now(TZ)
    start=now.replace(hour=0,minute=0,second=0,microsecond=0)
    end=now.replace(hour=23,minute=59,second=59,microsecond=0)
    events=superbet_football_events(start,end)
    c.execute("""CREATE TABLE IF NOT EXISTS ft_corner_picks(
        event_id INTEGER PRIMARY KEY,league TEXT,home TEXT,away TEXT,start_ts INTEGER,
        odds REAL NOT NULL,profile TEXT,sent_at TEXT NOT NULL)""")
    sent_ids={int(x[0]) for x in c.execute(
        "SELECT event_id FROM ft_corner_picks WHERE substr(sent_at,1,10)=?",
        (now.date().isoformat(),)).fetchall()}
    candidates=[]
    for listed in events:
        fx=listed.get("fixture") or {}
        eid=listed.get("event_id")
        if not eid or int(eid) in sent_ids:
            continue
        parts=str(fx.get("event_name") or "").split("·",1)
        if len(parts)!=2:
            continue
        try:
            kickoff=int(datetime.fromisoformat(str(fx["utc_date"]).replace("Z","+00:00")).timestamp())
        except Exception:
            continue
        if kickoff<=int(now.timestamp()):
            continue
        home,away=(x.strip() for x in parts)
        league=extract_league_name(listed) or "Liga niepodana"
        if excluded_corner_league(league):
            logging.info("FT Over 8.5: pominięto wykluczoną ligę %s",league)
            continue
        score,detail=ft_matchday_candidate_profile(league,home,away,kickoff)
        if score is None:
            continue
        candidates.append({"event_id":int(eid),"home":home,"away":away,"league":league,
                           "start_ts":kickoff,"profile_score":score,"profile":detail})
    logging.info("FT Over 8.5: wydarzenia=%s, z historią=%s, już wysłane dziś=%s",
                 len(events),len(candidates),len(sent_ids))
    def inspect(item):
        market_available=superbet_ft_corners_over_odds(item["event_id"],FT_CORNERS_LINE)
        if not market_available:
            return None
        item["odds"]=0.0  # pole techniczne bazy; kurs nie jest pobierany ani używany
        return item
    qualified=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures=[pool.submit(inspect,item) for item in candidates]
        for future in as_completed(futures):
            try:
                item=future.result()
                if item:
                    qualified.append(item)
            except Exception:
                logging.exception("FT Over 8.5: błąd odczytu rynku")
    qualified.sort(key=lambda x:(-x["profile_score"],x["start_ts"]))
    remaining=max(0,FT_DAILY_MAX-len(sent_ids))
    selected=qualified[:remaining]
    logging.info("FT Over 8.5: dokładny rynek 8.5 dostępny w %s meczach; wybrano %s; cel dzienny=%s",
                 len(qualified),len(selected),FT_DAILY_TARGET)
    for item in selected:
        kickoff=datetime.fromtimestamp(item["start_ts"],timezone.utc).astimezone(TZ)
        link=superbet_match_url(item["event_id"],item["home"],item["away"],item["start_ts"])
        msg=("⚽ TYP FT — OVER 8.5 RZUTÓW ROŻNYCH\n\n"
             f"🏆 {item['league']}\n⚔️ {item['home']} – {item['away']}\n"
             f"📅 {kickoff:%d.%m.%Y} | 🕒 {kickoff:%H:%M}\n"
             "🎯 Rynek: powyżej 8.5 rzutów rożnych — cały mecz (FT)\n"
             f"📊 Historia: {item['profile']}\n"
             f"💰 Stawka informacyjna: {STAKE} zł\n"
             f"🔗 Superbet — otwórz mecz, następnie Rzuty rożne → Liczba rzutów rożnych: {link}")
        if send(msg):
            c.execute("""INSERT OR IGNORE INTO ft_corner_picks
                (event_id,league,home,away,start_ts,odds,profile,sent_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (item["event_id"],item["league"],item["home"],item["away"],
                 item["start_ts"],item["odds"],item["profile"],now.isoformat()))
            c.commit()
            sent_ids.add(item["event_id"])
            logging.info("FT Over 8.5: wysłano %s - %s (kurs nie wpływa na wybór)",item["home"],item["away"])
        else:
            logging.error("FT Over 8.5: nie wysłano eventu %s",item["event_id"])
        time.sleep(0.3)
    if len(sent_ids)<FT_DAILY_TARGET:
        logging.warning("FT Over 8.5: cel 10 typów nieosiągnięty — potwierdzonych i wysłanych %s; nie dobieram spotkań bez rynku lub danych",
                        len(sent_ids))


def main():
    if not TOKEN or not CHAT:
        logging.error("Brak TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        return
    c=db_open()
    try:
        monitor_ft_corners(c)
    finally:
        c.close()


if __name__=="__main__": main()
