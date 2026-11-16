"""Meraki Analytics (lolstaticdata): campeões e itens em JSON, mantidos pela comunidade.

São dois arquivos estáticos num CDN, uma requisição cada. O projeto pede que quem usa
guarde uma cópia em vez de consultar toda hora; é o que fazemos, uma vez por dia.

O que acrescenta: classes detalhadas (`roles`, ex.: JUGGERNAUT, BURST), posições,
notas de atributos (dano, resistência, controle, mobilidade, utilidade), tipo de dano
adaptativo, data/patch de lançamento e último patch alterado; para itens, preço, receita,
atributos e se ainda estão no jogo. O conteúdo completo de cada item vai em `raw_json`.
"""

from __future__ import annotations

import json

import pandas as pd
import requests

from ..context import RunContext
from .base import SourceResult, log
from .snapshots import write_with_history

SOURCE = "meraki"
BASE = "https://cdn.merakianalytics.com/riot/lol/resources/latest/en-US"
HEAVY_KEYS = {"skins", "lore", "icon", "image", "sprite"}  # grandes e irrelevantes para análise


def _scalar(v):
    return v if isinstance(v, (str, int, float, bool)) or v is None else json.dumps(v, ensure_ascii=False, default=str)


def _join(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return ",".join(str(x) for x in v)
    return str(v)


def _slim(obj: dict) -> str:
    return json.dumps({k: v for k, v in obj.items() if k not in HEAVY_KEYS}, ensure_ascii=False,
                      sort_keys=True, default=str)


def flatten_champions(data: dict) -> pd.DataFrame:
    rows = []
    for key, c in data.items():
        if not isinstance(c, dict):
            continue
        row = {"champion_key": key}
        for f in ("id", "name", "title", "fullName", "resource", "attackType", "adaptiveType",
                  "releaseDate", "releasePatch", "patchLastChanged"):
            row[f] = _scalar(c.get(f))
        row["positions"] = _join(c.get("positions"))
        row["roles"] = _join(c.get("roles"))
        price = c.get("price") or {}
        row["price_blue_essence"] = price.get("blueEssence")
        row["price_rp"] = price.get("rp")
        for k, v in (c.get("attributeRatings") or {}).items():
            row[f"rating_{k}"] = v
        for stat, values in (c.get("stats") or {}).items():
            if isinstance(values, dict):
                for part in ("flat", "perLevel", "percent", "percentPerLevel"):
                    if isinstance(values.get(part), (int, float)):
                        row[f"stat_{stat}_{part}"] = values[part]
        row["raw_json"] = _slim(c)
        rows.append(row)
    return pd.DataFrame(rows)


def flatten_items(data: dict) -> pd.DataFrame:
    rows = []
    for key, it in data.items():
        if not isinstance(it, dict):
            continue
        shop = it.get("shop") or {}
        prices = shop.get("prices") or {}
        row = {"item_id": str(it.get("id", key))}
        for f in ("name", "tier", "removed", "requiredChampion", "requiredAlly", "specialRecipe", "noEffects"):
            row[f] = _scalar(it.get(f))
        row["rank"] = _join(it.get("rank"))
        row["builds_from"] = _join(it.get("buildsFrom"))
        row["builds_into"] = _join(it.get("buildsInto"))
        row["price_total"] = prices.get("total")
        row["price_combined"] = prices.get("combined")
        row["price_sell"] = prices.get("sell")
        row["purchasable"] = shop.get("purchasable")
        row["tags"] = _join(shop.get("tags"))
        for stat, values in (it.get("stats") or {}).items():
            if isinstance(values, dict):
                for part in ("flat", "percent"):
                    if isinstance(values.get(part), (int, float)) and values[part]:
                        row[f"stat_{stat}_{part}"] = values[part]
        row["raw_json"] = _slim(it)
        rows.append(row)
    return pd.DataFrame(rows)


def collect(ctx: RunContext) -> SourceResult:
    result = SourceResult()
    for table, filename, flatten, key in (("champions", "champions.json", flatten_champions, "champion_key"),
                                          ("items", "items.json", flatten_items, "item_id")):
        try:
            resp = ctx.session.get(f"{BASE}/{filename}", timeout=120)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            result.error(f"{filename}: {type(e).__name__}: {e}")
            continue
        df = flatten(data)
        if df.empty:
            result.error(f"{filename}: nenhum registro (formato mudou?)")
            continue
        log.info("  Meraki %s: %d registros", table, len(df))
        write_with_history(df, key, SOURCE, table, result)
    return result
