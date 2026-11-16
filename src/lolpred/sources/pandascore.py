"""PandaScore (opcional; precisa de conta gratuita e do segredo PANDASCORE_TOKEN).

O plano gratuito ("Fixtures") dá calendário, resultados e dados de contexto, com limite de
1.000 requisições por hora. O que acrescenta às outras fontes:
- `tier` de cada torneio (S, A, B, C, D), uma classificação de nível independente da nossa;
- jogadores com nacionalidade, data de nascimento e time atual;
- uma segunda base de resultados e de partidas futuras, para conferência.

Os termos de uso proíbem usar os dados para apostas; para análise e modelagem, tudo bem.

Coleta:
- partidas passadas: na primeira vez, todo o histórico (de trás para frente, retomando de
  onde parou se o orçamento de tempo acabar); depois, só as novas;
- partidas futuras: sempre inteiras (são poucas);
- ligas, temporadas, torneios, times e jogadores: inteiros quando não existem ou às segundas.
Cada tabela guarda as colunas principais e o objeto completo em `raw_json`.
"""

from __future__ import annotations

import json
import os
import time

import pandas as pd
import requests

from ..context import RunContext, now_utc
from ..http import RateLimiter
from ..storage import dataset_key, read_dataset, write_dataset
from .base import SourceResult, log

SOURCE = "pandascore"
BASE = "https://api.pandascore.co"
PAGE = 100
HEAVY = {"streams_list", "videogame", "videogame_version", "videogame_title", "live", "image_url"}


class Api:
    def __init__(self, session: requests.Session, token: str):
        self.session = session
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self.limiter = RateLimiter(3.7)  # ~970 por hora, abaixo do limite de 1.000

    def get(self, path: str, **params) -> list:
        self.limiter.wait()
        resp = self.session.get(f"{BASE}{path}", params=params, headers=self.headers, timeout=60)
        remaining = resp.headers.get("X-Rate-Limit-Remaining") if hasattr(resp, "headers") else None
        if remaining is not None and remaining.isdigit() and int(remaining) < 20:
            log.info("  PandaScore: restam %s requisições na hora; pausando 5 min", remaining)
            time.sleep(300)
        resp.raise_for_status()
        return resp.json()

    def pages(self, path: str, **params):
        page = 1
        while True:
            items = self.get(path, **{**params, "page[size]": PAGE, "page[number]": page})
            if not items:
                return
            yield items
            if len(items) < PAGE:
                return
            page += 1


def _raw(obj: dict) -> str:
    return json.dumps({k: v for k, v in obj.items() if k not in HEAVY}, ensure_ascii=False, sort_keys=True, default=str)


def _id(obj) -> object:
    return (obj or {}).get("id") if isinstance(obj, dict) else None


def flatten_match(m: dict) -> dict:
    opponents = [(o or {}).get("opponent") or {} for o in m.get("opponents") or []]
    opponents = (opponents + [{}, {}])[:2]
    scores = {r.get("team_id"): r.get("score") for r in m.get("results") or []}
    row = {k: m.get(k) for k in ("id", "name", "status", "match_type", "number_of_games", "begin_at", "end_at",
                                 "scheduled_at", "original_scheduled_at", "rescheduled", "forfeit", "draw",
                                 "winner_id", "league_id", "serie_id", "tournament_id", "modified_at")}
    for i, o in enumerate(opponents, start=1):
        row.update({f"team{i}_id": o.get("id"), f"team{i}_name": o.get("name"),
                    f"team{i}_acronym": o.get("acronym"), f"team{i}_score": scores.get(o.get("id"))})
    row["raw_json"] = _raw(m)
    return row


def flatten_simple(obj: dict, fields: tuple[str, ...]) -> dict:
    row = {f: obj.get(f) for f in fields}
    row["raw_json"] = _raw(obj)
    return row


DIMENSIONS = {
    "leagues": ("/lol/leagues", ("id", "name", "slug", "modified_at")),
    "series": ("/lol/series", ("id", "league_id", "name", "full_name", "season", "year", "begin_at", "end_at",
                               "winner_id", "modified_at")),
    "tournaments": ("/lol/tournaments", ("id", "name", "slug", "league_id", "serie_id", "tier", "type", "region",
                                         "country", "begin_at", "end_at", "winner_id", "prizepool",
                                         "has_bracket", "modified_at")),
    "teams": ("/lol/teams", ("id", "name", "acronym", "slug", "location", "modified_at")),
    "players": ("/lol/players", ("id", "name", "first_name", "last_name", "nationality", "role", "birthday",
                                 "age", "modified_at")),
}


