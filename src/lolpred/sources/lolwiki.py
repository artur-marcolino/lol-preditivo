"""LoL Wiki (wiki.leagueoflegends.com): dados de campeões mantidos pela comunidade.

A wiki guarda os dados de todos os campeões num módulo Lua (`Module:ChampionData/data`),
que é lido aqui em texto puro (`?action=raw`) e convertido com `lolpred.luatable`.
Uma única requisição por coleta.

O que acrescenta em relação ao Data Dragon: data e patch de lançamento, último patch em
que o campeão foi alterado (`changes`), classes atuais, posições, tipo de alcance e de
dano adaptativo e as notas de dano/resistência/controle/mobilidade/utilidade.

Conteúdo sob licença CC BY-SA (Weird Gloop); o robots.txt da wiki permite acesso
automatizado. Guardamos o histórico de mudanças (tabela `champions_history`).
"""

from __future__ import annotations

import json

import pandas as pd
import requests

from ..context import RunContext
from ..luatable import LuaParseError, loads
from .base import SourceResult, log
from .snapshots import write_with_history

SOURCE = "lolwiki"
WIKI = "https://wiki.leagueoflegends.com/en-us"
PAGE = "Module:ChampionData/data"
SCALARS = ["id", "apiname", "title", "fullname", "nickname", "resource", "rangetype", "adaptivetype",
           "date", "patch", "changes", "be", "rp", "damage", "toughness", "control", "mobility",
           "utility", "style", "difficulty", "herotype", "alttype"]
LISTS = ["role", "legacy", "client_positions", "external_positions"]


def _join(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = [value[k] for k in sorted(value) if isinstance(k, int)] or list(value.values())
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value)


def flatten(data: dict) -> pd.DataFrame:
    rows = []
    for name, champ in data.items():
        if not isinstance(champ, dict):
            continue
        row = {"champion": name}
        for k in SCALARS:
            v = champ.get(k)
            row[k] = _join(v) if isinstance(v, (list, dict)) else v
        for k in LISTS:
            row[k] = _join(champ.get(k))
        stats = champ.get("stats") or {}
        for k, v in stats.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                row[f"stat_{k}"] = v
        # o restante (habilidades, modificadores de modos, etc.) vai inteiro em JSON
        row["raw_json"] = json.dumps(champ, ensure_ascii=False, sort_keys=True, default=str)
        rows.append(row)
    return pd.DataFrame(rows)


def fetch_module(session: requests.Session) -> tuple[str | None, list[str]]:
    """Texto do módulo. Tenta a API do MediaWiki (o caminho indicado para robôs) e o modo raw."""
    failures = []
    attempts = [
        ("api.php", f"{WIKI}/api.php", {"action": "query", "prop": "revisions", "rvprop": "content",
                                         "rvslots": "main", "titles": PAGE, "format": "json",
                                         "formatversion": "2", "maxlag": "5"}),
        ("raw", f"{WIKI}/index.php", {"title": PAGE, "action": "raw"}),
    ]
    for label, url, params in attempts:
        try:
            resp = session.get(url, params=params, timeout=60)
            if resp.status_code != 200:
                snippet = " ".join(resp.text[:160].split())
                failures.append(f"{label}: HTTP {resp.status_code} ({snippet})")
                continue
            if label == "api.php":
                pages = resp.json().get("query", {}).get("pages", [])
                text = pages[0]["revisions"][0]["slots"]["main"]["content"]
            else:
                text = resp.text
            return text, failures
        except (requests.RequestException, ValueError, KeyError, IndexError) as e:
            failures.append(f"{label}: {type(e).__name__}: {e}")
    return None, failures


def collect(ctx: RunContext) -> SourceResult:
    result = SourceResult()
    text, failures = fetch_module(ctx.session)
    if text is None:
        result.error(f"{PAGE}: " + "; ".join(failures))
        return result
    try:
        data = loads(text)
    except LuaParseError as e:
        result.error(f"{PAGE}: não consegui ler o Lua ({e})")
        return result
    if not isinstance(data, dict) or not data:
        result.error("módulo veio vazio ou num formato inesperado")
        return result
    df = flatten(data)
    log.info("  LoL Wiki: %d campeões", len(df))
    write_with_history(df, "champion", SOURCE, "champions", result)
    return result
