"""Testes offline das fontes novas: lolwiki, meraki, standings do lolesports, pandascore, oddspapi."""

import json

import pandas as pd
from conftest import FakeResponse

from lolpred.sources import lolesports, lolwiki, meraki, oddspapi, pandascore
from lolpred.storage import dataset_path, load_manifest

WIKI_MODULE = '''-- <pre>
return {
  ["Aatrox"] = { ["id"] = 266, ["apiname"] = "Aatrox", ["title"] = "the Darkin Blade",
    ["role"] = {"Juggernaut"}, ["client_positions"] = {"Top"}, ["date"] = "2013-06-13",
    ["patch"] = "V3.8", ["changes"] = "V26.09", ["rangetype"] = "Melee", ["damage"] = 3,
    ["stats"] = { ["hp_base"] = 650, ["hp_lvl"] = 114, ["aram"] = { ["dmg_dealt"] = 1.05 } } },
  ["Ahri"] = { ["id"] = 103, ["role"] = {"Burst"}, ["changes"] = "V25.20" },
}'''


def test_lolwiki_history_only_grows_on_change(make_ctx):
    module = {"text": WIKI_MODULE}

    def handler(url, params):
        assert url.endswith("api.php") and params["titles"] == "Module:ChampionData/data"
        return FakeResponse(json={"query": {"pages": [{"revisions": [{"slots": {"main": {"content": module["text"]}}}]}]}})

    lolwiki.collect(make_ctx(handler))
    champs = pd.read_parquet(dataset_path("lolwiki", "champions"))
    a = champs.set_index("champion").loc["Aatrox"]
    assert a["role"] == "Juggernaut" and a["stat_hp_base"] == 650 and "aram" in a["raw_json"]
    assert len(pd.read_parquet(dataset_path("lolwiki", "champions_history"))) == 2

    lolwiki.collect(make_ctx(handler))  # nada mudou
    assert len(pd.read_parquet(dataset_path("lolwiki", "champions_history"))) == 2

    module["text"] = WIKI_MODULE.replace('"V25.20"', '"V26.10"')  # Ahri alterada num patch novo
    lolwiki.collect(make_ctx(handler))
    hist = pd.read_parquet(dataset_path("lolwiki", "champions_history"))
    assert len(hist) == 3 and list(hist[hist.champion == "Ahri"]["changes"]) == ["V25.20", "V26.10"]


def test_meraki_flattening(make_ctx):
    champs = {"Ahri": {"id": 103, "name": "Ahri", "resource": "MANA", "attackType": "RANGED",
                       "adaptiveType": "MAGIC_DAMAGE", "positions": ["MIDDLE"], "roles": ["BURST", "MAGE"],
                       "attributeRatings": {"damage": 3, "mobility": 3}, "releaseDate": "2011-12-14",
                       "patchLastChanged": "26.10", "price": {"blueEssence": 3150, "rp": 790},
                       "stats": {"health": {"flat": 590, "perLevel": 104}}, "skins": [{"huge": True}], "lore": "x"}}
    items = {"1001": {"id": 1001, "name": "Boots", "tier": 1, "removed": False,
                      "shop": {"prices": {"total": 300}, "purchasable": True, "tags": ["BOOTS"]},
                      "stats": {"movespeed": {"flat": 25, "percent": 0}}}}

    def handler(url, params):
        return FakeResponse(json=champs if url.endswith("champions.json") else items)

    result = meraki.collect(make_ctx(handler))
    assert result.status == "ok", result.errors
    c = pd.read_parquet(dataset_path("meraki", "champions")).iloc[0]
    assert c["roles"] == "BURST,MAGE" and c["rating_damage"] == 3 and c["stat_health_flat"] == 590
    assert "skins" not in json.loads(c["raw_json"])
    i = pd.read_parquet(dataset_path("meraki", "items")).iloc[0]
    assert i["price_total"] == 300 and i["stat_movespeed_flat"] == 25


STANDINGS = {"standings": [{"stages": [{"name": "Regular Season", "type": "groups", "slug": "regular_season",
    "sections": [{"name": "Regular Season",
                  "rankings": [{"ordinal": 1, "teams": [{"id": "T1", "code": "T1", "name": "T1",
                                                          "record": {"wins": 15, "losses": 3}}]}],
                  "matches": [{"id": "M1", "state": "completed", "previousMatchIds": [],
                               "teams": [{"id": "T1", "code": "T1", "result": {"outcome": "win", "gameWins": 2}},
                                         {"id": "GEN", "code": "GEN", "result": {"outcome": "loss", "gameWins": 1}}]}]}]}]}]}


def test_flatten_standings():
    rankings, matches = lolesports.flatten_standings("TOUR", STANDINGS)
    assert rankings[0]["ordinal"] == 1 and rankings[0]["wins"] == 15 and rankings[0]["stage_name"] == "Regular Season"
    assert matches[0]["match_id"] == "M1" and matches[0]["team2_game_wins"] == 1


