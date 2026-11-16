"""Histórico para fontes que só publicam a versão atual (Meraki, LoL Wiki).

Essas fontes não têm "o dado de 2023": só o estado de hoje. Para não perder a evolução,
cada coleta grava duas tabelas:

- `<tabela>`: a versão atual completa (sobrescrita);
- `<tabela>_history`: uma linha nova só quando o conteúdo de um item muda, com a data
  em que a mudança foi vista (`seen_at`). Com o tempo, isso responde perguntas como
  "quando o campeão X mudou de posição/atributos".
"""

from __future__ import annotations

import hashlib

import pandas as pd

from ..context import now_utc
from ..storage import dataset_key, read_dataset, write_dataset
from .base import SourceResult


def _row_hash(row: pd.Series, columns: list[str]) -> str:
    h = hashlib.sha256()
    for c in columns:
        h.update(str(row[c]).encode())
        h.update(b"\x1f")
    return h.hexdigest()[:16]


def write_with_history(df: pd.DataFrame, key: str, source: str, table: str, result: SourceResult) -> None:
    df = df.copy()
    content_cols = [c for c in df.columns if c != key]
    df["content_hash"] = df.apply(_row_hash, axis=1, columns=content_cols)
    result.record(dataset_key(source, table), write_dataset(df, source, table))

    seen = now_utc().strftime("%Y-%m-%d")
    history = read_dataset(source, f"{table}_history")
    if history is not None and not history.empty:
        last = history.sort_values("seen_at").groupby(key)["content_hash"].last()
        changed = df[df[key].map(last).ne(df["content_hash"])]
    else:
        changed = df
    if changed.empty and history is not None:
        result.unchanged.append(dataset_key(source, f"{table}_history"))
        return
    new = changed.assign(seen_at=seen)
    merged = new if history is None else pd.concat([history, new], ignore_index=True)
    result.record(dataset_key(source, f"{table}_history"),
                  write_dataset(merged, source, f"{table}_history", date_col="seen_at"))
