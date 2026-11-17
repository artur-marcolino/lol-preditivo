from conftest import FakeResponse

from lolpred.context import now_utc
from lolpred.sources import leaguepedia as lp


def test_build_fields_respects_whitelist():
    spec = lp.TableSpec("Players", "P", include=["ID", "Role"])
    assert lp.build_fields(spec, ["ID", "Name", "Role", "Twitter"]) == "P.ID=ID,P.Role=Role,P._pageName=wiki_page"


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
        rows = [{"GameId": f"g{i}", "DateTime UTC": "2026-03-01 10:00:00", "wiki page": "p"} for i in range(off, min(off + 2, 3))]
        return FakeResponse(json={"cargoquery": [{"title": r} for r in rows]})

    monkeypatch.setattr(lp, "TABLES", [lp.TableSpec("ScoreboardGames", "SG", date_field="SG.DateTime_UTC")])
    year = now_utc().year
    ctx = make_ctx(handler, years=[year])
    result = lp.collect(ctx)
    assert result.written == [f"leaguepedia/ScoreboardGames/{year}"]
    import pandas as pd
    from lolpred.storage import dataset_path
    df = pd.read_parquet(dataset_path("leaguepedia", "ScoreboardGames", str(year)))
    assert list(df.columns) == ["GameId", "DateTime_UTC", "wiki_page"]
    assert len(df) == 3


def test_empty_year_keeps_schema(make_ctx, monkeypatch):
    def handler(url, params):
        if params.get("action") == "cargofields":
            return FakeResponse(json={"cargofields": {"GameId": {}, "DateTime_UTC": {}}})
        return FakeResponse(json={"cargoquery": []})

    monkeypatch.setattr(lp, "TABLES", [lp.TableSpec("ScoreboardGames", "SG", date_field="SG.DateTime_UTC")])
    result = lp.collect(make_ctx(handler, years=[2011]))
    assert result.written == ["leaguepedia/ScoreboardGames/2011"]


def test_systematic_error_aborts_quickly(make_ctx, monkeypatch):
    """Um erro que se repete em toda consulta não pode consumir horas: a fonte é abortada."""
    calls = {"query": 0}

    def handler(url, params):
        if params.get("action") == "cargofields":
            return FakeResponse(json={"cargofields": {"GameId": {}, "DateTime_UTC": {}}})
        calls["query"] += 1
        return FakeResponse(json={"error": {"code": "cargoquery-invalidfieldalias", "info": "bad alias"}})

    monkeypatch.setattr(lp, "TABLES", [lp.TableSpec("ScoreboardGames", "SG", date_field="SG.DateTime_UTC")])
    result = lp.collect(make_ctx(handler, full=True))
    assert calls["query"] == lp.MAX_CONSECUTIVE_FAILURES  # uma tentativa por partição, sem trocar de ordenação
    assert result.status == "failed" and "abortado" in result.errors[-1]


def test_missing_date_field_falls_back_to_single_partition(make_ctx, monkeypatch):
    def handler(url, params):
        if params.get("action") == "cargofields":
            return FakeResponse(json={"cargofields": {"Player": {}, "Team": {}}})  # sem Date_Sort
        assert "where" not in params
        return FakeResponse(json={"cargoquery": [{"title": {"Player": "Faker", "Team": "T1", "wiki page": "x"}}]})

    monkeypatch.setattr(lp, "TABLES", [lp.TableSpec("RosterChanges", "RC", date_field="RC.Date_Sort")])
    result = lp.collect(make_ctx(handler))
    assert result.written == ["leaguepedia/RosterChanges/all"]
    assert "Date_Sort" in result.errors[0]


def test_backfill_only_skips_existing_and_current_year(make_ctx, monkeypatch):
    """Na continuação da carga histórica, nada que já existe é rebaixado."""
    import pandas as pd
    from lolpred.storage import write_dataset
    year = now_utc().year
    write_dataset(pd.DataFrame({"x": [1]}), "leaguepedia", "Teams", "all")
    write_dataset(pd.DataFrame({"x": [1]}), "leaguepedia", "ScoreboardGames", str(year))
    queried = []

    def handler(url, params):
        if params.get("action") == "cargofields":
            return FakeResponse(json={"cargofields": {"GameId": {}, "DateTime_UTC": {}}})
        queried.append(params.get("where", "all"))
        return FakeResponse(json={"cargoquery": []})

    monkeypatch.setattr(lp, "TABLES", [lp.TableSpec("Teams", "TE"),
                                       lp.TableSpec("ScoreboardGames", "SG", date_field="SG.DateTime_UTC")])
    result = lp.collect(make_ctx(handler, backfill_only=True))
    assert "leaguepedia/Teams/all" in result.skipped
    assert "leaguepedia/ScoreboardGames/%d" % year in result.skipped
    assert len(queried) == year - lp.FIRST_YEAR  # só os anos que faltam


