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

    def record(self, key: str, changed: bool) -> None:
        (self.written if changed else self.unchanged).append(key)
        log.info("  %s %s", "gravado" if changed else "sem mudança", key)

    def error(self, msg: str) -> None:
        log.warning("  ERRO: %s", msg)
        if len(self.errors) < 30:  # o log versionado guarda só os primeiros
            self.errors.append(msg[:400])

    @property
    def status(self) -> str:
        if self.errors and not (self.written or self.unchanged):
            return "failed"
        if self.errors:
            return "partial"
        if self.incomplete:
            return "incomplete"
        return "ok"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "written": self.written,
            "unchanged": len(self.unchanged),
            "skipped": len(self.skipped),
            "errors": self.errors,
            "incomplete": self.incomplete,
        }
