"""Esports Earnings: premiação por torneio, por time e por jogador.

API oficial do site esportsearnings.com (chave gratuita, criada na "Development Area" do
site). Fonte opcional: só roda com o segredo `ESPORTSEARNINGS_KEY`.

O que coletamos de LoL (GameId 164, cerca de 3 mil torneios desde 2010):
- `tournaments`: torneios de LoL (datas, local, premiação total, se é por time);
- `team_results`: colocação e prêmio de cada time em cada torneio;
- `team_players`: quem jogou por qual time em cada torneio;
- `player_results`: colocação e prêmio em torneios individuais.

Com a data de fim do torneio, isso vira variáveis pré-partida como "prêmio acumulado do
jogador/time até a data" ou "quantas finais grandes o elenco já disputou".

Ritmo: a API pede no máximo 1 requisição por segundo. A primeira carga leva algumas horas
(uma ou duas requisições por torneio) e continua sozinha em execuções encadeadas.
"""

from __future__ import annotations

import os
import time
from datetime import timedelta

import pandas as pd
import requests

from ..context import RunContext, now_utc
from ..http import RateLimiter
from ..storage import dataset_key, read_dataset, write_dataset
from .base import SourceResult, log

SOURCE = "esports_earnings"
BASE = "https://api.esportsearnings.com/v0"
LOL_GAME_ID = 164
CHECKPOINT = 150  # torneios por gravação intermediária
REFRESH_DAYS = 45  # torneios que terminaram há menos disso têm o resultado recoletado


class EEError(RuntimeError):
    pass


class Api:
    def __init__(self, session: requests.Session, key: str):
        self.session = session
        self.key = key
        self.limiter = RateLimiter(1.1)
        self.calls = 0

    def get(self, method: str, **params) -> list | dict:
        for attempt in range(4):
            self.limiter.wait()
            self.calls += 1
            resp = self.session.get(f"{BASE}/{method}", params={**params, "apikey": self.key, "format": "json"},
                                    timeout=60)
            resp.raise_for_status()
            text = resp.text.strip()
            if not text:
                return []  # sem resultados
            data = resp.json()
            code = data.get("ErrorCode") if isinstance(data, dict) else None
            if code is None:
                return data
            if int(code) in (1002, 1011) and attempt < 3:  # banco ocupado / rápido demais
                time.sleep(10 * (attempt + 1))
                continue
            raise EEError(f"{method}: ErrorCode {code}")
        raise EEError(f"{method}: muitas tentativas")


def _rows(data) -> list[dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def collect_tournaments(api: Api, ctx: RunContext, old: pd.DataFrame | None) -> pd.DataFrame:
    """Percorre os torneios mais recentes (todos os jogos, 100 por página) e guarda os de LoL.

    Na carga inicial vai até o fim da lista; depois, para um mês antes do torneio mais
    recente já guardado.
    """
    stop_before = None
    if old is not None and not old.empty and not ctx.full:
        stop_before = (pd.to_datetime(old["EndDate"], errors="coerce").max() - timedelta(days=30)).strftime("%Y-%m-%d")
    found, offset = [], 0
    while not ctx.budget.expired():
        page = _rows(api.get("LookupRecentTournaments", offset=offset))
        if not page:
            break
        found += [r for r in page if str(r.get("GameId")) == str(LOL_GAME_ID)]
        oldest = min((str(r.get("EndDate") or "") for r in page), default="")
        if stop_before and oldest and oldest < stop_before:
            break
        offset += 100
    new = pd.DataFrame(found)
    if new.empty:
        return old if old is not None else new
    merged = new if old is None else pd.concat([new, old], ignore_index=True)
    return merged.drop_duplicates("TournamentId", keep="first")


def collect(ctx: RunContext) -> SourceResult:
    result = SourceResult()
    key = os.environ.get("ESPORTSEARNINGS_KEY")
    if not key:
        result.error("ESPORTSEARNINGS_KEY não configurado (crie a chave em esportsearnings.com/dev)")
        return result
    api = Api(ctx.session, key)

    try:
        tournaments = collect_tournaments(api, ctx, read_dataset(SOURCE, "tournaments"))
    except (requests.RequestException, EEError, ValueError) as e:
        result.error(f"lista de torneios: {e}")
        return result
    if tournaments is None or tournaments.empty:
        result.error("nenhum torneio de LoL encontrado")
        return result
    result.record(dataset_key(SOURCE, "tournaments"),
                  write_dataset(tournaments, SOURCE, "tournaments", date_col="EndDate"))

    tables = {t: read_dataset(SOURCE, t) for t in ("team_results", "team_players", "player_results", "fetched")}
    # `fetched` registra os torneios já consultados (inclusive os que não tinham resultado)
    done = set() if tables["fetched"] is None else set(tables["fetched"]["TournamentId"].astype(str))
    recent = (now_utc() - timedelta(days=REFRESH_DAYS)).strftime("%Y-%m-%d")
    todo = tournaments[~tournaments["TournamentId"].astype(str).isin(done)
                       | (tournaments["EndDate"].astype(str) >= recent)]
    todo = todo.sort_values("EndDate", ascending=False)
    log.info("  Esports Earnings: %d torneios de LoL, %d a coletar resultados", len(tournaments), len(todo))

    new = {t: [] for t in tables}
    fetched: set[str] = set()

    def flush():
        for t, rows in new.items():
            if not rows:
                continue
            df = pd.DataFrame(rows)
            old = tables[t]
            if old is not None and not old.empty:
                old = old[~old["TournamentId"].astype(str).isin(fetched)]
                df = pd.concat([old, df], ignore_index=True)
            tables[t] = df
            result.record(dataset_key(SOURCE, t), write_dataset(df, SOURCE, t))
            new[t] = []
        fetched.clear()  # já gravados; na próxima gravação só se substituem os torneios novos

    for n, (_, tour) in enumerate(todo.iterrows(), start=1):
        if ctx.budget.expired():
            result.incomplete = True
            break
        tid = str(tour["TournamentId"])
        try:
            if str(tour.get("Teamplay")) in ("1", "True", "true"):
                for r in _rows(api.get("LookupTournamentTeamResultsByTournamentId", tournamentid=tid)):
                    new["team_results"].append({"TournamentId": tid, **r})
                for r in _rows(api.get("LookupTournamentTeamPlayersByTournamentId", tournamentid=tid)):
                    new["team_players"].append({"TournamentId": tid, **r})
            else:
                for r in _rows(api.get("LookupTournamentResultsByTournamentId", tournamentid=tid)):
                    new["player_results"].append({"TournamentId": tid, **r})
            fetched.add(tid)
            new["fetched"].append({"TournamentId": tid, "fetched_at": now_utc().isoformat(timespec="seconds")})
        except EEError as e:
            if "1010" in str(e) or "1012" in str(e):  # chave inválida ou restrita: não adianta seguir
                result.error(f"chave recusada: {e}")
                break
            result.error(f"torneio {tid}: {e}")
        except requests.RequestException as e:
            result.error(f"torneio {tid}: {e}")
        if n % CHECKPOINT == 0:
            flush()
    flush()
    result.stats["api_calls"] = api.calls
    return result
