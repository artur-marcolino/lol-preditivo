"""Tabelas finais para modelagem pré-partida.

- mart.pregame_series: uma linha por série. Variáveis do team1 (`t1_*`), do team2 (`t2_*`)
  e as diferenças (`diff_* = t1 - t2`). Alvo: `team1_win`.
- mart.pregame_games: uma linha por mapa. Variáveis do lado azul (`blue_*`) e vermelho
  (`red_*`), calculadas no início da série, mais o placar da série antes daquele mapa
  (conhecido antes do mapa começar). Alvo: `blue_win`.

As colunas t1_/t2_/diff_ são geradas a partir de feat.series_team: qualquer variável nova
adicionada lá entra aqui automaticamente.
"""

from __future__ import annotations

import duckdb

KEYS = {"series_id", "team_id", "franchise_id", "opp_team_id", "opp_franchise_id", "is_team1", "win"}
NUMERIC = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "DOUBLE", "FLOAT", "DECIMAL", "BOOLEAN")


def _feature_columns(con: duckdb.DuckDBPyConnection) -> list[tuple[str, bool]]:
    rows = con.execute("""
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_schema = 'feat' AND table_name = 'series_team' ORDER BY ordinal_position
    """).fetchall()
    return [(name, dtype.startswith(NUMERIC)) for name, dtype in rows if name not in KEYS]


def _side_select(features: list[tuple[str, bool]], alias: str, prefix: str) -> list[str]:
    return [f'{alias}."{c}" AS "{prefix}_{c}"' for c, _ in features]


def _diff_select(features: list[tuple[str, bool]], a: str, b: str) -> list[str]:
    return [f'({a}."{c}"::DOUBLE - {b}."{c}"::DOUBLE) AS "diff_{c}"' for c, numeric in features if numeric]


def build(con: duckdb.DuckDBPyConnection) -> None:
    feats = _feature_columns(con)
    con.execute("CREATE SCHEMA IF NOT EXISTS mart")

    cols = ",\n    ".join(_side_select(feats, "a", "t1") + _side_select(feats, "b", "t2") + _diff_select(feats, "a", "b"))
    con.execute(f"""
    CREATE OR REPLACE TABLE mart.pregame_series AS
    SELECT
        s.series_id, s.league, l.tier AS league_tier, l.region AS league_region, s.season, s.split, s.playoffs,
        s.patch, s.start_time, s.best_of, s.games_played, s.is_clean,
        s.team1_id, s.team1_name, a.franchise_id AS team1_franchise_id,
        s.team2_id, s.team2_name, b.franchise_id AS team2_franchise_id,
        s.team1_wins, s.team2_wins, s.team1_win,
        {cols}
    FROM core.fct_series s
    LEFT JOIN core.dim_league l USING (league)
    JOIN feat.series_team a ON a.series_id = s.series_id AND a.team_id = s.team1_id
    JOIN feat.series_team b ON b.series_id = s.series_id AND b.team_id = s.team2_id
    """)

    cols = ",\n    ".join(_side_select(feats, "bf", "blue") + _side_select(feats, "rf", "red") + _diff_select(feats, "bf", "rf"))
    con.execute(f"""
    CREATE OR REPLACE TABLE mart.pregame_games AS
    WITH score AS (
        -- vitórias de cada TIME na série antes deste mapa (o placar é conhecido antes do mapa começar)
        SELECT game_id, team_id,
               coalesce(sum(win) OVER (PARTITION BY series_id, team_id ORDER BY series_game_seq
                                       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS wins_before
        FROM core.fct_team_game
    )
    SELECT
        g.game_id, g.series_id, g.series_game_seq AS game_in_series, g.league, l.tier AS league_tier,
        l.region AS league_region, g.season, g.split, g.playoffs, g.patch, g.game_time, s.best_of,
        g.blue_team_id, g.blue_team_name, g.red_team_id, g.red_team_name,
        sb.wins_before AS blue_team_series_wins_before, sr.wins_before AS red_team_series_wins_before,
        g.blue_win, g.completeness,
        {cols}
    FROM core.fct_game g
    JOIN score sb ON sb.game_id = g.game_id AND sb.team_id = g.blue_team_id
    JOIN score sr ON sr.game_id = g.game_id AND sr.team_id = g.red_team_id
    JOIN core.fct_series s USING (series_id)
    LEFT JOIN core.dim_league l ON l.league = g.league
    JOIN feat.series_team bf ON bf.series_id = g.series_id AND bf.team_id = g.blue_team_id
    JOIN feat.series_team rf ON rf.series_id = g.series_id AND rf.team_id = g.red_team_id
    """)
