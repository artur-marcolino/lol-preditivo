"""Arquivo histórico do Global Contract Database na Leaguepedia.

A Leaguepedia guarda uma cópia diária do GCD da Riot por região, desde 2018, em páginas
`Archive:Global_Contract_Database/<região>/<AAAA-MM-DD>` (cerca de 33 mil páginas em
nov/2026). Cada página é uma tabela wiki com liga, time, nick, fim de contrato, status...

É o que permite responder, sem vazamento, perguntas como "quanto tempo de contrato o
jogador ainda tinha no dia da série?" ou "o time trocou alguém nas últimas semanas?".

Coleta:
- lista as páginas do arquivo (list=allpages) e baixa só as que ainda não foram lidas,
  50 por requisição (o conteúdo vem em wikitexto);
- grava `snapshots/<ano>`: uma linha por contrato por dia de cópia (com muita repetição,
  mas o Parquet comprime bem e assim dá para reconstruir qualquer data);
- grava `pages`: as páginas já lidas e quantas linhas cada uma rendeu.

Usa o mesmo login opcional (bot password) da fonte `leaguepedia`. Nomes civis e
contatos que aparecem nas tabelas não são guardados.
"""

from __future__ import annotations

import os
import re

import pandas as pd

from ..context import RunContext, now_utc
from ..storage import dataset_key, read_dataset, write_dataset
from .base import SourceResult, log
from .leaguepedia import Cargo, CargoError
from .riot_gcd import canonical_header

SOURCE = "leaguepedia_gcd"
PREFIX = "Global_Contract_Database/"
BATCH = 50  # títulos por requisição de conteúdo (limite da API para contas comuns)
CHECKPOINT_PAGES = 1500  # grava o progresso a cada N páginas lidas
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

COLUMNS = ["region", "snapshot_date", "league", "team", "summoner", "position", "end_date_raw",
           "end_date", "residency", "status"]


def _canonical(header: str) -> str | None:
    name = canonical_header(clean_cell(header))
    return name if name != "quadcode" else None


def clean_cell(cell: str) -> str:
    """Tira atributos de célula (style=...|valor), links e negrito do wikitexto."""
    cell = cell.strip()
    m = re.match(r'^(?:[a-z-]+\s*=\s*"[^"]*"\s*)+\|(?!\|)(.*)$', cell, flags=re.I)
    if m:
        cell = m.group(1)
    cell = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", cell)
    cell = re.sub(r"'{2,}", "", cell)
    cell = re.sub(r"<[^>]+>", "", cell)
    return cell.strip()


def parse_wikitable(text: str) -> pd.DataFrame:
    """Transforma a primeira tabela wiki da página em linhas com as colunas padronizadas."""
    header: list[str | None] | None = None
    rows: list[list[str]] = []
    current: list[str] | None = None
    in_table = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("{|"):
            in_table = True
            continue
        if not in_table:
            continue
        if line.startswith("|}"):
            break
        if line.startswith("|-"):
            if current:
                rows.append(current)
            current = []
            continue
        if line.startswith("!"):
            header = [_canonical(h) for h in line[1:].split("!!")]
            current = None
            continue
        if line.startswith("|") and current is not None:
            current.extend(line[1:].split("||"))
    if current:
        rows.append(current)
    if not header:
        return pd.DataFrame(columns=[c for c in COLUMNS if c not in ("region", "snapshot_date")])
    out = []
    for cells in rows:
        rec = {name: clean_cell(cells[i]) for i, name in enumerate(header) if name and i < len(cells)}
        if rec.get("team") and rec.get("summoner"):
            out.append(rec)
    return pd.DataFrame(out)


def list_archive(cargo: Cargo) -> list[tuple[str, str, str]]:
    """(título, região, data) de todas as cópias diárias."""
    ns = cargo.call({"action": "query", "meta": "siteinfo", "siprop": "namespaces"})["query"]["namespaces"]
    archive = next(int(k) for k, v in ns.items() if v.get("*") == "Archive" or v.get("name") == "Archive")
    out, cont = [], {}
    while True:
        data = cargo.call({"action": "query", "list": "allpages", "apprefix": PREFIX,
                           "apnamespace": archive, "aplimit": 500, **cont})
        for p in data["query"]["allpages"]:
            parts = p["title"].split("/")
            if len(parts) == 3 and DATE_RE.match(parts[2]):
                out.append((p["title"], parts[1], parts[2]))
        if "continue" not in data:
            return out
        cont = {k: v for k, v in data["continue"].items() if k != "continue"}


