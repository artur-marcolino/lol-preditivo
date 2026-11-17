"""EsportsBench: resultados de partidas de LoL desde 2011, em nível de time.

Conjunto de dados de Clayton Thorrez para comparar sistemas de rating em esports
(hf.co/datasets/EsportsBench/EsportsBench). A parte de LoL vem da Leaguepedia
(CC BY-SA 3.0): data, os dois competidores, resultado (0, 0,5 ou 1) e a página de origem.
Sai em versões (1.0, 2.0, ...), com cortes de treino e teste definidos pelo autor.

Não traz informação nova em relação à Leaguepedia; serve como régua externa: o mesmo
conjunto que outros trabalhos usam para medir sistemas de rating (Elo, Glicko, TrueSkill...).
A coleta compara a versão (sha) do repositório e só baixa de novo quando ela muda.
"""

from __future__ import annotations

import io

import pandas as pd
import requests

from ..context import RunContext
from ..storage import dataset_key, load_manifest, touch_dataset, write_dataset
from .base import SourceResult, log

SOURCE = "esportsbench"
REPO = "EsportsBench/EsportsBench"
INFO_URL = f"https://huggingface.co/api/datasets/{REPO}"
PARQUET_API = f"https://huggingface.co/api/datasets/{REPO}/parquet"
FILE_URL = f"https://huggingface.co/datasets/{REPO}/resolve/{{sha}}/{{path}}"
SPLIT = "league_of_legends"


def parquet_urls(session: requests.Session, info: dict) -> list[str]:
    """URLs dos arquivos do split de LoL: primeiro pela API de Parquet, depois pela lista de arquivos."""
    try:
        resp = session.get(PARQUET_API, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        urls = [u for config in data.values() if isinstance(config, dict)
                for split, lst in config.items() if split == SPLIT for u in lst]
        if urls:
            return urls
    except (requests.RequestException, ValueError):
        pass
    files = [s["rfilename"] for s in info.get("siblings", [])]
    wanted = [f for f in files if SPLIT in f and f.endswith((".parquet", ".csv"))]
    return [FILE_URL.format(sha=info.get("sha", "main"), path=f) for f in wanted]


def collect(ctx: RunContext) -> SourceResult:
    result = SourceResult()
    key = dataset_key(SOURCE, "matches")
    try:
        resp = ctx.session.get(INFO_URL, timeout=60)
        resp.raise_for_status()
        info = resp.json()
        sha = info.get("sha")
        if sha and not ctx.full and load_manifest(SOURCE)["datasets"].get(key, {}).get("hf_sha") == sha:
            result.skipped.append(key)
            return result
        urls = parquet_urls(ctx.session, info)
        if not urls:
            result.error(f"nenhum arquivo do split {SPLIT} encontrado em {REPO}")
            return result
        frames = []
        for url in urls:
            log.info("  EsportsBench: baixando %s", url.rsplit("/", 1)[-1])
            r = ctx.session.get(url, timeout=300)
            r.raise_for_status()
            buf = io.BytesIO(r.content)
            frames.append(pd.read_csv(buf) if url.endswith(".csv") else pd.read_parquet(buf))
        df = pd.concat(frames, ignore_index=True)
        changed = write_dataset(df, SOURCE, "matches", date_col="date" if "date" in df.columns else None)
        touch_dataset(SOURCE, "matches", hf_sha=sha)
        result.record(key, changed)
    except (requests.RequestException, ValueError) as e:
        result.error(f"EsportsBench: {e}")
    return result
