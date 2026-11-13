"""Download the raw data for the 2026 regular season.

    python pipeline/fetch.py schedule     # MLB Stats API: every 2026 regular-season game
    python pipeline/fetch.py players      # MLB Stats API: every player on a 2026 roster (height, position)
    python pipeline/fetch.py statcast     # Baseball Savant: every pitch, one CSV per day
    python pipeline/fetch.py challenges   # MLB Stats API: per-pitch review details (ABS challenges)

Everything lands in data/raw/ (git-ignored). Each step skips files it already has, so it can be re-run safely.
Requests are spaced out to be polite to both services.
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
SEASON = 2026
UA = {"User-Agent": "tap-or-hold research script (personal, non-commercial)"}


def get(url, tries=5, timeout=120):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={**UA, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            wait = 5 * (i + 1)
            print(f"  retry {i + 1}/{tries} in {wait}s: {e}", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"failed: {url}")


def schedule():
    out = RAW / "schedule_2026.json"
    url = ("https://statsapi.mlb.com/api/v1/schedule?sportId=1&gameType=R&season=%d"
           "&hydrate=venue&fields=dates,date,games,gamePk,gameDate,officialDate,status,detailedState,codedGameState,"
           "teams,away,home,team,id,name,abbreviation,score,venue,doubleHeader,gameNumber,seriesGameNumber,"
           "scheduledInnings,isTie" % SEASON)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(get(url))
    d = json.loads(out.read_text())
    games = [g for day in d["dates"] for g in day["games"]]
    final = [g for g in games if g["status"].get("codedGameState") == "F"]
    print(f"schedule: {len(games)} games listed, {len(final)} final")


def players():
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "players_2026.json").write_bytes(get(
        f"https://statsapi.mlb.com/api/v1/sports/1/players?season={SEASON}&fields=people,id,fullName,firstName,lastName,"
        "height,primaryPosition,abbreviation,type,currentTeam,id,batSide,code,pitchHand,code"))
    (RAW / "teams_2026.json").write_bytes(get(
        f"https://statsapi.mlb.com/api/v1/teams?sportId=1&season={SEASON}&fields=teams,id,name,abbreviation,teamName,"
        "locationName,league,division,id,name,venue"))
    n = len(json.loads((RAW / "players_2026.json").read_text())["people"])
    print(f"players: {n}")


def final_games():
    d = json.loads((RAW / "schedule_2026.json").read_text())
    games = {}
    for day in d["dates"]:
        for g in day["games"]:
            if g["status"].get("codedGameState") == "F":
                games[g["gamePk"]] = g  # suspended games appear twice; keep one record per gamePk
    return games


def statcast():
    games = final_games()
    days = sorted({g["officialDate"] for g in games.values()})
    folder = RAW / "statcast"
    folder.mkdir(parents=True, exist_ok=True)
    print(f"statcast: {len(days)} game days, {days[0]} to {days[-1]}")
    for i, day in enumerate(days):
        out = folder / f"{day}.csv"
        if out.exists() and out.stat().st_size > 1000:
            continue
        q = urllib.parse.urlencode({
            "all": "true", "hfGT": "R|", "hfSea": f"{SEASON}|", "player_type": "pitcher",
            "game_date_gt": day, "game_date_lt": day, "min_pitches": 0, "min_results": 0,
            "group_by": "name", "sort_col": "pitches", "sort_order": "desc", "min_abs": 0, "type": "details"})
        body = get("https://baseballsavant.mlb.com/statcast_search/csv?" + q)
        out.write_bytes(body)
        rows = body.count(b"\n") - 1
        print(f"  [{i + 1}/{len(days)}] {day}: {rows} pitches", flush=True)
        time.sleep(1.5)


PBP_FIELDS = ("allPlays,atBatIndex,about,inning,isTopInning,playEvents,index,pitchNumber,isPitch,details,code,"
              "hasReview,reviewDetails,isOverturned,inProgress,reviewType,challengeTeamId,player,id,count,balls,"
              "strikes,outs")


def challenges():
    games = final_games()
    folder = RAW / "pbp"
    folder.mkdir(parents=True, exist_ok=True)
    todo = [pk for pk in sorted(games) if not (folder / f"{pk}.json").exists()]
    print(f"challenges: {len(games)} final games, {len(todo)} to fetch")
    for i, pk in enumerate(todo):
        body = get(f"https://statsapi.mlb.com/api/v1/game/{pk}/playByPlay?fields={PBP_FIELDS}")
        (folder / f"{pk}.json").write_bytes(body)
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{len(todo)}", flush=True)
        time.sleep(0.25)
    print("challenges: done")


if __name__ == "__main__":
    {"schedule": schedule, "players": players, "statcast": statcast, "challenges": challenges}[sys.argv[1]]()
