"""Constrói o banco analítico (DuckDB) a partir dos Parquet.

Ordem das etapas:
    raw   views sobre os Parquet (db.py)
    10    stg   limpeza e tipos
    20    core  dimensões e fatos
    py    core.team_lineage (linhagem de times)
    25    core  franquias, dim_team, fct_team_series
    26    core  dim_champion (se houver Data Dragon)
    py    feat.team_elo
    30    feat  variáveis pré-partida
    40    mart  tabelas de análise de meta
    py    mart.pregame_series e mart.pregame_games
"""

from __future__ import annotations

import time
from importlib import resources

import duckdb

from ..sources.base import log
from . import elo, lineage, marts


def _sql(name: str) -> str:
    return (resources.files("lolpred") / "sql" / name).read_text(encoding="utf-8")


def _has_view(con: duckdb.DuckDBPyConnection, schema: str, name: str) -> bool:
    return bool(con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
        [schema, name]).fetchone()[0])


def _load_seeds(con: duckdb.DuckDBPyConnection) -> None:
    path = resources.files("lolpred") / "seeds" / "leagues.csv"
    con.execute("CREATE SCHEMA IF NOT EXISTS core")
    con.execute(f"CREATE OR REPLACE TABLE core.seed_leagues AS SELECT * FROM read_csv('{path}', header = true, "
                "columns = {'league': 'VARCHAR', 'league_name': 'VARCHAR', 'region': 'VARCHAR', "
                "'tier': 'INTEGER', 'notes': 'VARCHAR'})")


def build(con: duckdb.DuckDBPyConnection) -> dict[str, float]:
    if not _has_view(con, "raw", "oe_matches"):
        raise RuntimeError("raw.oe_matches não existe: rode `lolpred pull` (ou colete o Oracle's Elixir) antes")
    timings: dict[str, float] = {}

    def step(name, fn):
        t0 = time.monotonic()
        fn()
        timings[name] = round(time.monotonic() - t0, 1)
        log.info("  %-28s %5.1fs", name, timings[name])

    step("seeds", lambda: _load_seeds(con))
    step("10_stg", lambda: con.execute(_sql("10_stg.sql")))
    step("20_core", lambda: con.execute(_sql("20_core.sql")))
    step("lineage", lambda: lineage.build(con))
    step("25_franchise", lambda: con.execute(_sql("25_franchise.sql")))
    if _has_view(con, "raw", "dd_champions"):
        step("26_dim_champion", lambda: con.execute(_sql("26_dim_champion.sql")))
    step("elo", lambda: elo.build(con))
    step("30_feat", lambda: con.execute(_sql("30_feat.sql")))
    step("40_mart", lambda: con.execute(_sql("40_mart.sql")))
    step("pregame_marts", lambda: marts.build(con))
    return timings
