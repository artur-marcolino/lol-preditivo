"""Data Dragon (Riot): atributos estáticos de campeões e itens, por patch.

Serve para criar variáveis de composição do draft (classe do campeão, tipo de recurso,
atributos base, notas de ataque/defesa/magia/dificuldade) no patch em que o jogo aconteceu.

Guarda uma versão por patch (a mais recente de cada `X.Y`). Coleta incremental: só busca
os patches que ainda não estão no arquivo.
"""

from __future__ import annotations

import re

import pandas as pd
import requests

from ..context import RunContext
from ..http import RateLimiter
from ..storage import dataset_key, read_dataset, write_dataset
from .base import SourceResult, log

SOURCE = "ddragon"
BASE = "https://ddragon.leagueoflegends.com"
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
MIN_MAJOR = 4  # patch 4.1 = janeiro de 2014, início dos dados do Oracle's Elixir


def _join(values) -> str:
    return ",".join(str(v) for v in values or [])


def latest_per_patch(versions: list[str]) -> dict[str, str]:
    """{'14.10': '14.10.1', ...}: a maior versão de cada patch."""
    best: dict[str, tuple[int, ...]] = {}
    for v in versions:
        m = VERSION_RE.match(v)
        if not m:
            continue  # ignora entradas antigas como 'lolpatch_7.20'
        nums = tuple(int(x) for x in m.groups())
        if nums[0] < MIN_MAJOR:
            continue
        patch = f"{nums[0]}.{nums[1]}"
        if patch not in best or nums > best[patch]:
            best[patch] = nums
    return {p: ".".join(map(str, n)) for p, n in best.items()}


def flatten_champions(patch: str, version: str, data: dict) -> list[dict]:
    rows = []
    for champ in data.get("data", {}).values():
        info, stats = champ.get("info") or {}, champ.get("stats") or {}
        tags = champ.get("tags") or []
        rows.append({
            "patch": patch, "version": version, "champion_id": champ.get("id"), "key": champ.get("key"),
            "name": champ.get("name"), "title": champ.get("title"), "partype": champ.get("partype"),
            "tags": _join(tags), "tag_primary": tags[0] if tags else None,
            "tag_secondary": tags[1] if len(tags) > 1 else None,
            **{f"info_{k}": v for k, v in info.items()},
            **{f"stat_{k}": v for k, v in stats.items()},
        })
    return rows


def flatten_items(patch: str, version: str, data: dict) -> list[dict]:
    rows = []
    for item_id, item in data.get("data", {}).items():
        gold = item.get("gold") or {}
        rows.append({
            "patch": patch, "version": version, "item_id": item_id, "name": item.get("name"),
            "plaintext": item.get("plaintext"), "tags": _join(item.get("tags")),
            "gold_total": gold.get("total"), "gold_base": gold.get("base"), "gold_sell": gold.get("sell"),
            "purchasable": gold.get("purchasable"), "depth": item.get("depth"),
            "into": _join(item.get("into")), "from": _join(item.get("from")),
            **{f"stat_{k}": v for k, v in (item.get("stats") or {}).items()},
        })
    return rows


def collect(ctx: RunContext) -> SourceResult:
    result = SourceResult()
    limiter = RateLimiter(0.2)
    try:
        versions = ctx.session.get(f"{BASE}/api/versions.json", timeout=60).json()
    except (requests.RequestException, ValueError) as e:
        result.error(f"versions.json: {e}")
        return result
    patches = latest_per_patch(versions)

    for table, filename, flatten in (("champions", "champion.json", flatten_champions),
                                     ("items", "item.json", flatten_items)):
        old = read_dataset(SOURCE, table)
        have = set(old["version"]) if old is not None and not ctx.full else set()
        todo = {p: v for p, v in patches.items() if v not in have}
        log.info("Data Dragon %s: %d patches novos", table, len(todo))
        rows: list[dict] = []
        for patch, version in sorted(todo.items(), key=lambda kv: tuple(map(int, kv[0].split(".")))):
            if ctx.budget.expired():
                result.incomplete = True
                break
            limiter.wait()
            try:
                resp = ctx.session.get(f"{BASE}/cdn/{version}/data/en_US/{filename}", timeout=60)
                if resp.status_code == 403 or resp.status_code == 404:
                    continue  # versões muito antigas não têm todos os arquivos
                resp.raise_for_status()
                rows += flatten(patch, version, resp.json())
            except Exception as e:  # formato inesperado numa versão antiga não derruba as demais
                result.error(f"{table} {version}: {type(e).__name__}: {e}")
        if not rows:
            result.unchanged.append(dataset_key(SOURCE, table))
            continue
        new = pd.DataFrame(rows)
        merged = new if old is None or ctx.full else pd.concat([old, new], ignore_index=True)
        try:
            result.record(dataset_key(SOURCE, table), write_dataset(merged, SOURCE, table))
        except Exception as e:
            result.error(f"{table}: falha ao gravar ({type(e).__name__}: {e})")
    return result
