"""Contexto de uma execução de coleta: orçamento de tempo, anos-alvo e modo de atualização."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from .http import make_session


class Budget:
    """Orçamento de tempo da execução.

    A primeira carga histórica pode levar horas. Em vez de falhar no limite de tempo do
    GitHub Actions, cada coletor confere o orçamento entre partições e para de forma limpa;
    a execução seguinte continua do ponto em que parou.
    """

    def __init__(self, max_minutes: float | None):
        self.deadline = None if max_minutes is None else time.monotonic() + max_minutes * 60

    def expired(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline

    def remaining_minutes(self) -> float | None:
        if self.deadline is None:
            return None
        return max(0.0, (self.deadline - time.monotonic()) / 60)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def hot_years(today: datetime | None = None) -> set[int]:
    """Anos que ainda recebem dados novos e por isso são recoletados em toda execução.

    O ano corrente sempre; o anterior só em janeiro e fevereiro, quando correções
    do fim da temporada ainda podem aparecer.
    """
    today = today or now_utc()
    years = {today.year}
    if today.month <= 2:
        years.add(today.year - 1)
    return years


@dataclass
class RunContext:
    budget: Budget
    full: bool = False  # True: recoleta tudo, inclusive anos fechados
    years: list[int] | None = None  # restringe a coleta a estes anos (None = todos)
    session: requests.Session = field(default_factory=make_session)

    def wants_year(self, year: int) -> bool:
        return self.years is None or year in self.years

    def should_refresh(self, year: int, exists: bool) -> bool:
        """Decide se uma partição anual precisa ser (re)coletada."""
        if not self.wants_year(year):
            return False
        explicit = self.years is not None  # anos pedidos explicitamente sempre são recoletados
        return self.full or explicit or not exists or year in hot_years()
