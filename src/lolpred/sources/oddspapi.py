"""OddsPapi (opcional; precisa de conta gratuita e do segredo ODDSPAPI_KEY).

Odds de casas de aposta servem aqui como **régua**: a probabilidade implícita na odd de
fechamento é um "modelo" que já incorpora muita informação, e comparar o nosso modelo com
ela mostra se ele acrescenta alguma coisa.

O plano gratuito permite 250 requisições por mês e inclui o histórico de odds de cada
partida (cada chamada traz a série de preços ao longo do tempo, até 3 casas de aposta).
Por isso a coleta é econômica e tem trava de orçamento:
- 1 chamada por execução lista as partidas de LoL numa janela de ~10 dias;
- o restante do orçamento diário vai para o histórico de odds de partidas já encerradas,
  priorizando ligas principais e internacionais;
- nunca passa de ODDSPAPI_MONTHLY_BUDGET (padrão 240) chamadas no mês, contadas a partir
  do próprio registro de chamadas.

As respostas são guardadas brutas (`raw_json`): a interpretação dos mercados e preços fica
para o banco analítico, depois que virmos os dados reais.
"""

from __future__ import annotations

import json
import os
import re

import pandas as pd
import requests

from ..context import RunContext, now_utc
from ..http import RateLimiter
from ..storage import dataset_key, read_dataset, write_dataset
from .base import SourceResult, log

SOURCE = "oddspapi"
BASE = "https://api.oddspapi.io/v4"
SPORT_ID = 18  # ESport League of Legends
MAJOR = re.compile(r"\b(?:LCK|LPL|LEC|LCS|LTA|LCP|CBLOL|PCS|VCS|LJL|World|MSI|First Stand|EWC|Esports World Cup)\b", re.I)


def _budget() -> tuple[int, int]:
    return (int(os.environ.get("ODDSPAPI_MONTHLY_BUDGET", "240")), int(os.environ.get("ODDSPAPI_PER_RUN", "8")))


class Api:
    def __init__(self, session: requests.Session, key: str, used_this_month: int):
        self.session = session
        self.key = key
        self.limiter = RateLimiter(1.0)
        self.monthly, self.per_run = _budget()
        self.used_month = used_this_month
        self.used_run = 0
        self.calls: list[dict] = []

    def can_call(self) -> bool:
        return self.used_run < self.per_run and self.used_month < self.monthly

    def get(self, path: str, **params):
        if not self.can_call():
            raise RuntimeError("orçamento de requisições do OddsPapi esgotado")
        self.limiter.wait()
        self.used_run += 1
        self.used_month += 1
        resp = self.session.get(f"{BASE}{path}", params={**params, "apiKey": self.key}, timeout=60)
        self.calls.append({"called_at": now_utc().isoformat(timespec="seconds"), "endpoint": path,
                           "params": json.dumps(params, sort_keys=True), "status_code": resp.status_code,
                           "raw_json": resp.text if resp.status_code == 200 else resp.text[:2000]})
        resp.raise_for_status()
        return resp.json()


def _list(payload) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("data", "fixtures", "results"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def flatten_fixture(f: dict) -> dict:
    keys = ("fixtureId", "startTime", "statusName", "statusId", "participant1Name", "participant2Name",
            "participant1Id", "participant2Id", "tournamentId", "tournamentName", "tournamentSlug",
            "categoryName", "seasonId")
    row = {k: f.get(k) for k in keys}
    row["raw_json"] = json.dumps(f, ensure_ascii=False, sort_keys=True, default=str)
    return row


def collect(ctx: RunContext) -> SourceResult:
    result = SourceResult()
    key = os.environ.get("ODDSPAPI_KEY")
    if not key:
        result.error("ODDSPAPI_KEY não configurado (crie uma conta gratuita em oddspapi.io)")
        return result

    month = now_utc().strftime("%Y-%m")
    calls_old = read_dataset(SOURCE, "calls", month)
    used = 0 if calls_old is None else len(calls_old)
    api = Api(ctx.session, key, used)
    log.info("  OddsPapi: %d chamadas já usadas em %s (limite %d)", used, month, api.monthly)

    # 1) partidas numa janela em torno de hoje (uma chamada só, para a modalidade inteira)
    fixtures = read_dataset(SOURCE, "fixtures")
    now = pd.Timestamp(now_utc())
    try:
        payload = api.get("/fixtures", sportId=SPORT_ID,
                          **{"from": (now - pd.Timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                             "to": (now + pd.Timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")})
        rows = [flatten_fixture(f) for f in _list(payload) if isinstance(f, dict)]
        if rows:
            new = pd.DataFrame(rows)
            fixtures = new if fixtures is None else pd.concat([fixtures, new]).drop_duplicates("fixtureId", keep="last")
            result.record(dataset_key(SOURCE, "fixtures"),
                          write_dataset(fixtures, SOURCE, "fixtures", date_col="startTime"))
        else:
            result.error(f"/fixtures: resposta sem partidas reconhecíveis ({str(payload)[:200]})")
    except (requests.RequestException, RuntimeError, ValueError) as e:
        result.error(f"/fixtures: {e}")

    # 2) histórico de odds de partidas encerradas, priorizando ligas principais
    hist = read_dataset(SOURCE, "historical_odds_raw")
    done = set(hist["fixtureId"].astype(str)) if hist is not None else set()
    new_hist: list[dict] = []
    if fixtures is not None and not fixtures.empty:
        f = fixtures.copy()
        f["start"] = pd.to_datetime(f["startTime"], utc=True, errors="coerce")
        f = f[(f["start"] < now - pd.Timedelta(hours=6)) & ~f["fixtureId"].astype(str).isin(done)]
        label = f["tournamentName"].fillna("").astype(str) + " " + f["raw_json"].str.slice(0, 400)
        f["major"] = label.str.contains(MAJOR)
        f = f.sort_values(["major", "start"], ascending=[False, False])
        bookmakers = os.environ.get("ODDSPAPI_BOOKMAKERS", "pinnacle,bet365,ggbet")
        for fid in f["fixtureId"].astype(str):
            if not api.can_call() or ctx.budget.expired():
                break
            try:
                data = api.get("/historical-odds", fixtureId=fid, bookmakers=bookmakers)
                new_hist.append({"fixtureId": fid, "fetched_at": now_utc().isoformat(timespec="seconds"),
                                 "bookmakers": bookmakers, "raw_json": json.dumps(data, ensure_ascii=False)})
            except (requests.RequestException, ValueError) as e:
                result.error(f"/historical-odds {fid}: {e}")
    if new_hist:
        merged = pd.DataFrame(new_hist) if hist is None else pd.concat([hist, pd.DataFrame(new_hist)], ignore_index=True)
        result.record(dataset_key(SOURCE, "historical_odds_raw"),
                      write_dataset(merged, SOURCE, "historical_odds_raw", date_col="fetched_at"))

    # 3) registro de todas as chamadas do mês (é dele que sai a conta do orçamento)
    if api.calls:
        calls = pd.DataFrame(api.calls) if calls_old is None else pd.concat([calls_old, pd.DataFrame(api.calls)],
                                                                            ignore_index=True)
        result.record(dataset_key(SOURCE, "calls", month), write_dataset(calls, SOURCE, "calls", month))
    log.info("  OddsPapi: %d chamadas nesta execução", api.used_run)
    return result
