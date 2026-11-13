"""Build the DuckDB database from the raw downloads and run the data-quality checks.

    python pipeline/build_db.py

1. sql/01_load.sql, 02_pitches.sql, 03_innings.sql  -> tables raw_statcast, pitches, half_innings, pitch_runs
2. challenges left for each team before every pitch (needs a running count, so it is done here in Python)
3. data checks -> data/out/checks.json (each check has a name, a value and pass/fail)
"""
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "tapor.duckdb"
OUTDIR = ROOT / "data" / "out"


def run_sql(con):
    for f in sorted((ROOT / "sql").glob("0*.sql")):
        con.execute(f.read_text())
        print(f"ran {f.name}")


def challenge_state(con):
    """Challenges left for the batting and fielding team before each pitch.

    Rules (MLB, 2026): each club starts with two; a successful challenge is kept, a failed one is lost; in each
    extra inning a club with none left entering the inning is given one.
    """
    df = con.execute("""
        SELECT p.game_pk, p.at_bat_number, p.pitch_number, p.inning, p.is_top, p.bat_team_id, p.fld_team_id,
               p.challenged, p.overturned, p.challenge_team_id
        FROM pitches p ORDER BY p.game_pk, p.at_bat_number, p.pitch_number""").df()
    bat_left = np.zeros(len(df), dtype=np.int8)
    fld_left = np.zeros(len(df), dtype=np.int8)
    anomalies = []
    left, cur_game, cur_inning = {}, None, None
    for i, r in enumerate(df.itertuples(index=False)):
        if r.game_pk != cur_game:
            cur_game, cur_inning, left = r.game_pk, None, {}
        for t in (r.bat_team_id, r.fld_team_id):
            left.setdefault(t, 2)
        if r.inning != cur_inning:
            cur_inning = r.inning
            if r.inning >= 10:
                for t in left:
                    if left[t] == 0:
                        left[t] = 1
        bat_left[i], fld_left[i] = left[r.bat_team_id], left[r.fld_team_id]
        if r.challenged:
            t = r.challenge_team_id
            if left.get(t, 2) <= 0:
                anomalies.append((r.game_pk, r.at_bat_number, r.pitch_number, t))
            if not r.overturned:
                left[t] = max(0, left.get(t, 2) - 1)
    df["bat_ch_left"], df["fld_ch_left"] = bat_left, fld_left
    con.register("st", df[["game_pk", "at_bat_number", "pitch_number", "bat_ch_left", "fld_ch_left"]])
    con.execute("CREATE OR REPLACE TABLE pitch_state AS SELECT * FROM st")
    return anomalies


def derived(con):
    con.execute("""
        CREATE OR REPLACE TABLE called AS
        SELECT p.*, s.bat_ch_left, s.fld_ch_left, r.runs_rest, r.complete,
               coalesce(pl.position NOT IN ('P', 'TWP'), false) AS position_player_pitching,
               -- which side the umpire's call went against, and who on that side may challenge it
               CASE p.orig_call WHEN 'S' THEN 'offense' WHEN 'B' THEN 'defense' END AS wronged_side,
               CASE p.orig_call WHEN 'S' THEN p.bat_team_id WHEN 'B' THEN p.fld_team_id END AS wronged_team_id,
               CASE p.orig_call WHEN 'S' THEN s.bat_ch_left WHEN 'B' THEN s.fld_ch_left END AS wronged_ch_left
        FROM pitches p
        JOIN pitch_state s USING (game_pk, at_bat_number, pitch_number)
        JOIN pitch_runs r USING (game_pk, at_bat_number, pitch_number)
        LEFT JOIN players pl ON pl.player_id = p.pitcher
        WHERE p.is_called
    """)
    con.execute("""
        ALTER TABLE called ADD COLUMN opportunity BOOLEAN;
        UPDATE called SET opportunity = wronged_ch_left >= 1 AND NOT position_player_pitching;
    """)


