"""Oracle's Elixir: um CSV por ano com todas as partidas profissionais.

Cada jogo ocupa 12 linhas: 10 de jogadores e 2 de times (coluna `position == "team"`).
Os arquivos ficam numa pasta pública do Google Drive mantida por Tim Sevenhuysen.

Como funciona a coleta:
1. Lê a listagem da pasta pública (página `embeddedfolderview`) para achar o id de cada arquivo.
2. Baixa o CSV do ano. O Google às vezes mostra uma página de confirmação para arquivos
   grandes ou responde "Quota exceeded" quando muita gente baixa o mesmo arquivo; nesses
   casos o coletor tenta outros links e, se nada funcionar, mantém a versão anterior.
   A cota do Drive volta sozinha em algumas horas, então "Quota exceeded" com uma cópia
   baixada há menos de 2 dias conta como atualização adiada, não como falha; passado
   esse prazo, vira erro para chamar atenção.
3. Converte para Parquet (~10x menor) e grava em `oracles_elixir/matches/<ano>.parquet`.

Os anos fechados são baixados uma única vez; o ano corrente é baixado em toda execução.
"""

from __future__ import annotations

import html
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from ..context import RunContext, now_utc
from ..storage import data_dir, dataset_exists, dataset_key, load_manifest, touch_dataset, write_dataset
from .base import SourceResult, log

SOURCE = "oracles_elixir"
FIRST_YEAR = 2014
DEFAULT_FOLDER_ID = "1gLSw0RLjBbtaNy0dgnGQDAZOHIgCe-HH"
S3_URL = "https://oracleselixir-downloadable-match-data.s3-us-west-2.amazonaws.com/{name}"
FILE_RE = re.compile(r"^(\d{4})_LoL_esports_match_data_from_OraclesElixir(?:_\d+)?\.csv$")
CSV_HEADER = b"gameid,"
QUOTA_GRACE = timedelta(days=2)  # por quanto tempo "Quota exceeded" é só um adiamento


class DriveError(RuntimeError):
    pass


def parse_folder_listing(page: str) -> dict[str, str]:
    """Extrai {nome do arquivo: id no Drive} da página embeddedfolderview."""
    files: dict[str, str] = {}
    for m in re.finditer(r'id="entry-([\w-]+)".*?class="flip-entry-title">([^<]+)<', page, re.S):
        files[html.unescape(m.group(2)).strip()] = m.group(1)
    if not files:  # plano B caso o Google mude o HTML
        pattern = r'/file/d/([\w-]{20,})[^"]*"[^>]*>(?:(?!</a>).)*?(\d{4}_LoL_esports_match_data_from_OraclesElixir[^<"]*\.csv)'
        for m in re.finditer(pattern, page, re.S):
            files[m.group(2)] = m.group(1)
    return files


def files_by_year(listing: dict[str, str]) -> dict[int, tuple[str, str]]:
    """{ano: (nome, id)}. Se houver mais de um arquivo no ano, fica o de nome mais recente."""
    out: dict[int, tuple[str, str]] = {}
    for name, fid in sorted(listing.items()):
        m = FILE_RE.match(name)
        if m:
            out[int(m.group(1))] = (name, fid)
    return out


def list_folder(session: requests.Session, folder_id: str) -> dict[str, str]:
    resp = session.get("https://drive.google.com/embeddedfolderview", params={"id": folder_id}, timeout=60)
    resp.raise_for_status()
    return parse_folder_listing(resp.text)


def _stream_csv(resp: requests.Response, dest: Path) -> bytes | None:
    """Grava a resposta se ela for o CSV. Se for HTML (confirmação, cota), devolve o início dela."""
    it = resp.iter_content(chunk_size=1 << 20)
    first = b""
    for chunk in it:
        first += chunk
        if len(first) >= 64 * 1024:
            break
    if not first.lstrip(b"\xef\xbb\xbf").startswith(CSV_HEADER):
        return first or b"(resposta vazia)"
    tmp = dest.with_suffix(".part")
    with open(tmp, "wb") as f:
        f.write(first)
        for chunk in it:
            f.write(chunk)
    os.replace(tmp, dest)
    return None


