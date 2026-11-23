"""Cornerflick open match-data adapter for Rogi FT.

Competition season JSON is used as an index; corner counts are read only from
finished-match data.json records. Cache is persisted in rogift_duble.db so the
five-minute scan does not repeatedly download the same match records.
Data attribution: Cornerflick — https://cornerflick.com (CC BY 4.0).
"""
import json
import logging
import os
import re
import sqlite3
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://cornerflick.com"
LEAGUES_URL = BASE + "/football/leagues"
CACHE_DB = os.getenv("ROGI_FT_DB", "rogift_duble.db")
INDEX_TTL = 6 * 60 * 60
SEASON_TTL = 3 * 60 * 60
MATCH_TTL = 14 * 24 * 60 * 60
TEAM_MATCH_LIMIT = 15
MAX_WORKERS = 8

COUNTRY_WORDS = {
    "england", "english", "anglia", "spain", "spanish", "hiszpania",
    "italy", "italian", "wlochy", "france", "french", "francja",
    "germany", "german", "niemcy", "netherlands", "dutch", "holandia",
    "portugal", "portuguese", "portugalia", "turkey", "turkish", "turcja",
    "poland", "polish", "polska", "scotland", "scottish", "szkocja",
    "belgium", "belgian", "belgia", "greece", "greek", "grecja",
    "switzerland", "swiss", "szwajcaria", "austria", "austrian",
    "denmark", "danish", "dania", "sweden", "swedish", "szwecja",
    "norway", "norwegian", "norwegia", "finland", "finnish", "finlandia",
    "ireland", "irish", "irlandia", "wales", "walia", "brazil", "brazilian",
    "brazylia", "argentina", "argentinian", "argentyna", "mexico", "mexican",
    "meksyk", "usa", "canada", "japan", "japanese", "japonia", "australia",
    "saudi", "arabia", "egypt", "egyptian", "egipt", "europe", "uefa",
    "international", "africa", "asia", "oceania", "america", "south",
    "north", "america", "wales", "walia", "south", "africa", "rpa",
    "uruguay", "colombia", "chile", "peru", "ecuador", "paraguay",
    "bolivia", "venezuela", "new", "zealand", "india", "indonesia",
    "thailand", "vietnam", "malaysia", "singapore",
}
COUNTRY_CANON = {
    "anglia": "england", "english": "england", "england": "england",
    "hiszpania": "spain", "spanish": "spain", "spain": "spain",
    "wlochy": "italy", "italian": "italy", "italy": "italy",
    "francja": "france", "french": "france", "france": "france",
    "niemcy": "germany", "german": "germany", "germany": "germany",
    "holandia": "netherlands", "dutch": "netherlands", "netherlands": "netherlands",
    "portugalia": "portugal", "portuguese": "portugal", "portugal": "portugal",
    "turcja": "turkey", "turkish": "turkey", "turkey": "turkey",
    "polska": "poland", "polish": "poland", "poland": "poland",
    "szkocja": "scotland", "scottish": "scotland", "scotland": "scotland",
    "belgia": "belgium", "belgian": "belgium", "belgium": "belgium",
    "grecja": "greece", "greek": "greece", "greece": "greece",
    "szwajcaria": "switzerland", "swiss": "switzerland", "switzerland": "switzerland",
    "dania": "denmark", "danish": "denmark", "denmark": "denmark",
    "szwecja": "sweden", "swedish": "sweden", "sweden": "sweden",
    "norwegia": "norway", "norwegian": "norway", "norway": "norway",
    "finlandia": "finland", "finnish": "finland", "finland": "finland",
    "irlandia": "ireland", "irish": "ireland", "ireland": "ireland",
    "walia": "wales", "wales": "wales",
    "brazylia": "brazil", "brazilian": "brazil", "brazil": "brazil",
    "argentyna": "argentina", "argentinian": "argentina", "argentina": "argentina",
    "meksyk": "mexico", "mexican": "mexico", "mexico": "mexico",
    "japonia": "japan", "japanese": "japan", "japan": "japan",
    "usa": "usa", "canada": "canada", "rpa": "southafrica",
    "southafrica": "southafrica", "uruguay": "uruguay", "colombia": "colombia",
    "chile": "chile", "peru": "peru", "ecuador": "ecuador",
    "paraguay": "paraguay", "bolivia": "bolivia", "venezuela": "venezuela",
    "saudi": "saudiarabia", "arabia": "saudiarabia",
}
LEAGUE_ALIASES = {
    "epl": "premier-league",
    "premierleague": "premier-league",
    "laliga": "la-liga",
    "seriea": "serie-a",
    "bundesliga": "bundesliga",
    "ligue1": "ligue-1",
    "eredivisie": "eredivisie",
    "superlig": "super-lig",
    "saudiproleague": "saudi-pro-league",
    "j1league": "j1-league",
    "japanj1league": "j1-league",
    "championsleague": "champions-league",
    "uefachampionsleague": "champions-league",
    "ligamistrzow": "champions-league",
    "europaleague": "europa-league",
    "uefaeuropaleague": "europa-league",
    "ligaeuropy": "europa-league",
    "conferenceleague": "conference-league",
    "uefaconferenceleague": "conference-league",
    "ligakonferencji": "conference-league",
    "primeiraliga": "primeira-liga",
    "portugueseprimera": "primeira-liga",
    "brasileiraoseriea": "brasileirao",
    "brasileirao": "brasileirao",
    "brasilseriea": "brasileirao",
    "mls": "mls",
    "usamls": "mls",
    "majorleaguesoccer": "mls",
    "mlsplayoff": "mls",
    "ligaprofesional": "liga-profesional",
    "ekstraklasa": "ekstraklasa",
}

