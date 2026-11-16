"""Decide quais fontes uma execução do workflow vai coletar.

Regras:
- agendada (schedule): todas as fontes;
- manual (workflow_dispatch): as fontes pedidas no formulário (ou todas);
- push: só as fontes cujo coletor mudou (src/lolpred/sources/<fonte>.py). A mensagem do
  commit pode pedir explicitamente: "[coleta: lolesports meraki]" ou "[coleta: todas]".
  Mudanças só no banco analítico/documentação não disparam coleta;
- fontes opcionais (que precisam de chave) ficam de fora se o segredo não estiver configurado;
- fontes que já estão rodando em outra execução ficam de fora (evita duas coletas iguais
  ao mesmo tempo disputando a mesma API e o mesmo manifesto).

Saída (para $GITHUB_OUTPUT): sources=<lista JSON>
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REGISTRY = Path("src/lolpred/sources/__init__.py")
OPTIONAL = {"pandascore": "HAS_PANDASCORE", "oddspapi": "HAS_ODDSPAPI"}
TAG_RE = re.compile(r"\[coleta:\s*([^\]]*)\]", re.IGNORECASE)


def all_sources(registry_text: str) -> list[str]:
    return re.findall(r'"(\w+)":\s*\w+\.collect', registry_text)


def from_message(message: str, known: list[str]) -> list[str] | None:
    m = TAG_RE.search(message or "")
    if not m:
        return None
    words = m.group(1).replace(",", " ").split()
    if any(w.lower() in ("todas", "all") for w in words):
        return list(known)
    return [w for w in words if w in known]


def from_changed_files(files: list[str], known: list[str]) -> list[str]:
    out = []
    for f in files:
        m = re.fullmatch(r"src/lolpred/sources/(\w+)\.py", f)
        if m and m.group(1) in known and m.group(1) not in out:
            out.append(m.group(1))
    return out


def plan(event: str, known: list[str], dispatch_input: str = "", message: str = "",
         changed_files: list[str] | None = None, secrets: dict[str, bool] | None = None,
         busy: set[str] | None = None) -> tuple[list[str], list[str]]:
    """Devolve (fontes a coletar, observações)."""
    notes: list[str] = []
    if event == "workflow_dispatch":
        wanted = [s for s in dispatch_input.split() if s in known] if dispatch_input.strip() else list(known)
    elif event == "push":
        tagged = from_message(message, known)
        wanted = tagged if tagged is not None else from_changed_files(changed_files or [], known)
    else:
        wanted = list(known)

    secrets = secrets or {}
    for source, flag in OPTIONAL.items():
        if source in wanted and not secrets.get(flag, False):
            wanted.remove(source)
            notes.append(f"{source}: sem chave configurada, fica de fora")
    for source in sorted(busy or set()):
        if source in wanted:
            wanted.remove(source)
            notes.append(f"{source}: já está rodando em outra execução, fica de fora")
    return wanted, notes


def busy_sources(repo: str, run_id: str) -> set[str]:
    """Fontes com job de coleta em andamento/na fila em outras execuções deste workflow."""
    busy: set[str] = set()
    try:
        runs = []
        for status in ("in_progress", "queued"):
            out = subprocess.run(["gh", "api", f"repos/{repo}/actions/workflows/collect.yml/runs?status={status}"],
                                 capture_output=True, text=True, check=True).stdout
            runs += json.loads(out).get("workflow_runs", [])
        for run in runs:
            if str(run["id"]) == str(run_id):
                continue
            out = subprocess.run(["gh", "api", f"repos/{repo}/actions/runs/{run['id']}/jobs"],
                                 capture_output=True, text=True, check=True).stdout
            for job in json.loads(out).get("jobs", []):
                m = re.fullmatch(r"collect \((\w+)\)", job.get("name", ""))
                if m and job.get("status") in ("queued", "in_progress"):
                    busy.add(m.group(1))
    except Exception as e:  # sem permissão/rede: segue sem a trava
        print(f"aviso: não consegui verificar execuções em andamento ({e})", file=sys.stderr)
    return busy


def main() -> None:
    known = all_sources(REGISTRY.read_text(encoding="utf-8"))
    event = os.environ.get("EVENT", "")
    changed: list[str] = []
    if event == "push":
        before, after = os.environ.get("BEFORE", ""), os.environ.get("AFTER", "")
        if before and not set(before) <= {"0"}:
            changed = subprocess.run(["git", "diff", "--name-only", before, after],
                                     capture_output=True, text=True).stdout.split()
    secrets = {flag: os.environ.get(flag, "false") == "true" for flag in OPTIONAL.values()}
    busy = busy_sources(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("RUN_ID", ""))
    sources, notes = plan(event, known, os.environ.get("INPUT", ""), os.environ.get("MESSAGE", ""),
                          changed, secrets, busy)
    for n in notes:
        print(f"::notice::{n}", file=sys.stderr)
    print(f"fontes: {sources or 'nenhuma'}", file=sys.stderr)
    print(f"sources={json.dumps(sources)}")


if __name__ == "__main__":
    main()
