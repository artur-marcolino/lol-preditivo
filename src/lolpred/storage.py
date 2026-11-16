"""Armazenamento em Parquet e manifesto versionado.

Organização em disco::

    data/
      processed/<fonte>/<tabela>/<particao>.parquet   # os dados (fora do git)
      manifest/<fonte>.json                            # metadados de cada arquivo (no git)
      runs/<fonte>.json                                # log da última coleta (no git)
      CATALOG.md                                       # resumo legível (no git)

Há um manifesto por fonte para que as fontes possam ser coletadas em paralelo (um job do
GitHub Actions para cada uma) sem que uma sobrescreva o registro da outra.

O manifesto guarda, para cada arquivo, número de linhas, colunas, intervalo de datas e
uma impressão digital (fingerprint) do conteúdo. Como o manifesto é versionado, o
histórico do git mostra quando cada tabela mudou e quanto cresceu.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .context import now_utc


def data_dir() -> Path:
    return Path(os.environ.get("LOLPRED_DATA_DIR", "data"))


def processed_dir() -> Path:
    return data_dir() / "processed"


def manifest_path(source: str) -> Path:
    return data_dir() / "manifest" / f"{source}.json"


def manifest_sources() -> list[str]:
    folder = data_dir() / "manifest"
    return sorted(p.stem for p in folder.glob("*.json")) if folder.exists() else []


def dataset_path(source: str, table: str, partition: str = "all") -> Path:
    return processed_dir() / source / table / f"{partition}.parquet"


def dataset_key(source: str, table: str, partition: str = "all") -> str:
    return f"{source}/{table}/{partition}"


# --------------------------------------------------------------------------- manifesto


def load_manifest(source: str) -> dict[str, Any]:
    path = manifest_path(source)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"source": source, "status": {}, "datasets": {}}


def save_manifest(source: str, manifest: dict[str, Any]) -> None:
    path = manifest_path(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys deixa os diffs do git limpos e estáveis
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def update_source_status(source: str, status: str, message: str = "", success: bool = False) -> None:
    manifest = load_manifest(source)
    entry = manifest["status"]
    entry["last_attempt"] = now_utc().isoformat(timespec="seconds")
    entry["status"] = status
    entry["message"] = message
    if success:
        entry["last_success"] = entry["last_attempt"]
    save_manifest(source, manifest)


# --------------------------------------------------------------------------- dados


def normalize_for_parquet(df: pd.DataFrame) -> pd.DataFrame:
    """Colunas de texto/mistas viram string; nomes de coluna ficam padronizados.

    Fontes como a Leaguepedia devolvem tudo como texto e o Oracle's Elixir tem colunas
    com tipos misturados entre anos. A conversão de tipos para modelagem fica para uma
    etapa posterior; aqui o objetivo é guardar o dado fiel à fonte.
    """
    df = df.copy()
    df.columns = [str(c).strip().replace(" ", "_") for c in df.columns]
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype("string")
    return df


def fingerprint(df: pd.DataFrame) -> str:
    """Hash do conteúdo (colunas + valores), independente dos bytes do arquivo Parquet."""
    h = hashlib.sha256()
    h.update("|".join(map(str, df.columns)).encode())
    h.update(pd.util.hash_pandas_object(df, index=False).values.tobytes())
    return h.hexdigest()


def _date_range(df: pd.DataFrame, date_col: str | None) -> tuple[str | None, str | None]:
    if not date_col or date_col not in df.columns or df.empty:
        return None, None
    dates = pd.to_datetime(df[date_col], errors="coerce", utc=True).dropna()
    if dates.empty:
        return None, None
    return dates.min().isoformat(), dates.max().isoformat()


def write_dataset(
    df: pd.DataFrame,
    source: str,
    table: str,
    partition: str = "all",
    date_col: str | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    """Grava a partição e atualiza o manifesto. Retorna True se o conteúdo mudou.

    Se o fingerprint for igual ao já registrado, o arquivo não é reescrito: assim só
    sobe para a Release o que realmente mudou.
    """
    df = normalize_for_parquet(df)
    fp = fingerprint(df)
    key = dataset_key(source, table, partition)
    path = dataset_path(source, table, partition)
    manifest = load_manifest(source)
    old = manifest["datasets"].get(key)
    if old and old.get("fingerprint") == fp and path.exists():
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), tmp, compression="zstd")
    os.replace(tmp, path)  # escrita atômica: nunca fica um arquivo pela metade

    dmin, dmax = _date_range(df, date_col)
    ts = now_utc().isoformat(timespec="seconds")
    manifest["datasets"][key] = {
        "path": str(path.relative_to(data_dir())),
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "fingerprint": fp,
        "bytes": path.stat().st_size,
        "date_min": dmin,
        "date_max": dmax,
        "updated_at": ts,
        **(extra or {}),
    }
    save_manifest(source, manifest)
    return True


KNOWN_DATE_COLUMNS = ("date", "DateTime_UTC", "start_time", "start_date")


def reconcile(mark_published: bool = False) -> list[str]:
    """Registra no manifesto os Parquet que existem em disco mas não constam nele.

    Acontece quando uma coleta publicou arquivos na Release mas não conseguiu versionar o
    manifesto (falha no commit, job cancelado). Ao baixar da Release, esses arquivos são
    reconhecidos aqui e o histórico se recompõe sozinho.
    """
    added = []
    root = processed_dir()
    if not root.exists():
        return added
    for path in sorted(root.glob("*/*/*.parquet")):
        source, table, partition = path.parent.parent.name, path.parent.name, path.stem
        key = dataset_key(source, table, partition)
        manifest = load_manifest(source)
        if key in manifest["datasets"]:
            continue
        df = pd.read_parquet(path)
        fp = fingerprint(df)
        date_col = next((c for c in KNOWN_DATE_COLUMNS if c in df.columns), None)
        dmin, dmax = _date_range(df, date_col)
        manifest["datasets"][key] = {
            "path": str(path.relative_to(data_dir())), "rows": int(len(df)), "columns": int(df.shape[1]),
            "fingerprint": fp, "bytes": path.stat().st_size, "date_min": dmin, "date_max": dmax,
            "updated_at": now_utc().isoformat(timespec="seconds"), "recovered": True,
        }
        if mark_published:
            manifest.setdefault("published", {})[key] = fp
        save_manifest(source, manifest)
        added.append(key)
    return added


def read_dataset(source: str, table: str, partition: str = "all") -> pd.DataFrame | None:
    path = dataset_path(source, table, partition)
    if not path.exists():
        return None
    return pd.read_parquet(path)


def dataset_exists(source: str, table: str, partition: str = "all") -> bool:
    return dataset_path(source, table, partition).exists()
