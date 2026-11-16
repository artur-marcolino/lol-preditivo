"""Orquestra a coleta: roda cada fonte isoladamente e registra o resultado."""

from __future__ import annotations

import json
import time
import traceback

from .context import Budget, RunContext, now_utc
from .sources import SOURCES
from .sources.base import log
from .storage import data_dir, update_source_status


def run(sources: list[str] | None = None, full: bool = False, years: list[int] | None = None,
        max_minutes: float | None = None, backfill_only: bool = False) -> dict:
    """Coleta as fontes pedidas (todas, por padrão).

    Grava `data/runs/<fonte>.json` com o resumo de cada uma. Rodando várias fontes no mesmo
    processo, elas dividem o mesmo orçamento de tempo, em sequência; no GitHub Actions cada
    fonte roda num job separado, com orçamento próprio. Uma fonte que falha não impede as outras.
    """
    names = sources or list(SOURCES)
    ctx = RunContext(budget=Budget(max_minutes), full=full, years=years, backfill_only=backfill_only)
    report = {}
    for name in names:
        log.info("=== %s ===", name)
        started = now_utc().isoformat(timespec="seconds")
        t0 = time.monotonic()
        try:
            entry = SOURCES[name](ctx).to_dict()
        except Exception as e:  # erro inesperado numa fonte não derruba as outras
            log.exception("falha inesperada em %s", name)
            entry = {"status": "failed", "errors": [f"{type(e).__name__}: {e}"],
                     "traceback": traceback.format_exc(limit=5)}
        entry.update({"started_at": started, "finished_at": now_utc().isoformat(timespec="seconds"),
                      "minutes": round((time.monotonic() - t0) / 60, 1), "full": full, "years": years,
                      "max_minutes": max_minutes, "backfill_only": backfill_only})
        report[name] = entry
        notes = entry.get("errors", []) + entry.get("deferred", [])
        update_source_status(name, entry["status"], "; ".join(notes)[:500],
                             success=entry["status"] not in ("failed", "deferred"))
        path = data_dir() / "runs" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report