def fetch_contents(cargo: Cargo, titles: list[str]) -> dict[str, str]:
    data = cargo.call({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                       "titles": "|".join(titles), "formatversion": 2})
    out = {}
    for p in data.get("query", {}).get("pages", []):
        rev = (p.get("revisions") or [{}])[0]
        content = rev.get("slots", {}).get("main", {}).get("content") or rev.get("content")
        if content is not None:
            out[p["title"].replace(" ", "_")] = content
    return out


def _flush(new_rows: list[pd.DataFrame], new_pages: list[dict], pages_old, result: SourceResult):
    if new_rows:
        snap = pd.concat(new_rows, ignore_index=True)
        snap["end_date"] = pd.to_datetime(snap["end_date_raw"], errors="coerce", format="mixed").dt.strftime("%Y-%m-%d")
        snap = snap.reindex(columns=COLUMNS)
        for year, part in snap.groupby(snap["snapshot_date"].str[:4]):
            old = read_dataset(SOURCE, "snapshots", year)
            merged = part if old is None else pd.concat([old, part], ignore_index=True).drop_duplicates()
            merged = merged.sort_values(["snapshot_date", "region", "league", "team", "summoner"])
            result.record(dataset_key(SOURCE, "snapshots", year),
                          write_dataset(merged, SOURCE, "snapshots", year, date_col="snapshot_date"))
    pages = pd.DataFrame(new_pages)
    merged_pages = pages if pages_old is None else pd.concat([pages_old, pages], ignore_index=True)
    merged_pages = merged_pages.drop_duplicates("title", keep="last")
    result.record(dataset_key(SOURCE, "pages"), write_dataset(merged_pages, SOURCE, "pages", date_col="snapshot_date"))
    return merged_pages


def collect(ctx: RunContext) -> SourceResult:
    result = SourceResult()
    cargo = Cargo(ctx.session)
    user, password = os.environ.get("LEAGUEPEDIA_USER"), os.environ.get("LEAGUEPEDIA_PASSWORD")
    if user and password:
        try:
            cargo.login(user, password)
        except Exception as e:  # login é opcional
            result.error(f"login falhou, seguindo anônimo ({e})")

    try:
        archive = list_archive(cargo)
    except (CargoError, KeyError, StopIteration) as e:
        result.error(f"não consegui listar o arquivo do GCD: {e}")
        return result
    pages_old = read_dataset(SOURCE, "pages")
    done = set() if pages_old is None or ctx.full else set(pages_old["title"])
    pending = [a for a in archive if a[0].replace(" ", "_") not in done]
    if ctx.years is not None:
        pending = [a for a in pending if int(a[2][:4]) in ctx.years]
    pending.sort(key=lambda a: (a[2], a[1]))
    log.info("  arquivo do GCD: %d cópias, %d a ler", len(archive), len(pending))
    result.stats["pages_total"] = len(archive)

    new_rows: list[pd.DataFrame] = []
    new_pages: list[dict] = []
    since_flush = 0
    for i in range(0, len(pending), BATCH):
        if ctx.budget.expired():
            result.incomplete = True
            break
        batch = pending[i:i + BATCH]
        try:
            contents = fetch_contents(cargo, [t for t, _, _ in batch])
        except (CargoError, ValueError) as e:
            result.error(f"lote a partir de {batch[0][0]}: {e}")
            continue
        for title, region, date in batch:
            text = contents.get(title.replace(" ", "_"))
            if text is None:
                continue
            df = parse_wikitable(text)
            df["region"], df["snapshot_date"] = region, date
            new_rows.append(df)
            new_pages.append({"title": title.replace(" ", "_"), "region": region, "snapshot_date": date,
                              "rows": len(df), "read_at": now_utc().isoformat(timespec="seconds")})
            since_flush += 1
        if since_flush >= CHECKPOINT_PAGES:
            pages_old = _flush(new_rows, new_pages, pages_old, result)
            new_rows, new_pages, since_flush = [], [], 0
    if new_pages:
        _flush(new_rows, new_pages, pages_old, result)
    result.stats["rate_limit_pauses"] = cargo.pauses
    return result
