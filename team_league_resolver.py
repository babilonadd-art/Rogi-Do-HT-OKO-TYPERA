"""Free team-to-league lookup used when Superbet omits competition metadata.

TheSportsDB public test key is used without a paid subscription. A league is
returned only when both clubs independently resolve to the same competition
and country, avoiding guesses based on a single ambiguous club name.
"""
import logging
import re
import threading
import time

import requests

API = "https://www.thesportsdb.com/api/v1/json/3/searchteams.php"
TTL = 24 * 60 * 60
_cache = {}
_lock = threading.Lock()


def _norm(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _team_record(name):
    key = _norm(name)
    if not key:
        return None
    with _lock:
        cached = _cache.get(key)
        if cached and time.time() - cached[0] < TTL:
            return cached[1]
    try:
        response = requests.get(
            API, params={"t": name},
            headers={"User-Agent": "OkoTypera-RogiFT/1.0", "Accept": "application/json"},
            timeout=12,
        )
        response.raise_for_status()
        records = response.json().get("teams") or []
        ranked = []
        for item in records:
            candidates = [item.get("strTeam"), item.get("strAlternate")]
            scores = []
            for candidate in candidates:
                candidate_key = _norm(candidate)
                if candidate_key == key:
                    scores.append(1.0)
                elif candidate_key and key and (key in candidate_key or candidate_key in key):
                    scores.append(0.90)
            score = max(scores or [0.0])
            league = str(item.get("strLeague") or "").strip()
            country = str(item.get("strCountry") or "").strip()
            if score >= 0.90 and league and country:
                ranked.append((score, league, country))
        ranked.sort(reverse=True)
        result = None
        if ranked and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
            result = {"league": ranked[0][1], "country": ranked[0][2]}
        with _lock:
            _cache[key] = (time.time(), result)
        return result
    except Exception as exc:
        logging.debug("TheSportsDB: brak rozpoznania drużyny %s: %s", name, exc)
        with _lock:
            _cache[key] = (time.time(), None)
        return None


def resolve_match_league(home, away):
    """Return 'Country - League' only when both teams agree on both fields."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_team_record, home)
        second = pool.submit(_team_record, away)
        h, a = first.result(), second.result()
    if not h or not a:
        return None
    if _norm(h["league"]) != _norm(a["league"]) or _norm(h["country"]) != _norm(a["country"]):
        logging.info(
            "TheSportsDB: drużyny nie potwierdzają tej samej ligi: %s=%s/%s, %s=%s/%s",
            home, h["country"], h["league"], away, a["country"], a["league"]
        )
        return None
    return f'{h["country"]} - {h["league"]}'
