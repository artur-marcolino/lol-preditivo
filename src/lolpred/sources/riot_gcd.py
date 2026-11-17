"""Global Contract Database (GCD) oficial da Riot: contratos vigentes de jogadores.

O GCD é o registro oficial de quem está contratado por qual time nas ligas reconhecidas
pela Riot, com a data de fim de contrato. O site de Operações Competitivas da Riot
(competitiveops.riotgames.com) aponta para uma planilha pública do Google, com uma aba
por região (AME, CN, EMEA, KR, LCP...). Cada aba pode ser baixada em CSV.

A planilha só mostra o estado de hoje, então guardamos o histórico (`contracts_history`):
uma linha nova sempre que o contrato de alguém muda (time, fim de contrato, status...).
O histórico anterior a esta coleta vem do arquivo da Leaguepedia (fonte `leaguepedia_gcd`).

Privacidade: a planilha traz nome civil e e-mail de contato dos times. Nada disso é
guardado; ficam só os campos esportivos (liga, time, nick, posição, fim de contrato,
residência, status).
"""

from __future__ import annotations

import csv
import io
import re

import pandas as pd
import requests

from ..context import RunContext
from ..storage import dataset_key, write_dataset
from .base import SourceResult, log
from .snapshots import write_with_history

SOURCE = "riot_gcd"
SHEET_ID = "1Y7k5kQ2AegbuyiGwEPsa62e883FYVtHqr6UVut9RC4o"
PUBHTML = "https://docs.google.com/spreadsheets/d/{sheet}/pubhtml"
CSV_URL = "https://docs.google.com/spreadsheets/d/{sheet}/pub?gid={gid}&single=true&output=csv"
# Abas conhecidas em 16/11/2026; a lista real é lida da página publicada a cada coleta
KNOWN_TABS = {"AME": "0", "CN": "594163931", "EMEA": "148326031", "KR": "905624073", "LCP": "1177719586"}
SKIP_TABS = {"INFO"}
TAB_RE = re.compile(r'name:\s*"([^"]+)",\s*pageUrl:\s*"[^"]*",\s*gid:\s*"(\d+)"')

# Cabeçalho da planilha -> nome padronizado. Colunas fora daqui (nomes civis, contato) são descartadas.
COLUMN_RULES = [
    ("league", lambda h: h == "league"),
    ("team", lambda h: h == "team"),
    ("summoner", lambda h: "summoner" in h or h in ("ign", "handle", "player")),
    ("position", lambda h: h in ("position", "main role", "role")),
    ("end_date_raw", lambda h: h.startswith("end date")),
    ("residency", lambda h: h.startswith("residency")),
    ("status", lambda h: h == "status"),
    ("quadcode", lambda h: h == "quadcode"),
]
OUTPUT_COLUMNS = ["region", "league", "team", "summoner", "position", "end_date_raw", "end_date",
                  "residency", "status", "quadcode", "sheet_last_update"]


def list_tabs(page: str) -> dict[str, str]:
    tabs = {name: gid for name, gid in TAB_RE.findall(page)}
    return {n: g for n, g in tabs.items() if n not in SKIP_TABS}


def _canonical(header: str) -> str | None:
    h = re.sub(r"\s+", " ", header.strip().lower())
    for name, rule in COLUMN_RULES:
        if rule(h):
            return name
    return None


def parse_sheet(text: str, region: str) -> pd.DataFrame:
    """Lê o CSV de uma aba: acha a linha de cabeçalho e ignora linhas de seção ("LCS TEAMS")."""
    rows = list(csv.reader(io.StringIO(text)))
    header_idx = next((i for i, r in enumerate(rows)
                       if {"league", "team"} <= {c.strip().lower() for c in r}), None)
    if header_idx is None:
        raise ValueError(f"aba {region}: cabeçalho com League/Team não encontrado")
    # Primeira linha costuma trazer a data da última atualização ("LAST UPDATE:", "Oct 27, 2026 ...")
    last_update = None
    for r in rows[:header_idx]:
        cells = [c.strip() for c in r if c.strip()]
        if cells and "update" in cells[0].lower() and len(cells) > 1:
            last_update = cells[1]
            break
    mapping = {i: _canonical(h) for i, h in enumerate(rows[header_idx])}
    out = []
    for r in rows[header_idx + 1:]:
        rec = {name: r[i].strip() for i, name in mapping.items() if name and i < len(r)}
        if not rec.get("team") or not rec.get("summoner"):
            continue  # linha de seção ou vazia
        out.append(rec)
    df = pd.DataFrame(out)
    for col in OUTPUT_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df["region"] = region
    df["sheet_last_update"] = last_update
    df["end_date"] = pd.to_datetime(df["end_date_raw"], errors="coerce", format="mixed").dt.strftime("%Y-%m-%d")
    return df[OUTPUT_COLUMNS]


def collect(ctx: RunContext) -> SourceResult:
    result = SourceResult()
    try:
        page = ctx.session.get(PUBHTML.format(sheet=SHEET_ID), timeout=60)
        page.raise_for_status()
        tabs = list_tabs(page.text) or KNOWN_TABS
    except requests.RequestException as e:
        log.warning("  GCD: não consegui ler a lista de abas (%s); usando as conhecidas", e)
        tabs = KNOWN_TABS

    frames = []
    for region, gid in tabs.items():
        try:
            resp = ctx.session.get(CSV_URL.format(sheet=SHEET_ID, gid=gid), timeout=60)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            df = parse_sheet(resp.text, region)
            log.info("  GCD %s: %d contratos", region, len(df))
            frames.append(df)
        except (requests.RequestException, ValueError) as e:
            result.error(f"aba {region}: {e}")
    if not frames:
        return result

    contracts = pd.concat(frames, ignore_index=True)
    contracts["player_key"] = (contracts["region"] + "|" + contracts["league"].fillna("") + "|"
                               + contracts["team"].fillna("") + "|" + contracts["summoner"].fillna(""))
    contracts = contracts.drop_duplicates("player_key", keep="first")
    write_with_history(contracts, "player_key", SOURCE, "contracts", result)
    sheets = (contracts.groupby("region").agg(contracts=("player_key", "size"),
                                              sheet_last_update=("sheet_last_update", "first"))
              .reset_index())
    result.record(dataset_key(SOURCE, "sheets"), write_dataset(sheets, SOURCE, "sheets"))
    return result