def _merge(old: pd.DataFrame | None, new_rows: list[dict], key: str = "id") -> pd.DataFrame:
    new = pd.DataFrame(new_rows)
    if old is None or old.empty:
        return new.drop_duplicates(key, keep="last")
    return pd.concat([old, new], ignore_index=True).drop_duplicates(key, keep="last")


def collect(ctx: RunContext) -> SourceResult:
    result = SourceResult()
    token = os.environ.get("PANDASCORE_TOKEN")
    if not token:
        result.error("PANDASCORE_TOKEN não configurado (crie uma conta gratuita em pandascore.co)")
        return result
    api = Api(ctx.session, token)
    monday = now_utc().weekday() == 0

    # ---------------------------------------------------------------- dimensões
    for table, (path, fields) in DIMENSIONS.items():
        if ctx.budget.expired():
            result.incomplete = True
            return result
        if read_dataset(SOURCE, table) is not None and not (monday or ctx.full):
            result.skipped.append(dataset_key(SOURCE, table))
            continue
        rows, extra = [], []
        try:
            for items in api.pages(path, sort="id"):
                for obj in items:
                    row = flatten_simple(obj, fields)
                    if table == "players":
                        row["current_team_id"] = _id(obj.get("current_team"))
                    if table == "teams":
                        extra += [{"team_id": obj.get("id"), "player_id": p.get("id"), "player_name": p.get("name"),
                                   "role": p.get("role")} for p in obj.get("players") or []]
                    rows.append(row)
                if ctx.budget.expired():
                    break
        except requests.RequestException as e:
            result.error(f"{path}: {e}")
            continue
        if ctx.budget.expired():
            result.incomplete = True  # tabela inteira pela metade não substitui a anterior
            return result
        result.record(dataset_key(SOURCE, table), write_dataset(pd.DataFrame(rows), SOURCE, table))
        if table == "teams" and extra:
            result.record(dataset_key(SOURCE, "team_players"),
                          write_dataset(pd.DataFrame(extra), SOURCE, "team_players"))

    # ---------------------------------------------------------------- partidas futuras
    try:
        upcoming = [flatten_match(m) for items in api.pages("/lol/matches/upcoming", sort="begin_at") for m in items]
        result.record(dataset_key(SOURCE, "matches_upcoming"),
                      write_dataset(pd.DataFrame(upcoming), SOURCE, "matches_upcoming", date_col="scheduled_at"))
    except requests.RequestException as e:
        result.error(f"/lol/matches/upcoming: {e}")

    # ---------------------------------------------------------------- partidas passadas
    past = read_dataset(SOURCE, "matches_past")
    state = read_dataset(SOURCE, "state")
    backfill_done = state is not None and bool((state["key"] == "past_backfill_done").any())
    rows: list[dict] = []

    def save() -> None:
        nonlocal past, rows
        if rows:
            past = _merge(past, rows)
            rows = []
            result.record(dataset_key(SOURCE, "matches_past"),
                          write_dataset(past, SOURCE, "matches_past", date_col="begin_at"))

    try:
        # 1) novidades: do mais recente para trás até encostar no que já temos
        newest = pd.to_datetime(past["begin_at"], utc=True, errors="coerce").max() if past is not None else None
        stop = newest - pd.Timedelta(days=3) if newest is not None and pd.notna(newest) else None
        for items in api.pages("/lol/matches/past", sort="-begin_at"):
            rows += [flatten_match(m) for m in items]
            oldest_here = pd.to_datetime(items[-1].get("begin_at"), utc=True, errors="coerce")
            if stop is not None and pd.notna(oldest_here) and oldest_here < stop:
                break
            if ctx.budget.expired():
                break
        save()
        # 2) carga histórica: continua de onde parou, indo para o passado
        if not backfill_done and not ctx.budget.expired() and past is not None:
            oldest = pd.to_datetime(past["begin_at"], utc=True, errors="coerce").min()
            if pd.notna(oldest):
                window = f"2010-01-01T00:00:00Z,{oldest.strftime('%Y-%m-%dT%H:%M:%SZ')}"
                finished = True
                for n, items in enumerate(api.pages("/lol/matches/past", sort="-begin_at",
                                                    **{"range[begin_at]": window}), start=1):
                    rows += [flatten_match(m) for m in items]
                    if n % 20 == 0:
                        save()
                    if ctx.budget.expired():
                        finished = False
                        result.incomplete = True
                        break
                save()
                if finished:
                    write_dataset(pd.DataFrame([{"key": "past_backfill_done", "value": now_utc().isoformat()}]),
                                  SOURCE, "state")
    except requests.RequestException as e:
        save()
        result.error(f"/lol/matches/past: {e}")
    return result
