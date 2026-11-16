"""Sessão HTTP compartilhada: User-Agent identificável, retentativas e limite de ritmo."""

from __future__ import annotations

import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "lol-preditivo/0.1 (projeto recreativo de análise; github.com/zLanchus/lol-preditivo)"


def make_session(user_agent: str = USER_AGENT) -> requests.Session:
    """Sessão com retentativa automática em erros transitórios (429 e 5xx)."""
    session = requests.Session()
    session.headers["User-Agent"] = user_agent
    retry = Retry(
        total=5,
        backoff_factor=2,  # espera 2, 4, 8, 16... segundos
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class RateLimiter:
    """Garante um intervalo mínimo entre chamadas, para não sobrecarregar a fonte."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()
