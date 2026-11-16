"""Testa o banco analítico com um mini-campeonato sintético de respostas conhecidas.

Cenário (liga LCK):
  S1  dia 1   Alpha x Beta   Bo3, Alpha vence 2-1
  S2  dia 2   Beta  x Gamma  Bo1, Gamma vence
  S3  dia 3   Alpha x Gamma  Bo3, Alpha vence 2-0
  S4  dia 10  Alpha Nova x Beta  Bo1  (Alpha trocou de nome e de ID, mesmo elenco), Beta vence
"""

from __future__ import annotations

from datetime import datetime, timedelta

import duckdb
import numpy as np
import pandas as pd
import pytest

from lolpred import db
from lolpred.storage import write_dataset
from lolpred.warehouse import checks

ROLES = ["top", "jng", "mid", "bot", "sup"]
TEAMS = {
    "Alpha": ("oe:team:alpha", "a"),
    "Alpha Nova": ("oe:team:alpha2", "a"),  # mesmo elenco de Alpha
    "Beta": ("oe:team:beta", "b"),
    "Gamma": ("oe:team:gamma", "g"),
}
CHAMPS = ["Aatrox", "Lee Sin", "Ahri", "Jinx", "Thresh", "Gnar", "Vi", "Orianna", "Kai'Sa", "Nautilus"]
NUMERIC = ["kills", "deaths", "assists", "team_kpm", "ckpm", "firstblood", "firstdragon", "dragons", "opp_dragons",
           "elders", "firstherald", "heralds", "void_grubs", "opp_void_grubs", "firstbaron", "barons", "opp_barons",
           "atakhans", "firsttower", "towers", "opp_towers", "firstmidtower", "firsttothreetowers", "turretplates",
           "opp_turretplates", "inhibitors", "dpm", "damagetakenperminute", "wpm", "wcpm", "vspm",
           "controlwardsbought", "earned_gpm", "gspd", "cspm", "golddiffat10", "xpdiffat10", "csdiffat10",
           "golddiffat15", "xpdiffat15", "csdiffat15", "golddiffat20", "golddiffat25", "killsat15", "deathsat15",
           "firstbloodkill", "firstbloodvictim", "damageshare", "earnedgoldshare", "assistsat15"]

# (série, dia, time azul do jogo 1, time vermelho do jogo 1, vencedores por jogo)
SERIES = [
    ("S1", 1, "Alpha", "Beta", ["Alpha", "Beta", "Alpha"]),
    ("S2", 2, "Beta", "Gamma", ["Gamma"]),
    ("S3", 3, "Alpha", "Gamma", ["Alpha", "Alpha"]),
    ("S4", 10, "Alpha Nova", "Beta", ["Beta"]),
]


def make_oe() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    rows = []
    for sid, day, t1, t2, winners in SERIES:
        for g, winner in enumerate(winners, start=1):
            blue, red = (t1, t2) if g % 2 == 1 else (t2, t1)  # alterna lados
            when = datetime(2026, 3, 1, 10) + timedelta(days=day, hours=g)
            base = {"gameid": f"{sid}-G{g}", "league": "LCK", "year": 2026, "split": "Spring", "playoffs": 0,
                    "date": when.strftime("%Y-%m-%d %H:%M:%S"), "game": float(g), "patch": 16.05,
                    "gamelength": 1800, "datacompleteness": "complete", "url": None}
            for side, team in (("Blue", blue), ("Red", red)):
                tid, roster = TEAMS[team]
                win = int(team == winner)
                nums = {c: float(rng.integers(0, 5)) for c in NUMERIC}
                nums["golddiffat15"] = 1500.0 if win else -1500.0
                rows.append({**base, **nums, "side": side, "position": "team", "teamid": tid, "teamname": team,
                             "playerid": None, "playername": None, "champion": None, "result": win,
                             "participantid": 100 if side == "Blue" else 200, "firstPick": int(side == "Blue"),
                             **{f"ban{i}": CHAMPS[(i + g) % 10] for i in range(1, 6)},
                             **{f"pick{i}": None for i in range(1, 6)}})
                for i, role in enumerate(ROLES):
                    rows.append({**base, **nums, "side": side, "position": role, "teamid": tid, "teamname": team,
                                 "playerid": f"oe:player:{roster}{i}", "playername": f"{roster.upper()}{role}",
                                 "champion": CHAMPS[i if side == "Blue" else i + 5], "result": win,
                                 "participantid": i + 1, "firstPick": None,
                                 **{f"ban{k}": None for k in range(1, 6)}, **{f"pick{k}": None for k in range(1, 6)}})
    return pd.DataFrame(rows)


