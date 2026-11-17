"""Banco DuckDB local (`data/lol.duckdb`), em camadas:

    raw   views que leem os Parquet diretamente (oe_matches, lp_scoreboardgames, les_schedule...)
    stg   limpeza e tipos
    core  dimensões e fatos (o modelo de dados)
    feat  variáveis pré-partida, sem vazamento
    mart  tabelas prontas para análise e modelagem

`lolpred build-db` recria tudo do zero em menos de um minuto. `--raw-only` cria só as views brutas.
"""

from __future__ import annotations

import duckdb

from .sources.base import log
from .storage import data_dir, processed_dir

PREFIX = {"oracles_elixir": "oe", "leaguepedia": "lp", "lolesports": "les", "ddragon": "dd",
          "meraki": "mk", "lolwiki": "wiki", "pandascore": "ps", "oddspapi": "odds",
          "riot_gcd": "gcd", "leaguepedia_gcd": "lpgcd", "pandaskill": "pskill", "esportsbench": "ebench",
          "esports_earnings": "ee"}


def view_name(source: str, table: str) -> str:
    return f"{PREFIX.get(source, source)}_{table.lower()}"


def create_raw_views(con: duckdb.DuckDBPyConnection) -> list[str]:
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    created = []
    if not processed_dir().exists():
        return created
    for source_dir in sorted(p for p in processed_dir().iterdir() if p.is_dir()):
        for table_dir in sorted(p for p in source_dir.iterdir() if p.is_dir()):
            if not list(table_dir.glob("*.parquet")):
                continue
            name = view_name(source_dir.name, table_dir.name)
            glob = str(table_dir.resolve() / "*.parquet").replace("'", "''")
            # union_by_name: anos com colunas diferentes são unidos pelo nome da coluna
            con.execute(f"CREATE OR REPLACE VIEW raw.{name} AS "
                        f"SELECT * FROM read_parquet('{glob}', union_by_name = true)")
            created.append(f"raw.{name}")
    return created


def build(db_path=None, raw_only: bool = False) -> list[str]:
    from . import warehouse

    db_path = db_path or data_dir() / "lol.duckdb"
    if not raw_only and db_path.exists():
        # o banco é 100% derivado dos Parquet: recriar do zero evita arquivo inchado
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    try:
        created = create_raw_views(con)
        log.info("%d views brutas", len(created))
        if not raw_only:
            warehouse.build(con)
        tables = con.execute("""
            SELECT table_schema || '.' || table_name FROM information_schema.tables
            WHERE table_schema IN ('raw', 'stg', 'core', 'feat', 'mart', 'link') ORDER BY 1
        """).fetchall()
    finally:
        con.close()
    log.info("DuckDB pronto em %s", db_path)
    return [t[0] for t in tables]
