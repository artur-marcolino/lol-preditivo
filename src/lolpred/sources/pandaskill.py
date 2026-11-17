"""PandaSkill (PandaScore): estatísticas de fim de jogo por jogador e ratings de habilidade.

Dados do artigo "PandaSkill - Player Performance and Skill Rating in Esports" (De Bois et
al., 2025), publicados no repositório aberto github.com/PandaScore/pandaskill (os mesmos
arquivos do dataset no IEEE DataPort, DOI 10.21227/5evv-jk25, licença CC BY 4.0):

- `game_metadata`: um jogo por linha (série, liga, torneio, data, patch...);
- `game_players_stats`: estatísticas de fim de jogo de cada jogador (28 campos);
- `game_events`: eventos de abate com tempo e posição;
- `skill_ratings` e `pscores`: o rating de habilidade e a nota de desempenho por jogo
  calculados pelo PandaSkill.

Cobre todas as partidas profissionais de 15/09/2019 a 15/09/2024. É um conjunto fechado:
os arquivos estão no Git LFS, e a cada coleta comparamos só o identificador (oid) de cada
um; o download só acontece quando o arquivo mudou.

`skill_ratings` traz o rating de cada jogador antes e depois de cada jogo
(`*_rating_before*` e `*_rating_after*`). Para variáveis pré-partida, use só as colunas
`_before` do primeiro jogo da série (ou as `_after` do último jogo anterior a ela).
"""

from __future__ import annotations

import io
import re

import pandas as pd
import requests

from ..context import RunContext
from ..storage import dataset_key, load_manifest, touch_dataset, write_dataset
from .base import SourceResult, log

SOURCE = "pandaskill"
REPO = "PandaScore/pandaskill"
BRANCH = "main"
POINTER_URL = "https://raw.githubusercontent.com/{repo}/{branch}/pandaskill/artifacts/data/{path}"
MEDIA_URL = "https://media.githubusercontent.com/media/{repo}/{branch}/pandaskill/artifacts/data/{path}"
FILES = {  # tabela -> caminho no repositório
    "game_metadata": "raw/game_metadata.csv",
    "game_players_stats": "raw/game_players_stats.csv",
    "game_events": "raw/game_events.csv",
    "skill_ratings": "app/pandaskill_skill_ratings.csv",
    "pscores": "app/pandaskill_pscores.csv",
}
DATE_COLUMNS = ("date", "game_date", "start_date", "begin_at")
OID_RE = re.compile(r"oid sha256:([0-9a-f]{64})")


def lfs_oid(session: requests.Session, path: str) -> str | None:
    """Lê o ponteiro do Git LFS (poucos bytes) e devolve o hash do arquivo."""
    resp = session.get(POINTER_URL.format(repo=REPO, branch=BRANCH, path=path), timeout=60)
    resp.raise_for_status()
    m = OID_RE.search(resp.text[:500])
    return m.group(1) if m else None


def collect(ctx: RunContext) -> SourceResult:
    result = SourceResult()
    datasets = load_manifest(SOURCE)["datasets"]
    for table, path in FILES.items():
        key = dataset_key(SOURCE, table)
        try:
            oid = lfs_oid(ctx.session, path)
            if oid and not ctx.full and datasets.get(key, {}).get("lfs_oid") == oid:
                result.skipped.append(key)
                continue
            if ctx.budget.expired():
                result.incomplete = True
                break
            log.info("  PandaSkill: baixando %s", path)
            resp = ctx.session.get(MEDIA_URL.format(repo=REPO, branch=BRANCH, path=path), timeout=600)
            resp.raise_for_status()
            if resp.content[:200].lstrip().startswith(b"version https://git-lfs"):
                raise ValueError("recebi o ponteiro do LFS em vez do arquivo")
            df = pd.read_csv(io.BytesIO(resp.content), low_memory=False)
            date_col = next((c for c in DATE_COLUMNS if c in df.columns), None)
            changed = write_dataset(df, SOURCE, table, date_col=date_col, extra={"source_path": path})
            touch_dataset(SOURCE, table, lfs_oid=oid)  # vale também quando o conteúdo não mudou
            result.record(key, changed)
        except (requests.RequestException, ValueError, pd.errors.ParserError) as e:
            result.error(f"{table}: {e}")
    return result
