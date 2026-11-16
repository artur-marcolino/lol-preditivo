"""Rating Elo por franquia, calculado jogo a jogo em ordem cronológica.

Como funciona (versão simples, para servir de variável e de baseline):
- Cada franquia começa com um rating que depende do nível da liga em que estreou
  (liga principal 1500, segunda divisão 1400, outras 1300).
- Antes de cada jogo, a chance esperada de vitória do azul é
  E = 1 / (1 + 10^((R_vermelho - R_azul) / 400)).
- Depois do jogo, cada time ganha ou perde K * (resultado - E). K é maior nos primeiros
  jogos (o rating ainda é incerto) e menor depois.
- Na virada de temporada o rating encolhe 25% em direção a 1500, porque elencos mudam.

`elo_pre` é o rating *antes* do jogo: pode ser usado como variável sem vazamento.
Os parâmetros ficam aqui como ponto de partida; ajustá-los é parte da modelagem.
"""

from __future__ import annotations

import duckdb
import pandas as pd

INITIAL = {1: 1500.0, 2: 1500.0, 3: 1400.0, 4: 1300.0}
K_PROVISIONAL, K_REGULAR, PROVISIONAL_GAMES = 32.0, 20.0, 20
SEASON_SHRINK = 0.75
MEAN = 1500.0


def build(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    games = con.execute("""
        SELECT g.game_id, g.game_time, g.season, g.blue_win, coalesce(l.tier, 4) AS tier,
               b.franchise_id AS blue_f, r.franchise_id AS red_f
        FROM core.fct_game g
        JOIN core.fct_team_game b ON b.game_id = g.game_id AND b.side = 'blue'
        JOIN core.fct_team_game r ON r.game_id = g.game_id AND r.side = 'red'
        LEFT JOIN core.dim_league l ON l.league = g.league
        ORDER BY g.game_time, g.game_id
    """).df()

    rating: dict[str, float] = {}
    n_games: dict[str, int] = {}
    last_season: dict[str, int] = {}
    rows = []

    def current(f: str, season: int, tier: int) -> float:
        if f not in rating:
            rating[f] = INITIAL.get(tier, 1300.0)
            n_games[f] = 0
        elif last_season.get(f) is not None and season > last_season[f]:
            rating[f] = MEAN + SEASON_SHRINK * (rating[f] - MEAN)
        last_season[f] = season
        return rating[f]

    for g in games.itertuples(index=False):
        rb = current(g.blue_f, g.season, g.tier)
        rr = current(g.red_f, g.season, g.tier)
        exp_blue = 1.0 / (1.0 + 10 ** ((rr - rb) / 400.0))
        rows.append((g.game_id, g.blue_f, rb, rr, exp_blue))
        rows.append((g.game_id, g.red_f, rr, rb, 1.0 - exp_blue))
        if pd.isna(g.blue_win):
            continue  # jogo sem resultado válido não atualiza o rating
        for f, exp, res in ((g.blue_f, exp_blue, g.blue_win), (g.red_f, 1.0 - exp_blue, 1 - g.blue_win)):
            k = K_PROVISIONAL if n_games[f] < PROVISIONAL_GAMES else K_REGULAR
            rating[f] += k * (res - exp)
            n_games[f] += 1

    out = pd.DataFrame(rows, columns=["game_id", "franchise_id", "elo_pre", "opp_elo_pre", "elo_win_prob"])
    con.register("_elo", out)
    con.execute("CREATE SCHEMA IF NOT EXISTS feat")
    con.execute("CREATE OR REPLACE TABLE feat.team_elo AS SELECT * FROM _elo")
    con.unregister("_elo")
    return out