def _try_url(session: requests.Session, url: str, params: dict, dest: Path) -> str | None:
    """Tenta um link. Devolve None em caso de sucesso ou a descrição da falha."""
    with session.get(url, params=params, stream=True, timeout=300) as resp:
        if resp.status_code != 200:
            return f"HTTP {resp.status_code}"
        page = _stream_csv(resp, dest)
    if page is None:
        return None
    text = page.decode("utf-8", "replace")
    # Página "não foi possível verificar vírus": reenviar o formulário com os campos ocultos
    form = re.search(r'<form[^>]+action="([^"]+)"', text)
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', text))
    if form and fields:
        with session.get(html.unescape(form.group(1)), params=fields, stream=True, timeout=300) as resp2:
            if resp2.status_code == 200 and _stream_csv(resp2, dest) is None:
                return None
    title = re.search(r"<title>([^<]*)</title>", text)
    return f"recebeu HTML em vez de CSV ({title.group(1).strip() if title else text[:80]!r})"


def download_year(session: requests.Session, name: str, file_id: str | None, dest: Path) -> list[str]:
    """Baixa um arquivo tentando várias rotas. Devolve a lista de falhas (vazia = sucesso)."""
    attempts: list[tuple[str, str, dict]] = []
    if file_id:
        api_key = os.environ.get("GOOGLE_API_KEY")
        if api_key:
            attempts.append(("drive-api", f"https://www.googleapis.com/drive/v3/files/{file_id}", {"alt": "media", "key": api_key}))
        params = {"id": file_id, "export": "download", "confirm": "t"}
        attempts.append(("drive-usercontent", "https://drive.usercontent.google.com/download", params))
        attempts.append(("drive-uc", "https://drive.google.com/uc", params))
    attempts.append(("s3-antigo", S3_URL.format(name=name), {}))

    failures = []
    for label, url, params in attempts:
        try:
            err = _try_url(session, url, params, dest)
        except requests.RequestException as e:
            err = f"{type(e).__name__}: {e}"
        if err is None:
            log.info("  baixado via %s", label)
            return []
        failures.append(f"{label}: {err}")
    return failures


def only_drive_quota(failures: list[str]) -> bool:
    """Todas as rotas do Drive responderam "Quota exceeded" (o link S3 antigo não conta)."""
    drive = [f for f in failures if f.startswith("drive")]
    return bool(drive) and all("Quota exceeded" in f for f in drive)


def last_download(key: str) -> datetime | None:
    """Última vez que a partição foi baixada com sucesso (mudando ou não)."""
    meta = load_manifest(SOURCE)["datasets"].get(key) or {}
    ts = meta.get("checked_at") or meta.get("updated_at")
    if not ts:
        return None
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def handle_failure(result: SourceResult, key: str, year: int, failures: list[str]) -> None:
    detail = "; ".join(failures)
    if not dataset_exists(SOURCE, "matches", str(year)):
        result.error(f"{year}: download falhou e não há cópia anterior ({detail})")
        return
    last = last_download(key)
    if only_drive_quota(failures) and last and now_utc() - last < QUOTA_GRACE:
        result.defer(f"{year}: cota de download do Google Drive excedida; mantida a cópia baixada em "
                     f"{last:%Y-%m-%d %H:%M} UTC, nova tentativa na próxima coleta")
        return
    result.error(f"{year}: download falhou, versão anterior mantida ({detail})")


def collect(ctx: RunContext) -> SourceResult:
    result = SourceResult()
    folder_id = os.environ.get("OE_DRIVE_FOLDER_ID", DEFAULT_FOLDER_ID)
    try:
        by_year = files_by_year(list_folder(ctx.session, folder_id))
        log.info("  pasta do Drive: %d arquivos anuais", len(by_year))
    except (requests.RequestException, DriveError) as e:
        result.error(f"não consegui listar a pasta do Drive ({e}); tentando só o link S3")
        by_year = {}

    current = now_utc().year
    for year in range(FIRST_YEAR, current + 1):
        key = dataset_key(SOURCE, "matches", str(year))
        if not ctx.should_refresh(year, dataset_exists(SOURCE, "matches", str(year))):
            result.skipped.append(key)
            continue
        if ctx.budget.expired():
            result.incomplete = True
            break
        name, fid = by_year.get(year, (f"{year}_LoL_esports_match_data_from_OraclesElixir.csv", None))
        log.info("Oracle's Elixir %d (%s)", year, name)
        tmpdir = data_dir() / "tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmpdir) as td:
            dest = Path(td) / name
            failures = download_year(ctx.session, name, fid, dest)
            if failures:
                handle_failure(result, key, year, failures)
                continue
            df = pd.read_csv(dest, low_memory=False)
        changed = write_dataset(
            df, SOURCE, "matches", str(year), date_col="date",
            extra={"source_file": name, "drive_file_id": fid},
        )
        touch_dataset(SOURCE, "matches", str(year), checked_at=now_utc().isoformat(timespec="seconds"))
        result.record(key, changed)
    return result
