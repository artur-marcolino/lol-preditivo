"""Leaguepedia (lol.fandom.com): tabelas Cargo da wiki competitiva.

A Cargo API funciona como um SQL de leitura sobre as tabelas da wiki
(`tables`, `fields`, `where`, `join_on`, `order_by`, `limit`, `offset`).

Decisões de projeto:
- Os campos de cada tabela são descobertos em tempo de execução (`action=cargofields`), então
  colunas novas criadas pela wiki entram automaticamente, sem mudar o código.
- Tabelas grandes são particionadas por ano (pela data do jogo); as pequenas são baixadas
  inteiras a cada execução.
- A paginação ordena por `_ID` (chave interna da Cargo) para que o `offset` seja estável.
- O acesso anônimo tem limite de requisições baixo. O coletor espera entre chamadas e, se
  receber `ratelimited`, faz uma pausa e passa a ir mais devagar dali em diante (ritmo
  adaptativo): pausas repetidas custam muito mais tempo do que um ritmo um pouco menor.
  O login com bot password da Fandom (LEAGUEPEDIA_USER / LEAGUEPEDIA_PASSWORD) é opcional.
- Coleta do dia a dia (depois da carga histórica): o ano corrente das tabelas com data
  própria é atualizado só a partir de 14 dias atrás e mesclado ao arquivo do ano; o ano
  inteiro é rebaixado às segundas-feiras, junto com as tabelas de referência sem data
  (times, jogadores, nomes antigos). Ligas e torneios sem data são atualizados todo dia.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Iterator

import pandas as pd
import requests

from ..context import RunContext, hot_years, now_utc
from ..http import RateLimiter
from ..storage import dataset_exists, dataset_key, read_dataset, write_dataset
from .base import SourceResult, log

SOURCE = "leaguepedia"
API = "https://lol.fandom.com/api.php"
FIRST_YEAR = 2011
PAGE_SIZE = 500


class CargoError(RuntimeError):
    pass


@dataclass
class TableSpec:
    table: str
    alias: str
    date_field: str | None = None  # campo (com alias) usado para particionar por ano
    joins: list[tuple[str, str]] = field(default_factory=list)  # [(tabela, alias)] extras
    join_on: str | None = None
    include: list[str] | None = None  # se definido, só estes campos (lista branca)

    @property
    def partitioned(self) -> bool:
        return self.date_field is not None


# Ordem pensada para coletar primeiro o que é pequeno e mais útil.
TABLES: list[TableSpec] = [
    TableSpec("Leagues", "L"),
    TableSpec("Tournaments", "T"),
    TableSpec("Teams", "TE"),
    TableSpec("TeamRedirects", "TR"),
    # Players: só campos esportivos (sem nome civil, redes sociais etc.)
    TableSpec("Players", "P", include=[
        "ID", "OverviewPage", "Country", "Nationality", "NationalityPrimary", "Residency",
        "Birthdate", "Role", "Team", "Team2", "IsRetired", "IsSubstitute", "IsTrainee",
        "IsLowercase", "Contract",
    ]),
    TableSpec("PlayerRedirects", "PR"),
    TableSpec("MatchSchedule", "MS", date_field="MS.DateTime_UTC"),
    TableSpec("ScoreboardGames", "SG", date_field="SG.DateTime_UTC"),
    TableSpec("PicksAndBansS7", "PB", date_field="SG.DateTime_UTC",
              joins=[("ScoreboardGames", "SG")], join_on="PB.GameId=SG.GameId"),
    TableSpec("TournamentPlayers", "TP", date_field="T.DateStart",
              joins=[("Tournaments", "T")], join_on="TP.OverviewPage=T.OverviewPage"),
    TableSpec("ScoreboardPlayers", "SP", date_field="SP.DateTime_UTC"),
    # Transferências (entrada/saída de jogadores, com data) e colocação final em cada torneio
    TableSpec("RosterChanges", "RC", date_field="RC.Date_Sort"),
    TableSpec("TournamentResults", "TRES", date_field="T.DateStart",
              joins=[("Tournaments", "T")], join_on="TRES.OverviewPage=T.OverviewPage"),
]


def normalize_row(title: dict) -> dict:
    """A API troca '_' por ' ' nos nomes das colunas; aqui voltamos ao nome original."""
    return {k.replace(" ", "_"): v for k, v in title.items()}


ANON_INTERVAL = 3.0  # sem login a wiki limita bastante; ritmo mais lento tropeça menos no limite
LOGGED_INTERVAL = 1.0
MAX_INTERVAL = 8.0  # teto do ritmo adaptativo (segundos entre requisições)
RECENT_DAYS = 14  # na coleta do dia a dia, o ano corrente é atualizado a partir de 14 dias atrás
FULL_REFRESH_WEEKDAY = 0  # segunda-feira: ano corrente inteiro e tabelas de referência
DAILY_REFERENCE = {"Leagues", "Tournaments"}  # tabelas sem data que mudam com frequência


def full_refresh_day() -> bool:
    return now_utc().weekday() == FULL_REFRESH_WEEKDAY


class Cargo:
    def __init__(self, session: requests.Session, min_interval: float = ANON_INTERVAL):
        self.session = session
        self.limiter = RateLimiter(min_interval)
        self.pauses = 0

    def call(self, params: dict, max_wait: float = 600) -> dict:
        params = {**params, "format": "json"}
        wait = 30.0
        while True:
            self.limiter.wait()
            resp = self.session.get(API, params=params, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            err = data.get("error")
            if not err:
                return data
            if err.get("code") in ("ratelimited", "maxlag") and wait <= max_wait:
                self.pauses += 1
                if err.get("code") == "ratelimited":  # dali em diante, mais devagar
                    self.limiter.min_interval = min(MAX_INTERVAL, self.limiter.min_interval * 1.5 + 0.5)
                log.info("  Leaguepedia pediu pausa (%s); aguardando %.0fs; ritmo: 1 requisição a cada %.1fs",
                         err.get("code"), wait, self.limiter.min_interval)
                time.sleep(wait)
                wait *= 2
                continue
            raise CargoError(f"{err.get('code')}: {err.get('info')}")

    def login(self, user: str, password: str) -> bool:
        """Login com bot password (Special:BotPasswords). Devolve True se deu certo."""
        token = self.call({"action": "query", "meta": "tokens", "type": "login"})["query"]["tokens"]["logintoken"]
        self.limiter.wait()
        resp = self.session.post(API, data={
            "action": "login", "lgname": user, "lgpassword": password, "lgtoken": token, "format": "json",
        }, timeout=60)
        result = resp.json().get("login", {})
        log.info("  login na Leaguepedia: %s", result.get("result"))
        if result.get("result") != "Success":
            raise CargoError(f"login recusado: {result.get('result')} {result.get('reason', '')}")
        self.limiter.min_interval = LOGGED_INTERVAL
        return True

    def fields(self, table: str) -> list[str]:
        data = self.call({"action": "cargofields", "table": table})
        return list(data.get("cargofields", {}).keys())

    def query(self, tables: str, fields: str, where: str | None = None, join_on: str | None = None,
              order_by: str | None = None) -> Iterator[list[dict]]:
        """Itera página a página (cada página é uma lista de linhas)."""
        offset = 0
        while True:
            params = {"action": "cargoquery", "tables": tables, "fields": fields,
                      "limit": PAGE_SIZE, "offset": offset}
            if where:
                params["where"] = where
            if join_on:
                params["join_on"] = join_on
            if order_by:
                params["order_by"] = order_by
            rows = [normalize_row(r["title"]) for r in self.call(params).get("cargoquery", [])]
            if rows:
                yield rows
            if len(rows) < PAGE_SIZE:
                return
            offset += PAGE_SIZE


PAGE_ALIAS = "wiki_page"  # a Cargo API não aceita apelidos começando com "_"
MAX_CONSECUTIVE_FAILURES = 6  # depois disso a fonte é abortada: o erro é sistemático, não pontual


def build_fields(spec: TableSpec, available: list[str]) -> str:
    names = [f for f in available if spec.include is None or f in spec.include]
    parts = [f"{spec.alias}.{name}={name}" for name in names]
    parts.append(f"{spec.alias}._pageName={PAGE_ALIAS}")
    return ",".join(parts)


def year_where(spec: TableSpec, year: int) -> str:
    return f"{spec.date_field} >= '{year}-01-01' AND {spec.date_field} < '{year + 1}-01-01'"


def fetch(cargo: Cargo, spec: TableSpec, fields: str, where: str | None, ctx: RunContext) -> pd.DataFrame | None:
    """Baixa todas as páginas de uma consulta. Devolve None se o orçamento acabar no meio."""
    tables = ",".join([f"{spec.table}={spec.alias}"] + [f"{t}={a}" for t, a in spec.joins])
    # Ordenação estável para paginar com offset; se a wiki recusar _ID, tenta alternativas
    orderings = [f"{spec.alias}._ID", f"{spec.alias}._pageName", None]
    for i, order_by in enumerate(orderings):
        rows: list[dict] = []
        try:
            for page in cargo.query(tables, fields, where=where, join_on=spec.join_on, order_by=order_by):
                rows.extend(page)
                if ctx.budget.expired():
                    return None  # partição pela metade não é gravada
            break
        except CargoError as e:
            # só vale tentar outra ordenação se o erro for sobre a ordenação
            about_order = order_by and (order_by.split(".")[-1] in str(e) or "order" in str(e).lower())
            if i == len(orderings) - 1 or rows or not about_order:
                raise
            log.info("  ordenação %s recusada (%s); tentando a próxima", order_by, e)
    columns = [part.split("=", 1)[1] for part in fields.split(",")]
    # Colunas fixas mesmo quando o ano não tem dados, para o esquema ficar consistente
    return pd.DataFrame(rows, columns=columns).drop_duplicates()


def _date_column(spec: TableSpec) -> str | None:
    if spec.date_field and spec.date_field.startswith(spec.alias + "."):
        return spec.date_field.split(".", 1)[1]
    return None


def collect(ctx: RunContext, only: list[str] | None = None) -> SourceResult:
    result = SourceResult()
    cargo = Cargo(ctx.session)
    user, password = os.environ.get("LEAGUEPEDIA_USER"), os.environ.get("LEAGUEPEDIA_PASSWORD")
    if user and password:
        try:
            cargo.login(user, password)
        except Exception as e:  # login é opcional: segue anônimo
            result.error(f"login falhou, seguindo anônimo ({e})")

    current = now_utc().year
    failures = 0  # falhas seguidas, em qualquer tabela

    def failed(msg: str) -> bool:
        """Registra a falha; devolve True se for hora de abortar a fonte."""
        nonlocal failures
        failures += 1
        result.error(msg)
        if failures >= MAX_CONSECUTIVE_FAILURES:
            result.error(f"abortado após {failures} falhas seguidas (erro sistemático; veja as mensagens acima)")
            return True
        return False

    for spec in TABLES:
        if only and spec.table not in only:
            continue
        if ctx.budget.expired():
            result.incomplete = True
            break
        try:
            available = cargo.fields(spec.table)
        except (requests.RequestException, CargoError) as e:
            if failed(f"{spec.table}: não consegui ler os campos ({e})"):
                return result
            continue
        if not available:
            result.error(f"{spec.table}: tabela sem campos (nome mudou?)")
            continue
        fields = build_fields(spec, available)

        partitioned = spec.partitioned
        own_date = _date_column(spec)
        if partitioned and own_date and own_date not in available:
            # o campo de data esperado não existe (a wiki mudou o esquema): baixa a tabela inteira
            result.error(f"{spec.table}: campo {own_date} não existe; coletando sem partição por ano")
            partitioned = False
        partitions = [str(y) for y in range(FIRST_YEAR, current + 1)] if partitioned else ["all"]
        for part in partitions:
            key = dataset_key(SOURCE, spec.table, part)
            exists = dataset_exists(SOURCE, spec.table, part)
            if part != "all" and not ctx.should_refresh(int(part), exists):
                result.skipped.append(key)
                continue
            if part == "all" and exists and ctx.backfill_only and not ctx.full:
                result.skipped.append(key)
                continue
            if (part == "all" and exists and not ctx.full and spec.table not in DAILY_REFERENCE
                    and not full_refresh_day()):
                result.skipped.append(key)  # tabela de referência: atualizada às segundas
                continue
            # coleta do dia a dia: só as últimas semanas do ano corrente, mescladas ao arquivo
            incremental = bool(part != "all" and exists and own_date and partitioned and not ctx.full
                           and ctx.years is None and int(part) in hot_years() and not full_refresh_day())
            if ctx.budget.expired():
                result.incomplete = True
                break
            log.info("Leaguepedia %s [%s]%s", spec.table, part, " (últimas semanas)" if incremental else "")
            try:
                where = year_where(spec, int(part)) if part != "all" else None
                if incremental:
                    since = recent_since(int(part))
                    where = f"{spec.date_field} >= '{since}' AND {spec.date_field} < '{int(part) + 1}-01-01'"
                fetch_spec = spec if part != "all" or not spec.joins else TableSpec(spec.table, spec.alias, include=spec.include)
                df = fetch(cargo, fetch_spec, fields, where, ctx)
            except (requests.RequestException, CargoError) as e:
                if failed(f"{spec.table} [{part}]: {e}"):
                    return result
                continue
            if df is None:
                result.incomplete = True
                break
            failures = 0
            if incremental:
                df = merge_recent(read_dataset(SOURCE, spec.table, part), df, own_date, recent_since(int(part)))
            changed = write_dataset(df, SOURCE, spec.table, part, date_col=own_date)
            result.record(key, changed)
        if result.incomplete:
            break
    result.stats["rate_limit_pauses"] = cargo.pauses
    result.stats["final_interval_s"] = round(cargo.limiter.min_interval, 2)
    return result


def recent_since(year: int) -> str:
    """Início da janela da coleta do dia a dia, sem sair do ano da partição."""
    since = (now_utc() - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")
    return max(since, f"{year}-01-01")


def merge_recent(old: pd.DataFrame | None, recent: pd.DataFrame, date_col: str, since: str) -> pd.DataFrame:
    """Troca, no arquivo do ano, as linhas a partir de `since` pelas recém-baixadas."""
    if old is None or old.empty or date_col not in old.columns:
        return recent
    keep = old[~(old[date_col].astype("string").fillna("") >= since)]
    merged = pd.concat([keep, recent], ignore_index=True)
    return merged.drop_duplicates()
