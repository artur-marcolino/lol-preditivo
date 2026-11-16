"""Estrutura comum aos coletores."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger("lolpred")


@dataclass
class SourceResult:
    """Resumo do que um coletor fez numa execução."""

    written: list[str] = field(default_factory=list)  # partições cujo conteúdo mudou
    unchanged: list[str] = field(default_factory=list)  # recoletadas, mas iguais às anteriores
    skipped: list[str] = field(default_factory=list)  # já existentes e "frias" (não recoletadas)
    errors: list[str] = field(default_factory=list)
    incomplete: bool = False  # parou por orçamento de tempo; a próxima execução continua
    # atualização adiada por um limite temporário da fonte (a cópia anterior continua valendo)
    deferred: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)  # contadores livres da fonte (ex.: pausas por limite)

    def record(self, key: str, changed: bool) -> None:
        (self.written if changed else self.unchanged).append(key)
        log.info("  %s %s", "gravado" if changed else "sem mudança", key)

    def error(self, msg: str) -> None:
        log.warning("  ERRO: %s", msg)
        if len(self.errors) < 30:  # o log versionado guarda só os primeiros
            self.errors.append(msg[:400])

    def defer(self, msg: str) -> None:
        log.warning("  ADIADO: %s", msg)
        self.deferred.append(msg[:400])

    @property
    def status(self) -> str:
        if self.errors and not (self.written or self.unchanged):
            return "failed"
        if self.errors:
            return "partial"
        if self.incomplete:
            return "incomplete"
        if self.deferred:
            return "deferred"
        return "ok"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "written": self.written,
            "unchanged": len(self.unchanged),
            "skipped": len(self.skipped),
            "errors": self.errors,
            "incomplete": self.incomplete,
            **({"deferred": self.deferred} if self.deferred else {}),
            **({"stats": self.stats} if self.stats else {}),
        }
