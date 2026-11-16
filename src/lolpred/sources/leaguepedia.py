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
  receber `ratelimited`, aguarda cada vez mais. Com um bot password da Fandom
  (LEAGUEPEDIA_USER / LEAGUEPEDIA_PASSWORD) os limites ficam bem maiores.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Iterator

import pandas as pd
import requests

from ..context import RunContext, now_utc
from ..http import RateLimiter
from ..storage import dataset_exists, dataset_key, write_dataset
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
]


def normalize_row(title: dict) -> dict:
    """A API troca '_' por ' ' nos nomes das colunas; aqui voltamos ao nome original."""
    return {k.replace(" ", "_"): v for k, v in title.items()}


class Cargo:
    def __init__(self, session: requests.Session, min_interval: float = 1.5):
        self.session = session
        self.limiter = RateLimiter(min_interval)

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
                log.info("  Leaguepedia pediu pausa (%s); aguardando %.0fs", err.get("code"), wait)
                time.sleep(wait)
                wait *= 2
                continue
            raise CargoError(f"{err.get('code')}: {err.get('info')}")

    def login(self, user: str, password: str) -> None:
        token = self.call({"action": "query", "meta": "tokens", "type": "login"})["query"]["tokens"]["logintoken"]
        self.limiter.wait()
        resp = self.session.post(API, data={
            "action": "login", "lgname": user, "lgpassword": password, "lgtoken": token, "format": "json",
        }, timeout=60)
        result = resp.json().get("login", {}).get("result")
        log.info("  login na Leaguepedia: %s", result)

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

        partitions = [str(y) for y in range(FIRST_YEAR, current + 1)] if spec.partitioned else ["all"]
        for part in partitions:
            key = dataset_key(SOURCE, spec.table, part)
            exists = dataset_exists(SOURCE, spec.table, part)
            if part != "all" and not ctx.should_refresh(int(part), exists):
                result.skipped.append(key)
                continue
            if ctx.budget.expired():
                result.incomplete = True
                break
            log.info("Leaguepedia %s [%s]", spec.table, part)
            try:
                df = fetch(cargo, spec, fields, year_where(spec, int(part)) if part != "all" else None, ctx)
            except (requests.RequestException, CargoError) as e:
                if failed(f"{spec.table} [{part}]: {e}"):
                    return result
                continue
            if df is None:
                result.incomplete = True
                break
            failures = 0
            changed = write_dataset(df, SOURCE, spec.table, part, date_col=_date_column(spec))
            result.record(key, changed)
        if result.incomplete:
            break
    return result
