import pandas as pd
from conftest import FakeResponse

from lolpred.sources import ddragon, lolesports as les
from lolpred.storage import dataset_path


def _event(mid, state="completed", start="2026-03-01T08:00:00Z"):
    return {"startTime": start, "state": state, "type": "match", "blockName": "Week 1",
            "league": {"name": "LCK", "slug": "lck"},
            "match": {"id": mid, "strategy": {"type": "bestOf", "count": 3},
                      "teams": [{"name": "T1", "code": "T1", "result": {"outcome": "win", "gameWins": 2}, "record": {"wins": 1, "losses": 0}},
                                {"name": "Gen.G", "code": "GEN", "result": {"outcome": "loss", "gameWins": 1}, "record": {"wins": 0, "losses": 1}}]}}


def test_lolesports_collect(make_ctx):
    def handler(url, params):
        ep = url.rsplit("/", 1)[-1]
        if ep == "getLeagues":
            return FakeResponse(json={"data": {"leagues": [{"id": "L1", "slug": "lck", "name": "LCK", "region": "KOREA"}]}})
        if ep == "getTournamentsForLeague":
            return FakeResponse(json={"data": {"leagues": [{"tournaments": [{"id": "T9", "slug": "lck_2026", "startDate": "2026-01-10", "endDate": "2026-04-01"}]}]}})
        if ep == "getSchedule":
            token = params.get("pageToken")
            if token is None:
                return FakeResponse(json={"data": {"schedule": {"pages": {"older": "o1", "newer": "n1"}, "events": [_event("M2")]}}})
            if token == "o1":
                return FakeResponse(json={"data": {"schedule": {"pages": {"older": None}, "events": [_event("M1", start="2026-02-01T08:00:00Z")]}}})
            return FakeResponse(json={"data": {"schedule": {"pages": {"newer": None}, "events": [_event("M3", state="unstarted", start="2026-12-01T08:00:00Z")]}}})
        if ep == "getEventDetails":
            return FakeResponse(json={"data": {"event": {"league": {"slug": "lck"}, "tournament": {"id": "T9"}, "match": {
                "teams": [{"id": "A", "code": "T1"}, {"id": "B", "code": "GEN"}],
                "games": [{"id": params["id"] + "-g1", "number": 1, "state": "completed",
                           "teams": [{"id": "A", "side": "blue"}, {"id": "B", "side": "red"}]}]}}}})
        if ep == "getTeams":
            return FakeResponse(json={"data": {"teams": [{"id": "A", "code": "T1", "players": [{"id": "p1", "summonerName": "Faker", "role": "mid"}]}]}})
        raise AssertionError(url)

    result = les.collect(make_ctx(handler))
    assert result.status == "ok", result.errors
    sched = pd.read_parquet(dataset_path("lolesports", "schedule"))
    assert list(sched["match_id"]) == ["M1", "M2", "M3"]
    games = pd.read_parquet(dataset_path("lolesports", "match_games"))
    assert set(games["match_id"]) == {"M1", "M2"} and games["blue_team_code"].iloc[0] == "T1"

    # segunda execução: nenhum detalhe novo para buscar
    ctx2 = make_ctx(handler)
    les.collect(ctx2)
    assert not any(u.endswith("getEventDetails") for u, _ in ctx2.session.calls)


def test_ddragon_latest_per_patch():
    out = ddragon.latest_per_patch(["14.10.1", "14.10.2", "14.9.1", "lolpatch_7.20", "0.151.2"])
    assert out["14.10"] == "14.10.2" and "lolpatch_7.20" not in out.values()


def test_ddragon_collect_incremental(make_ctx):
    def handler(url, params):
        if url.endswith("versions.json"):
            return FakeResponse(json=["15.2.1", "15.1.1"])
        if url.endswith("champion.json"):
            return FakeResponse(json={"data": {"Ahri": {"id": "Ahri", "key": "103", "name": "Ahri", "tags": ["Mage", "Assassin"],
                                                         "info": {"attack": 3}, "stats": {"hp": 590}}}})
        return FakeResponse(json={"data": {"1001": {"name": "Boots", "gold": {"total": 300}, "stats": {}}}})

    ddragon.collect(make_ctx(handler))
    champs = pd.read_parquet(dataset_path("ddragon", "champions"))
    assert len(champs) == 2 and champs["tag_secondary"].iloc[0] == "Assassin"
    ctx2 = make_ctx(handler)
    ddragon.collect(ctx2)
    assert len(ctx2.session.calls) == 1  # só versions.json