_league_index_memory = None
_season_memory = {}
_match_memory = {}


def _norm(value):
    raw = str(value or "").replace("ł", "l").replace("Ł", "L")
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode().lower()
        if ch.isalnum()
    )


def _raw_tokens(value):
    raw = str(value or "").replace("ł", "l").replace("Ł", "L")
    return re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode().lower())


def _core_tokens(value):
    tokens = _raw_tokens(value)
    return {token for token in tokens if token not in COUNTRY_WORDS and token not in {"football", "soccer"}}


def _country_tag(value):
    return next((COUNTRY_CANON[token] for token in _raw_tokens(value) if token in COUNTRY_CANON), None)


def _competition_score(requested, candidate):
    requested_country = _country_tag(requested)
    candidate_country = _country_tag(candidate)
    # Never map a same-named competition from a different country.
    if requested_country and candidate_country and requested_country != candidate_country:
        return 0.0
    a, b = _core_tokens(requested), _core_tokens(candidate)
    if not a or not b:
        return 0.0
    if a == b or a.issubset(b) or b.issubset(a):
        return 1.0
    jaccard = len(a & b) / len(a | b)
    compact = SequenceMatcher(None, "".join(sorted(a)), "".join(sorted(b))).ratio()
    return max(jaccard, compact * 0.85)


def _db_connect():
    conn = sqlite3.connect(CACHE_DB, timeout=30)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cornerflick_cache "
        "(cache_key TEXT PRIMARY KEY, fetched_at INTEGER NOT NULL, payload TEXT NOT NULL)"
    )
    return conn


def _cache_get(key, ttl):
    conn = None
    try:
        conn = _db_connect()
        row = conn.execute(
            "SELECT fetched_at,payload FROM cornerflick_cache WHERE cache_key=?", (key,)
        ).fetchone()
        if row and int(time.time()) - int(row[0]) < ttl:
            return json.loads(row[1])
    except Exception as exc:
        logging.debug("Cornerflick cache read %s: %s", key, exc)
    finally:
        if conn is not None:
            conn.close()
    return None


def _cache_set(key, payload):
    conn = None
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        conn = _db_connect()
        conn.execute(
            "INSERT INTO cornerflick_cache(cache_key,fetched_at,payload) VALUES(?,?,?) "
            "ON CONFLICT(cache_key) DO UPDATE SET fetched_at=excluded.fetched_at,payload=excluded.payload",
            (key, int(time.time()), encoded),
        )
        conn.commit()
    except Exception as exc:
        logging.warning("Cornerflick cache write %s: %s", key, exc)
    finally:
        if conn is not None:
            conn.close()


