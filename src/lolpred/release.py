"""Publicação e download dos dados pela Release `data-latest` do GitHub.

Por que não guardar os Parquet no próprio git? Porque o ano corrente muda todo dia: cada
versão nova ficaria para sempre no histórico e o repositório incharia rápido. A Release
guarda só a versão atual de cada arquivo; o git guarda os manifestos (com o fingerprint de
cada arquivo), que são leves e documentam quando cada tabela mudou.

Os arquivos sobem com o caminho codificado no nome:
`processed/leaguepedia/ScoreboardGames/2024.parquet` -> `leaguepedia__ScoreboardGames__2024.parquet`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests

from .sources.base import log
from .storage import reconcile, data_dir, load_manifest, manifest_path, manifest_sources, processed_dir, save_manifest

TAG = os.environ.get("LOLPRED_RELEASE_TAG", "data-latest")
SEP = "__"


def asset_name(path: Path) -> str:
    rel = path.relative_to(processed_dir())
    return SEP.join(rel.parts)


def asset_to_path(name: str) -> Path:
    return processed_dir().joinpath(*name.split(SEP))


def repo_slug() -> str:
    repo = os.environ.get("LOLPRED_REPO") or os.environ.get("GITHUB_REPOSITORY")
    if repo:
        return repo
    url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", url)
    if not m:
        raise RuntimeError("não sei qual é o repositório; defina LOLPRED_REPO=dono/repo")
    return m.group(1)


def _gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=check)


def _ensure_release(repo: str) -> None:
    if _gh("release", "view", TAG, "-R", repo, check=False).returncode != 0:
        notes = ("Dados processados (Parquet) da coleta automática. Esta release é sobrescrita a cada coleta; "
                 "veja data/manifest.json no repositório para o histórico de versões.")
        _gh("release", "create", TAG, "-R", repo, "--title", "Dados mais recentes", "--notes", notes, "--latest=false")


def publish(sources: list[str] | None = None, snapshot_tag: str | None = None) -> list[str]:
    """Sobe para a Release os arquivos cujo conteúdo mudou desde a última publicação."""
    repo = repo_slug()
    _ensure_release(repo)
    remote = {a["name"] for a in json.loads(_gh("release", "view", TAG, "-R", repo, "--json", "assets").stdout)["assets"]}
    uploaded = []
    for source in sources or manifest_sources():
        manifest = load_manifest(source)
        published = manifest.setdefault("published", {})
        with tempfile.TemporaryDirectory() as td:
            for key, meta in sorted(manifest["datasets"].items()):
                path = data_dir() / meta["path"]
                if not path.exists():
                    continue
                name = asset_name(path)
                if published.get(key) == meta["fingerprint"] and name in remote:
                    continue
                staged = Path(td) / name
                shutil.copyfile(path, staged)
                _gh("release", "upload", TAG, str(staged), "--clobber", "-R", repo)
                published[key] = meta["fingerprint"]
                uploaded.append(name)
                log.info("  enviado %s", name)
        save_manifest(source, manifest)

    if snapshot_tag:
        snapshot(repo, snapshot_tag)
    return uploaded


def snapshot(repo: str, tag: str) -> None:
    """Cria uma Release fixa (ex.: data-2026-12-01) com todos os arquivos atuais.

    Serve para reprodutibilidade: um modelo treinado com a snapshot de um mês pode ser
    refeito depois com exatamente os mesmos dados.
    """
    if _gh("release", "view", tag, "-R", repo, check=False).returncode == 0:
        log.info("snapshot %s já existe", tag)
        return
    with tempfile.TemporaryDirectory() as td:
        files = []
        for path in sorted(processed_dir().rglob("*.parquet")):
            staged = Path(td) / asset_name(path)
            shutil.copyfile(path, staged)
            files.append(str(staged))
        for source in manifest_sources():
            staged = Path(td) / f"manifest{SEP}{source}.json"
            shutil.copyfile(manifest_path(source), staged)
            files.append(str(staged))
        _gh("release", "create", tag, *files, "-R", repo, "--title", f"Snapshot {tag}",
            "--notes", "Cópia congelada dos dados nesta data.", "--latest=false")
    log.info("snapshot %s criada com %d arquivos", tag, len(files))


def pull(tag: str = TAG, pattern: str | None = None) -> int:
    """Baixa os Parquet da Release para data/processed. Usa o gh se houver; senão, a API pública."""
    repo = repo_slug()
    processed_dir().mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        use_api = shutil.which("gh") is None
        if not use_api:
            args = ["release", "download", tag, "-R", repo, "-D", td, "--clobber"]
            args += ["--pattern", pattern or "*.parquet"]
            proc = _gh(*args, check=False)
            if proc.returncode != 0:
                err = proc.stderr.lower()
                if "release not found" in err or "no assets" in err:
                    log.info("nada para baixar de %s (%s)", tag, proc.stderr.strip())
                    return 0
                # gh sem login, por exemplo: tenta a API pública (funciona em repositório público)
                log.info("gh falhou (%s); usando a API pública do GitHub", proc.stderr.strip()[:120])
                use_api = True
        if use_api:
            _download_via_api(repo, tag, Path(td), pattern)
        count = 0
        for f in Path(td).glob("*.parquet"):
            dest = asset_to_path(f.name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), dest)
            count += 1
    log.info("baixados %d arquivos de %s@%s", count, repo, tag)
    recovered = reconcile(mark_published=(tag == TAG))
    if recovered:
        log.info("registrados no manifesto %d arquivos que estavam só na Release", len(recovered))
    return count


def _download_via_api(repo: str, tag: str, dest: Path, pattern: str | None) -> None:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    resp = requests.get(url, headers=headers, timeout=60)
    if resp.status_code == 401 and token:  # token inválido: tenta sem autenticação
        headers.pop("Authorization")
        resp = requests.get(url, headers=headers, timeout=60)
    if resp.status_code == 404:
        raise RuntimeError(f"release {tag} não encontrada (repositório privado? instale o gh e faça `gh auth login`)")
    resp.raise_for_status()
    rx = re.compile((pattern or "*.parquet").replace(".", r"\.").replace("*", ".*") + "$")
    for asset in resp.json().get("assets", []):
        if not rx.match(asset["name"]):
            continue
        with requests.get(asset["url"], headers={**headers, "Accept": "application/octet-stream"},
                          stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(dest / asset["name"], "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
