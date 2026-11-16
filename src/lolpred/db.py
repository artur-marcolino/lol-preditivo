"""Banco DuckDB local montado a partir dos Parquet.

O arquivo `data/lol.duckdb` só contém *views* que leem os Parquet diretamente, então é
recriado em segundos e nunca fica desatualizado em relação aos arquivos.

Nomes das views: `<prefixo>_<tabela>` em minúsculas, por exemplo
`oe_matches`, `lp_scoreboardgames`, `les_schedule`, `dd_champions`.
"""

from __future__ import annotations

import duckdb

from .sources.base import log
from .storage import data_dir, processed_dir

PREFIX = {"oracles_elixir": "oe", "leaguepedia": "lp", "lolesports": "les", "ddragon": "dd"}


def view_name(source: str, table: str) -> str:
    return f"{PREFIX.get(source, source)}_{table.lower()}"


def build(db_path=None) -> list[str]:
    db_path = db_path or data_dir() / "lol.duckdb"
    con = duckdb.connect(str(db_path))
    created = []
    for source_dir in sorted(p for p in processed_dir().iterdir() if p.is_dir()):
        for table_dir in sorted(p for p in source_dir.iterdir() if p.is_dir()):
            files = list(table_dir.glob("*.parquet"))
            if not files:
                continue
            name = view_name(source_dir.name, table_dir.name)
            glob = str(table_dir.resolve() / "*.parquet").replace("'", "''")
            # union_by_name: anos com colunas diferentes são unidos pelo nome da coluna
            con.execute(f"CREATE OR REPLACE VIEW {name} AS "
                        f"SELECT * FROM read_parquet('{glob}', union_by_name = true)")
            created.append(name)
    con.close()
    log.info("DuckDB em %s com %d views", db_path, len(created))
    return created