def _get_json(url, timeout=20):
    response = requests.get(
        url,
        headers={
            "User-Agent": "OkoTypera-RogiFT/1.0 (+https://cornerflick.com)",
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def _load_league_index():
    global _league_index_memory
    if _league_index_memory is not None:
        return _league_index_memory
    cached = _cache_get("cornerflick:league-index", INDEX_TTL)
    if cached:
        _league_index_memory = cached
        return cached
    try:
        response = requests.get(
            LEAGUES_URL,
            headers={"User-Agent": "OkoTypera-RogiFT/1.0", "Accept": "text/html"},
            timeout=20,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        entries, seen = [], set()
        for anchor in soup.select('a[href^="/football/leagues/"]'):
            href = str(anchor.get("href") or "").split("?", 1)[0].rstrip("/")
            match = re.fullmatch(r"/football/leagues/([a-z0-9-]+)", href)
            label = anchor.get_text(" ", strip=True)
            if not match or not label:
                continue
            slug = match.group(1)
            if slug in seen or slug in {"results", "table"}:
                continue
            seen.add(slug)
            entries.append({"slug": slug, "name": label})
        if not entries:
            raise ValueError("nie znaleziono lig w indeksie Cornerflick")
        _league_index_memory = entries
        _cache_set("cornerflick:league-index", entries)
        logging.info("Cornerflick: indeks lig — %s rozgrywek", len(entries))
        return entries
    except Exception as exc:
        logging.warning("Cornerflick: nie udało się pobrać indeksu lig: %s", exc)
        _league_index_memory = []
        return []


def resolve_league_slug(league):
    key = _norm(league)
    if not key:
        return None
    if key in LEAGUE_ALIASES:
        return LEAGUE_ALIASES[key]
    # Common Polish country prefixes can be removed without guessing the league.
    for prefix in (
        "anglia", "hiszpania", "wlochy", "francja", "niemcy", "holandia",
        "portugalia", "turcja", "polska", "szkocja", "belgia", "grecja",
        "brazylia", "argentyna", "meksyk", "japonia", "uefa",
    ):
        if key.startswith(prefix) and len(key) > len(prefix):
            stripped = key[len(prefix):]
            if stripped in LEAGUE_ALIASES:
                return LEAGUE_ALIASES[stripped]
    entries = _load_league_index()
    ranked = sorted(
        ((_competition_score(league, item["name"]), item["slug"]) for item in entries),
        reverse=True,
    )
    if not ranked or ranked[0][0] < 0.85:
        logging.info("Cornerflick: brak pewnego mapowania ligi '%s'", league)
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.06:
        logging.warning("Cornerflick: niejednoznaczna liga '%s': %s / %s",
                        league, ranked[0], ranked[1])
        return None
    return ranked[0][1]


def _season_data(slug):
    if slug in _season_memory:
        return _season_memory[slug]
    cache_key = "cornerflick:season:" + slug
    payload = _cache_get(cache_key, SEASON_TTL)
    if payload is None:
        url = f"{BASE}/football/leagues/{slug}/results/season.json"
        try:
            payload = _get_json(url, timeout=25)
            _cache_set(cache_key, payload)
        except Exception as exc:
            logging.warning("Cornerflick: sezon %s niedostępny: %s", slug, exc)
            return None
    _season_memory[slug] = payload
    return payload


def _team_similarity(first, second):
    aliases = {
        "manutd": "manchesterunited", "manchesterunited": "manchesterunited",
        "mancity": "manchestercity", "manchestercity": "manchestercity",
        "tottenham": "tottenham", "tottenhamhotspur": "tottenham",
        "spurs": "tottenham", "intermilan": "inter", "internazionale": "inter",
        "inter": "inter", "psg": "parissaintgermain",
        "parissaintgermain": "parissaintgermain", "atleticodemadrid": "atleticomadrid",
        "atleticomadrid": "atleticomadrid", "bayernmunich": "bayernmunchen",
        "bayernmunchen": "bayernmunchen", "nottmforest": "nottinghamforest",
        "nottinghamforest": "nottinghamforest",
    }
    a, b = _norm(first), _norm(second)
    a, b = aliases.get(a, a), aliases.get(b, b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if min(len(a), len(b)) >= 5 and (a in b or b in a):
        return 0.92
    return SequenceMatcher(None, a, b).ratio()


def _team_matches(team, matches, fixture_date):
    selected = []
    for item in matches:
        if str(item.get("status") or "").lower() not in {"finished", "ft", "complete", "completed"}:
            continue
        kickoff = str(item.get("kickoff_at") or "")
        match_date = kickoff[:10]
        if not match_date or match_date >= fixture_date:
            continue
        home = item.get("home_team") or ""
        away = item.get("away_team") or ""
        hscore = _team_similarity(team, home)
        ascore = _team_similarity(team, away)
        if max(hscore, ascore) >= 0.74 and abs(hscore - ascore) >= 0.04:
            selected.append(item)
    selected.sort(key=lambda item: str(item.get("kickoff_at") or ""), reverse=True)
    return selected[:TEAM_MATCH_LIMIT]


def _extract_corner_row(payload):
    if not isinstance(payload, dict):
        return None
    match = payload.get("match") or {}
    if str(match.get("status") or "").lower() not in {"finished", "ft", "complete", "completed"}:
        return None
    teams = payload.get("teams") or {}
    home = (teams.get("home") or {}).get("name")
    away = (teams.get("away") or {}).get("name")
    kickoff = str(match.get("kickoff_at") or "")
    if not home or not away or not kickoff:
        return None
    corners = None
    for stat in payload.get("statistics") or []:
        label = _norm(stat.get("label") or stat.get("name"))
        if label in {"corners", "cornerkicks", "corner"}:
            try:
                hc = float(stat.get("home"))
                ac = float(stat.get("away"))
                if hc < 0 or ac < 0:
                    return None
                corners = (int(hc), int(ac))
            except (TypeError, ValueError):
                return None
            break
    if corners is None:
        return None
    return {
        "date": kickoff[:10],
        "home": str(home),
        "away": str(away),
        "home_corners": corners[0],
        "away_corners": corners[1],
        "cornerflick_url": (payload.get("meta") or {}).get("url"),
    }


def _match_payload(item):
    match_url = str(item.get("dataset_url") or "")
    if not match_url:
        page_url = str(item.get("url") or "")
        if page_url.startswith(BASE + "/"):
            match_url = page_url.rstrip("/") + "/data.json"
    if not match_url.startswith(BASE + "/"):
        return None
    cache_key = "cornerflick:match:" + str(item.get("id") or match_url)
    payload = _cache_get(cache_key, MATCH_TTL)
    if payload is not None:
        return payload
    try:
        payload = _get_json(match_url, timeout=18)
        _cache_set(cache_key, payload)
        return payload
    except Exception as exc:
        logging.debug("Cornerflick: mecz %s niedostępny: %s", match_url, exc)
        return None


def load_team_history(league, home, away, fixture_date):
    """Return real FT-corner rows for the two candidate teams from Cornerflick."""
    slug = resolve_league_slug(league)
    if not slug:
        return []
    season = _season_data(slug)
    if not isinstance(season, dict):
        return []
    matches = season.get("matches") or []
    selected = {}
    for team in (home, away):
        for item in _team_matches(team, matches, fixture_date):
            identity = item.get("id") or item.get("dataset_url") or item.get("url")
            if identity:
                selected[str(identity)] = item
    if not selected:
        logging.info("Cornerflick: brak zakończonych meczów dla %s / %s (%s)",
                     home, away, slug)
        return []
    payloads = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(_match_payload, item) for item in selected.values()]
        for future in as_completed(futures):
            payload = future.result()
            if payload:
                row = _extract_corner_row(payload)
                if row:
                    payloads.append(row)
    payloads.sort(key=lambda row: row["date"], reverse=True)
    logging.info("Cornerflick: %s — pobrano %s meczów z rzeczywistymi rożnymi dla %s / %s",
                 slug, len(payloads), home, away)
    return payloads