@pytest.fixture
def con(tmp_data):
    write_dataset(make_oe(), "oracles_elixir", "matches", "2026", date_col="date")
    db.build()
    c = duckdb.connect(str(tmp_data / "lol.duckdb"), read_only=True)
    yield c
    c.close()


def one(con, sql):
    return con.execute(sql).fetchone()


def test_series_are_reconstructed(con):
    rows = con.execute("SELECT series_id, best_of, team1_name, team1_wins, team2_wins FROM core.fct_series ORDER BY start_time").fetchall()
    assert rows == [("s_S1-G1", 3, "Alpha", 2, 1), ("s_S2-G1", 1, "Beta", 0, 1),
                    ("s_S3-G1", 3, "Alpha", 2, 0), ("s_S4-G1", 1, "Alpha Nova", 0, 1)]


def test_renamed_team_joins_franchise(con):
    assert one(con, "SELECT franchise_id FROM core.team_lineage WHERE team_id = 'oe:team:alpha2'")[0] == "oe:team:alpha"
    assert one(con, "SELECT franchise_name FROM core.team_lineage WHERE team_id = 'oe:team:alpha'")[0] == "Alpha Nova"


def test_features_use_only_past_games(con):
    # Primeira série: ninguém tem histórico
    assert one(con, "SELECT games_total, winrate_last10 FROM feat.series_team WHERE series_id = 's_S1-G1' AND team_id = 'oe:team:alpha'") == (0, None)
    # S3: Alpha jogou só a S1 (3 jogos, 2 vitórias); nenhum jogo da S3 entra
    gt, wr = one(con, "SELECT games_total, winrate_last10 FROM feat.series_team WHERE series_id = 's_S3-G1' AND team_id = 'oe:team:alpha'")
    assert gt == 3 and wr == pytest.approx(2 / 3)
    # S4: o histórico segue a franquia mesmo com ID novo (3 + 2 = 5 jogos, 4 vitórias)
    gt, wr, cont, series_total = one(con, """SELECT games_total, winrate_last10, lineup_continuity, series_total
        FROM feat.series_team WHERE series_id = 's_S4-G1' AND team_id = 'oe:team:alpha2'""")
    assert (gt, cont, series_total) == (5, 5, 2) and wr == pytest.approx(4 / 5)
    # Confronto direto Alpha x Beta antes da S4: 1 série, vencida pela Alpha
    assert one(con, "SELECT h2h_series, h2h_series_winrate FROM feat.series_team WHERE series_id = 's_S4-G1' AND team_id = 'oe:team:alpha2'") == (1, 1.0)
    # Experiência dos jogadores do Beta antes da S4: S1 (3 jogos) + S2 (1 jogo)
    assert one(con, "SELECT lineup_games_mean FROM feat.series_team WHERE series_id = 's_S4-G1' AND team_id = 'oe:team:beta'")[0] == 4


def test_pregame_tables(con):
    assert one(con, "SELECT count(*) FROM mart.pregame_series")[0] == 4
    # S1 jogo 3: antes dele estava 1-1
    assert one(con, """SELECT blue_team_series_wins_before, red_team_series_wins_before
                       FROM mart.pregame_games WHERE game_id = 'S1-G3'""") == (1, 1)
    t1, t2, diff = one(con, "SELECT t1_games_total, t2_games_total, diff_games_total FROM mart.pregame_series WHERE series_id = 's_S4-G1'")
    assert (t1, t2, diff) == (5, 4, 1)


def test_elo_is_pregame(con):
    # Antes do primeiro jogo todos começam iguais; depois a Alpha (que venceu) sobe
    assert one(con, "SELECT elo_pre FROM feat.team_elo WHERE game_id = 'S1-G1' AND franchise_id = 'oe:team:alpha'")[0] == 1500
    assert one(con, "SELECT elo_pre FROM feat.team_elo WHERE game_id = 'S1-G2' AND franchise_id = 'oe:team:alpha'")[0] > 1500


def test_quality_checks_pass(con):
    failures = [(name, n) for name, n in checks.run(con) if n]
    assert failures == []