def test_collect_standings_incremental(make_ctx):
    tournaments = pd.DataFrame([
        {"tournament_id": "OLD", "start_date": "2020-01-01", "end_date": "2020-03-01"},
        {"tournament_id": "FUT", "start_date": "2099-01-01", "end_date": "2099-03-01"},
    ])

    def handler(url, params):
        return FakeResponse(json={"data": STANDINGS})

    ctx = make_ctx(handler)
    result = lolesports.SourceResult()
    lolesports.collect_standings(lolesports.Api(ctx.session), ctx, tournaments, result)
    assert [p["tournamentId"] for _, p in ctx.session.calls] == ["OLD"]  # o futuro ainda não começou
    ctx2 = make_ctx(handler)
    lolesports.collect_standings(lolesports.Api(ctx2.session), ctx2, tournaments, lolesports.SourceResult())
    assert ctx2.session.calls == []  # torneio antigo já coletado não é revisitado
    assert len(pd.read_parquet(dataset_path("lolesports", "standings_rankings"))) == 1


def _match(mid, begin):
    return {"id": mid, "name": "A vs B", "status": "finished", "begin_at": begin, "winner_id": 1,
            "opponents": [{"opponent": {"id": 1, "name": "A", "acronym": "A"}},
                          {"opponent": {"id": 2, "name": "B", "acronym": "B"}}],
            "results": [{"team_id": 1, "score": 2}, {"team_id": 2, "score": 1}], "streams_list": ["big"]}


def test_pandascore_backfill(make_ctx, monkeypatch):
    monkeypatch.setenv("PANDASCORE_TOKEN", "x")
    monkeypatch.setattr(pandascore, "PAGE", 2)
    history = [_match(i, f"2026-0{1 + (5 - i) // 2}-1{i}T10:00:00Z") for i in range(5, 0, -1)]  # 5 = mais recente

    def handler(url, params):
        if url.endswith("/lol/matches/past"):
            items = history
            if "range[begin_at]" in params:
                cut = params["range[begin_at]"].split(",")[1]
                items = [m for m in history if m["begin_at"] < cut]
            n = params["page[number]"]
            return FakeResponse(json=items[(n - 1) * 2: n * 2])
        if url.endswith("/lol/matches/upcoming"):
            return FakeResponse(json=[])
        if url.endswith("/lol/teams"):
            return FakeResponse(json=[{"id": 1, "name": "A", "players": [{"id": 9, "name": "p", "role": "mid"}]}])
        return FakeResponse(json=[{"id": 1, "name": "x", "tier": "s", "birthday": "2000-01-01",
                                   "current_team": {"id": 1}}])

    result = pandascore.collect(make_ctx(handler))
    assert result.status == "ok", result.errors
    past = pd.read_parquet(dataset_path("pandascore", "matches_past"))
    assert sorted(past["id"]) == [1, 2, 3, 4, 5] and past.set_index("id").loc[5, "team1_score"] == 2
    assert "streams_list" not in json.loads(past.iloc[0]["raw_json"])
    assert pd.read_parquet(dataset_path("pandascore", "players")).iloc[0]["current_team_id"] == 1


def test_oddspapi_respects_budget(make_ctx, monkeypatch):
    monkeypatch.setenv("ODDSPAPI_KEY", "k")
    monkeypatch.setenv("ODDSPAPI_PER_RUN", "3")
    fixtures = [{"fixtureId": f"f{i}", "startTime": "2020-01-0%dT10:00:00Z" % (i + 1),
                 "tournamentName": "LCK" if i == 3 else "Tier 3 Cup", "participant1Name": "A",
                 "participant2Name": "B"} for i in range(5)]

    def handler(url, params):
        assert params["apiKey"] == "k"
        if url.endswith("/fixtures"):
            return FakeResponse(json=fixtures)
        return FakeResponse(json={"fixtureId": params["fixtureId"], "bookmakers": {}})

    ctx = make_ctx(handler)
    result = oddspapi.collect(ctx)
    assert result.status == "ok", result.errors
    hist = pd.read_parquet(dataset_path("oddspapi", "historical_odds_raw"))
    assert len(hist) == 2 and hist.iloc[0]["fixtureId"] == "f3"  # 1 chamada de fixtures + 2 de odds; LCK primeiro
    month_key = [k for k in load_manifest("oddspapi")["datasets"] if k.startswith("oddspapi/calls/")]
    assert len(pd.read_parquet(dataset_path("oddspapi", "calls", month_key[0].split("/")[-1]))) == 3


def test_optional_sources_without_key_fail_clearly(make_ctx, monkeypatch):
    monkeypatch.delenv("PANDASCORE_TOKEN", raising=False)
    monkeypatch.delenv("ODDSPAPI_KEY", raising=False)
    no_calls = make_ctx(lambda u, p: (_ for _ in ()).throw(AssertionError("não devia chamar")))
    assert "PANDASCORE_TOKEN" in pandascore.collect(no_calls).errors[0]
    assert "ODDSPAPI_KEY" in oddspapi.collect(no_calls).errors[0]


def test_lolwiki_falls_back_to_raw_and_reports_failures(make_ctx):
    def handler(url, params):
        if url.endswith("api.php"):
            return FakeResponse(status_code=403, text="<html>Just a moment...</html>")
        return FakeResponse(text=WIKI_MODULE)

    result = lolwiki.collect(make_ctx(handler))
    assert result.status == "ok" and "lolwiki/champions/all" in result.written

    blocked = lolwiki.collect(make_ctx(lambda u, p: FakeResponse(status_code=403, text="<html>Just a moment...</html>")))
    assert blocked.status == "failed" and "Just a moment" in blocked.errors[0]
