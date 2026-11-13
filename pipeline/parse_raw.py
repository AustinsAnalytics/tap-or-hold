"""Flatten the raw JSON downloads into parquet tables that DuckDB can join.

    python pipeline/parse_raw.py

Writes data/interim/{pbp_pitches,games,players,teams}.parquet.
"""
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW, OUT = ROOT / "data" / "raw", ROOT / "data" / "interim"


def height_inches(h):
    # MLB Stats API heights look like 6' 2"
    if not isinstance(h, str) or "'" not in h:
        return None
    ft, inch = h.replace('"', "").split("'")
    return int(ft) * 12 + int(inch.strip() or 0)


def pbp():
    rows = []
    for f in sorted((RAW / "pbp").glob("*.json")):
        pk = int(f.stem)
        for play in json.loads(f.read_text()).get("allPlays", []):
            ab = play["atBatIndex"]
            for e in play.get("playEvents", []):
                if not e.get("isPitch"):
                    continue
                rd = e.get("reviewDetails") or {}
                rows.append({
                    "game_pk": pk,
                    "at_bat_number": ab + 1,                      # Statcast numbers plate appearances from 1
                    "pitch_number": e.get("pitchNumber"),
                    "inning_api": play["about"]["inning"],
                    "is_top_api": play["about"]["isTopInning"],
                    "call_code_api": (e.get("details") or {}).get("code"),
                    "review_type": rd.get("reviewType"),
                    "overturned": rd.get("isOverturned"),
                    "challenge_team_id": rd.get("challengeTeamId"),
                    "challenger_id": (rd.get("player") or {}).get("id"),
                })
    df = pd.DataFrame(rows)
    df.to_parquet(OUT / "pbp_pitches.parquet", index=False)
    abs_ = df[df.review_type == "MJ"]
    print(f"pbp: {df.game_pk.nunique()} games, {len(df)} pitches, {len(abs_)} ABS challenges "
          f"({abs_.overturned.mean():.1%} overturned)")


def games():
    d = json.loads((RAW / "schedule_2026.json").read_text())
    rows = {}
    for day in d["dates"]:
        for g in day["games"]:
            if g["status"].get("codedGameState") != "F":
                continue
            h, a = g["teams"]["home"], g["teams"]["away"]
            rows[g["gamePk"]] = {
                "game_pk": g["gamePk"], "official_date": g["officialDate"],
                "home_id": h["team"]["id"], "away_id": a["team"]["id"],
                "home_runs": h.get("score"), "away_runs": a.get("score"),
                "venue_id": g["venue"]["id"], "venue": g["venue"]["name"],
                "scheduled_innings": g.get("scheduledInnings", 9),
                "detailed_state": g["status"].get("detailedState"),
            }
    pd.DataFrame(rows.values()).to_parquet(OUT / "games.parquet", index=False)
    print(f"games: {len(rows)}")


def players_teams():
    people = json.loads((RAW / "players_2026.json").read_text())["people"]
    pdf = pd.DataFrame([{
        "player_id": p["id"], "name": p.get("fullName"), "first": p.get("firstName"), "last": p.get("lastName"),
        "height_in": height_inches(p.get("height")), "position": (p.get("primaryPosition") or {}).get("abbreviation"),
        "team_id": (p.get("currentTeam") or {}).get("id"), "bats": (p.get("batSide") or {}).get("code"),
        "throws": (p.get("pitchHand") or {}).get("code"),
    } for p in people])
    pdf.to_parquet(OUT / "players.parquet", index=False)
    teams = json.loads((RAW / "teams_2026.json").read_text())["teams"]
    tdf = pd.DataFrame([{
        "team_id": t["id"], "abbr": t["abbreviation"], "team": t["name"], "club": t.get("teamName"),
        "league": (t.get("league") or {}).get("name"), "division": (t.get("division") or {}).get("name"),
        "venue_id": (t.get("venue") or {}).get("id"),
    } for t in teams])
    tdf.to_parquet(OUT / "teams.parquet", index=False)
    print(f"players: {len(pdf)}, teams: {len(tdf)}")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    games()
    players_teams()
    pbp()
