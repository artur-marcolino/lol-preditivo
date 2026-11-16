from conftest import FakeResponse

from lolpred.context import now_utc
from lolpred.sources import leaguepedia as lp


def test_build_fields_respects_whitelist():
    spec = lp.TableSpec("Players", "P", include=["ID", "Role"])
    assert lp.build_fields(spec, ["ID", "Name", "Role", "Twitter"]) == "P.ID=ID,P.Role=Role,P._pageName=_pageName"


def test_year_where():
    spec = lp.TableSpec("ScoreboardGames", "SG", date_field="SG.DateTime_UTC")
    assert lp.year_where(spec, 2024) == "SG.DateTime_UTC >= '2024-01-01' AND SG.DateTime_UTC < '2025-01-01'"


def test_query_paginates_and_retries_ratelimit(make_ctx, monkeypatch):
    monkeypatch.setattr(lp, "PAGE_SIZE", 2)
    state = {"limited": True}

    def handler(url, params):
        if params.get("action") == "cargofields":
            return FakeResponse(json={"cargofields": {"GameId": {}, "DateTime_UTC": {}}})
        if state["limited"]:
            state["limited"] = False
            return FakeResponse(json={"error": {"code": "ratelimited", "info": "slow down"}})
        off = int(params["offset"])
        rows = [{"GameId": f"g{i}", "DateTime UTC": "2026-03-01 10:00:00", "_pageName": "p"} for i in range(off, min(off + 2, 3))]
        return FakeResponse(json={"cargoquery": [{"title": r} for r in rows]})

    monkeypatch.setattr(lp, "TABLES", [lp.TableSpec("ScoreboardGames", "SG", date_field="SG.DateTime_UTC")])
    year = now_utc().year
    ctx = make_ctx(handler, years=[year])
    result = lp.collect(ctx)
    assert result.written == [f"leaguepedia/ScoreboardGames/{year}"]
    import pandas as pd
    from lolpred.storage import dataset_path
    df = pd.read_parquet(dataset_path("leaguepedia", "ScoreboardGames", str(year)))
    assert list(df.columns) == ["GameId", "DateTime_UTC", "_pageName"]
    assert len(df) == 3


def test_empty_year_keeps_schema(make_ctx, monkeypatch):
    def handler(url, params):
        if params.get("action") == "cargofields":
            return FakeResponse(json={"cargofields": {"GameId": {}}})
        return FakeResponse(json={"cargoquery": []})

    monkeypatch.setattr(lp, "TABLES", [lp.TableSpec("ScoreboardGames", "SG", date_field="SG.DateTime_UTC")])
    result = lp.collect(make_ctx(handler, years=[2011]))
    assert result.written == ["leaguepedia/ScoreboardGames/2011"]