def checks(con, anomalies):
    q = lambda s: con.execute(s).fetchone()[0]
    out = []

    def add(name, value, ok, note=""):
        out.append({"check": name, "value": value, "pass": bool(ok), "note": note})

    n_raw = q("SELECT count(*) FROM raw_statcast WHERE game_type = 'R'")
    n = q("SELECT count(*) FROM pitches")
    add("Statcast regular-season rows", n_raw, n_raw > 600_000)
    add("Duplicate pitch rows removed", n_raw - n, (n_raw - n) < 0.01 * n_raw)
    add("Games in Statcast", q("SELECT count(DISTINCT game_pk) FROM pitches"),
        q("SELECT count(DISTINCT game_pk) FROM pitches") >= 2425, "2,429 games were completed in 2026")
    add("Pitches matched to MLB Stats API pitch events", round(q("SELECT avg(matched_api::INT) FROM pitches"), 5),
        q("SELECT avg(matched_api::INT) FROM pitches") > 0.995)
    api_ch = q("SELECT count(*) FROM pbp WHERE review_type = 'MJ'")
    sc_ch = q("SELECT count(*) FROM pitches WHERE challenged")
    add("ABS challenges in the API", api_ch, api_ch > 1000)
    add("ABS challenges matched to a Statcast pitch", sc_ch, sc_ch >= 0.995 * api_ch)
    add("Challenges on pitches that are not called balls/strikes",
        q("SELECT count(*) FROM pitches WHERE challenged AND NOT is_called"),
        q("SELECT count(*) FROM pitches WHERE challenged AND NOT is_called") <= 0.005 * sc_ch)
    add("Teams mapped from Statcast abbreviations", q("SELECT count(DISTINCT bat_abbr) FROM pitches WHERE bat_team_id IS NOT NULL"),
        q("SELECT count(*) FROM pitches WHERE bat_team_id IS NULL OR fld_team_id IS NULL") == 0)
    miss = q("SELECT avg((x_mid IS NULL OR z_mid IS NULL OR sz_top IS NULL)::INT) FROM pitches WHERE is_called")
    add("Called pitches missing tracking or zone data", round(miss, 5), miss < 0.005)
    ratio = con.execute("SELECT avg(sz_bot / sz_top), stddev(sz_bot / sz_top) FROM pitches WHERE sz_top > 0").fetchone()
    add("Zone bottom / top ratio (rule: 27 / 53.5 = 0.5047)", round(ratio[0], 5), abs(ratio[0] - 27 / 53.5) < 0.001,
        f"sd {ratio[1]:.6f}")
    const = q("""SELECT avg(((max_top - min_top) * 12 <= 0.1)::INT) FROM (
                 SELECT batter, min(sz_top) AS min_top, max(sz_top) AS max_top
                 FROM pitches WHERE sz_top IS NOT NULL GROUP BY ALL)""")
    add("Batters whose ABS zone stays within 0.1 in. all season", round(const, 4), const > 0.98,
        "some heights switch between inch and cm precision mid-season (~0.01 in.)")
    add("Challenges made with none left (data or rule anomalies)", len(anomalies), len(anomalies) <= 0.002 * sc_ch,
        "could be technical-failure retentions")
    complete = q("SELECT avg(complete::INT) FROM half_innings")
    add("Half-innings played to three outs", round(complete, 4), complete > 0.97)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / "checks.json").write_text(json.dumps(out, indent=1))
    for c in out:
        print(f"  [{'ok' if c['pass'] else 'FAIL'}] {c['check']}: {c['value']} {c['note']}")
    return out


if __name__ == "__main__":
    con = duckdb.connect(str(DB))
    run_sql(con)
    anomalies = challenge_state(con)
    derived(con)
    checks(con, anomalies)
    print("called pitches:", con.execute("SELECT count(*) FROM called").fetchone()[0])
