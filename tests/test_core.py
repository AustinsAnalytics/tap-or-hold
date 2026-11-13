"""Unit tests for the rules and the arithmetic the whole project rests on. Run: python -m pytest -q tests"""
import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import build_db  # noqa: E402
import perception  # noqa: E402
import values  # noqa: E402


# ---------------------------------------------------------------- base-running on a walk
@pytest.mark.parametrize("bases,expected", [
    (0, (1, 0)),            # empty -> runner on first
    (1, (3, 0)),            # first -> first and second
    (2, (3, 0)),            # second only -> first and second (runner on second not forced)
    (4, (5, 0)),            # third only -> first and third
    (3, (7, 0)),            # first and second -> loaded
    (5, (7, 0)),            # first and third -> loaded
    (7, (7, 1)),            # loaded -> run scores
])
def test_walk(bases, expected):
    assert values.walk(bases) == expected


# ---------------------------------------------------------------- the ABS rectangle (SQL macro)
def edge(x_ft, z_ft, top=3.5, bot=1.5):
    con = duckdb.connect()
    sql = (ROOT / "sql" / "02_pitches.sql").read_text()
    start = sql.index("CREATE OR REPLACE MACRO")
    macro = sql[start:sql.index("END;", start) + 4]
    con.execute(macro)
    return con.execute("SELECT edge_in(?, ?, ?, ?)", [x_ft, z_ft, top, bot]).fetchone()[0]


def test_edge_inside_is_negative():
    assert edge(0.0, 2.5) == pytest.approx(-8.5)             # middle: nearest edges are the sides, 8.5 in. away


def test_edge_on_side_and_corner():
    assert edge(8.5 / 12 + 1 / 12, 2.5) == pytest.approx(1.0)          # 1 in. outside the side edge
    d = edge(8.5 / 12 + 3 / 12, 3.5 + 4 / 12)                            # 3 in. wide and 4 in. high: corner
    assert d == pytest.approx(5.0)                                       # 3-4-5


# ---------------------------------------------------------------- challenge counting rules
def make_game(events):
    rows = []
    for i, (inning, is_top, challenged, overturned, team) in enumerate(events):
        rows.append(dict(game_pk=1, at_bat_number=i + 1, pitch_number=1, inning=inning, is_top=is_top,
                         bat_team_id=10 if is_top else 20, fld_team_id=20 if is_top else 10,
                         challenged=challenged, overturned=overturned, challenge_team_id=team))
    con = duckdb.connect()
    con.register("df", pd.DataFrame(rows))
    con.execute("CREATE TABLE pitches AS SELECT * FROM df")
    build_db.challenge_state(con)
    return con.execute("SELECT * FROM pitch_state ORDER BY at_bat_number").df()


def test_lost_challenge_costs_one_and_won_challenge_is_kept():
    s = make_game([
        (1, True, True, True, 10),     # team 10 (batting) wins a challenge: keeps both
        (1, True, True, False, 10),    # loses one -> 1 left
        (2, True, False, None, None),
    ])
    assert list(s.bat_ch_left) == [2, 2, 1]


def test_extra_inning_award():
    s = make_game([
        (3, True, True, False, 10),
        (4, True, True, False, 10),    # team 10 now has none
        (9, True, False, None, None),
        (10, True, False, None, None),  # entering the 10th with none -> one awarded
        (10, True, True, False, 10),   # spends it
        (11, True, False, None, None),  # and gets another in the 11th
    ])
    assert list(s.bat_ch_left) == [2, 1, 0, 1, 1, 1]


# ---------------------------------------------------------------- beliefs
def test_belief_curve_is_monotone_and_bounded():
    rng = np.random.default_rng(0)
    w = np.r_[rng.normal(-8, 4, 20000), rng.normal(0.8, 0.8, 1500)]
    p = perception.belief_curve(w, 2.0)
    assert np.all(np.diff(p) >= -1e-9)
    assert 0 <= p.min() and p.max() <= 1


# ---------------------------------------------------------------- the built data passed its checks
@pytest.mark.skipif(not (ROOT / "data" / "out" / "checks.json").exists(), reason="data not built")
def test_data_checks_pass():
    checks = json.loads((ROOT / "data" / "out" / "checks.json").read_text())
    failed = [c["check"] for c in checks if not c["pass"]]
    assert not failed, failed