def test_daily_refresh_merges_only_recent_weeks(make_ctx, monkeypatch):
    """No dia a dia, o ano corrente vem só das últimas semanas e é mesclado ao arquivo do ano."""
    import pandas as pd
    from datetime import timedelta
    from lolpred.storage import dataset_path, write_dataset
    monkeypatch.setattr(lp, "full_refresh_day", lambda: False)
    year = now_utc().year
    since = lp.recent_since(year)
    old_day = f"{year}-01-01 10:00:00"
    recent_day = (now_utc() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    write_dataset(pd.DataFrame({"GameId": ["antigo", "recente-velho"], "DateTime_UTC": [old_day, recent_day],
                                "wiki_page": ["p", "p"]}), "leaguepedia", "ScoreboardGames", str(year))
    wheres = []

    def handler(url, params):
        if params.get("action") == "cargofields":
            return FakeResponse(json={"cargofields": {"GameId": {}, "DateTime_UTC": {}}})
        wheres.append(params.get("where"))
        rows = [{"GameId": "recente-novo", "DateTime UTC": recent_day, "wiki page": "p"}]
        return FakeResponse(json={"cargoquery": [{"title": r} for r in rows]})

    monkeypatch.setattr(lp, "TABLES", [lp.TableSpec("ScoreboardGames", "SG", date_field="SG.DateTime_UTC")])
    lp.collect(make_ctx(handler))
    # os anos que faltam vêm inteiros; o ano corrente, que já existe, só das últimas semanas
    assert f"SG.DateTime_UTC >= '{since}' AND SG.DateTime_UTC < '{year + 1}-01-01'" in wheres
    assert lp.year_where(lp.TABLES[0], year) not in wheres
    df = pd.read_parquet(dataset_path("leaguepedia", "ScoreboardGames", str(year)))
    # a linha antiga fica; a recente é substituída pela versão nova
    assert sorted(df["GameId"]) == (["recente-novo"] if old_day >= since else ["antigo", "recente-novo"])


def test_reference_tables_refresh_weekly(make_ctx, monkeypatch):
    import pandas as pd
    from lolpred.storage import write_dataset
    for table in ("Teams", "Tournaments"):
        write_dataset(pd.DataFrame({"x": [1]}), "leaguepedia", table, "all")

    def handler(url, params):
        if params.get("action") == "cargofields":
            return FakeResponse(json={"cargofields": {"Name": {}}})
        return FakeResponse(json={"cargoquery": [{"title": {"Name": "a", "wiki page": "p"}}]})

    monkeypatch.setattr(lp, "TABLES", [lp.TableSpec("Teams", "TE"), lp.TableSpec("Tournaments", "T")])
    monkeypatch.setattr(lp, "full_refresh_day", lambda: False)
    result = lp.collect(make_ctx(handler))
    assert "leaguepedia/Teams/all" in result.skipped and result.written == ["leaguepedia/Tournaments/all"]
    monkeypatch.setattr(lp, "full_refresh_day", lambda: True)  # segunda-feira
    result = lp.collect(make_ctx(handler))
    assert "leaguepedia/Teams/all" not in result.skipped


def test_ratelimit_slows_the_pace(make_ctx, monkeypatch):
    state = {"n": 0}

    def handler(url, params):
        if params.get("action") == "cargofields":
            return FakeResponse(json={"cargofields": {"Name": {}}})
        state["n"] += 1
        if state["n"] <= 2:
            return FakeResponse(json={"error": {"code": "ratelimited", "info": "slow down"}})
        return FakeResponse(json={"cargoquery": [{"title": {"Name": "a", "wiki page": "p"}}]})

    monkeypatch.setattr(lp, "TABLES", [lp.TableSpec("Leagues", "L")])
    result = lp.collect(make_ctx(handler))
    assert result.stats["rate_limit_pauses"] == 2
    assert result.stats["final_interval_s"] == round((lp.ANON_INTERVAL * 1.5 + 0.5) * 1.5 + 0.5, 2)
