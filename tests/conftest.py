"""Fixtures: diretório de dados temporário e uma sessão HTTP falsa (testes rodam offline)."""

from __future__ import annotations

import json as jsonlib
from typing import Callable

import pytest

from lolpred.context import Budget, RunContext


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOLPRED_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("time.sleep", lambda s: None)
    return tmp_path / "data"


class FakeResponse:
    def __init__(self, status_code=200, json=None, content: bytes | None = None, text: str | None = None):
        self.status_code = status_code
        self._json = json
        if content is None:
            content = (text if text is not None else jsonlib.dumps(json)).encode()
        self.content = content
        self.text = content.decode("utf-8", "replace")

    def json(self):
        return self._json if self._json is not None else jsonlib.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1024):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i:i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSession:
    """Responde chamadas com uma função handler(url, params) -> FakeResponse."""

    def __init__(self, handler: Callable):
        self.handler = handler
        self.calls: list[tuple[str, dict]] = []
        self.headers = {}

    def get(self, url, params=None, **kw):
        self.calls.append((url, dict(params or {})))
        return self.handler(url, dict(params or {}))

    post = get


@pytest.fixture
def make_ctx():
    def _make(handler, **kw):
        return RunContext(budget=Budget(None), session=FakeSession(handler), **kw)
    return _make
