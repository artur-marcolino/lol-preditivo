"""Testes offline das fontes de jogadores: GCD (Riot e arquivo da Leaguepedia), PandaSkill,
EsportsBench e Esports Earnings."""

import io
import json

import pandas as pd
from conftest import FakeResponse

from lolpred.sources import esports_earnings, esportsbench, leaguepedia_gcd, pandaskill, riot_gcd
from lolpred.storage import dataset_path, load_manifest

# ---------------------------------------------------------------- GCD da Riot (planilha)

PUBHTML = '''<script>items.push({name: "INFO", pageUrl: "x", gid: "940656680"});
items.push({name: "AME", pageUrl: "x", gid: "0"});items.push({name: "CN", pageUrl: "x", gid: "594163931"});</script>'''
AME_CSV = (
    'LAST UPDATE:,"Oct 27, 2026 11:52:20 PT",5,Blue - Reserve Roster,,\r\n'
    'League,Team,Official Summoner Name,Position,Legal Given Name,Legal Family Name,'
    '"End Date (Month, Day, Year)",Residency,Status,QuadCode,Team Contact\r\n'
    'LCS TEAMS,,,,,,,,,,\r\n'
    'LCS,Cloud9,Thanatos,Top,Seung-gyu,Park,"November 20, 2028",Non-Resident,Active,C9,Fulana <f@c9.gg>\r\n'
    'LCS,Cloud9,Blaber,Jungle,Robert,Huang,"November 20, 2028",NA Resident,Active,,\r\n'
)
CN_CSV = ('THIS SHEET WAS LAST UPDATED ON:,2026/11/12 23:19,,\r\n'
          'League,Team,Official Summoner Name,Main Role,Legal First Name,Legal Family Name,"End Date (Month, Day, Year)",Status\r\n'
          'LPL,Bilibili Gaming,Bin,Top,X,Y,"November 30, 2027",Active\r\n')


def _gcd_handler(sheets):
    def handler(url, params):
        if url.endswith("pubhtml"):
            return FakeResponse(text=PUBHTML)
        gid = url.split("gid=")[1].split("&")[0]
        return FakeResponse(text=sheets[gid])
    return handler


def test_riot_gcd_parses_tabs_drops_private_fields_and_tracks_history(make_ctx):
    sheets = {"0": AME_CSV, "594163931": CN_CSV}
    result = riot_gcd.collect(make_ctx(_gcd_handler(sheets)))
    assert result.errors == []
    c = pd.read_parquet(dataset_path("riot_gcd", "contracts"))
    assert len(c) == 3 and set(c["region"]) == {"AME", "CN"}
    assert not any("legal" in col.lower() or "contact" in col.lower() for col in c.columns)
    thanatos = c.set_index("summoner").loc["Thanatos"]
    assert thanatos["end_date"] == "2028-11-20" and thanatos["position"] == "Top" and thanatos["quadcode"] == "C9"
    assert c.set_index("summoner").loc["Bin", "position"] == "Top"  # "Main Role" na aba CN
    assert c.set_index("summoner").loc["Thanatos", "sheet_last_update"].startswith("Oct 27, 2026")

    riot_gcd.collect(make_ctx(_gcd_handler(sheets)))  # nada mudou
    assert len(pd.read_parquet(dataset_path("riot_gcd", "contracts_history"))) == 3

    sheets["0"] = AME_CSV.replace('"November 20, 2028",NA Resident', '"November 20, 2029",NA Resident')
    riot_gcd.collect(make_ctx(_gcd_handler(sheets)))  # Blaber renovou
    hist = pd.read_parquet(dataset_path("riot_gcd", "contracts_history"))
    assert list(hist[hist.summoner == "Blaber"]["end_date"]) == ["2028-11-20", "2029-11-20"]


# ---------------------------------------------------------------- arquivo do GCD na Leaguepedia

PAGE_2019 = """<noinclude>{{GCDBackup|lastupdateddate=2019-06-02}}</noinclude>
{|
|-
! League !! Team !! Official Summoner Name !! Legal Family Name !! Legal First Name !! End Date (Month, Day, Year) !!  !! Team Contact Information
|-
| LCK || Hanwha Life Esports || Key || Kim || Hangi || November 18, 2019 ||  || x@hle.com || HLE
|-
| LCK || style="background:#ccc" | [[Hanwha Life Esports|HLE]] || '''Lava''' || Kim || Taehun || November 15, 2021 ||  || x@hle.com
|}"""
PAGE_2026 = """{|
|-
! League !! Team !! Official Summoner Name !! Position !! End Date (Month, Day, Year) !! Residency !! Status
|-
| LCS || Cloud9 || Thanatos || Top || November 20, 2028 || Non-Resident || Active
|}"""


def _lp_handler(contents, calls):
    titles = list(contents)

    def handler(url, params):
        calls.append(params)
        if params.get("meta") == "siteinfo":
            return FakeResponse(json={"query": {"namespaces": {"0": {"*": ""}, "10004": {"*": "Archive"}}}})
        if params.get("list") == "allpages":
            pages = [{"title": t.replace("_", " ")} for t in titles]
            pages.append({"title": "Archive:Global Contract Database/KR/Current"})  # ignorada
            return FakeResponse(json={"query": {"allpages": pages}})
        if params.get("prop") == "revisions":
            asked = params["titles"].split("|")
            return FakeResponse(json={"query": {"pages": [
                {"title": t.replace("_", " "), "revisions": [{"slots": {"main": {"content": contents[t.replace(" ", "_")]}}}]}
                for t in asked]}})
        raise AssertionError(params)
    return handler


def test_leaguepedia_gcd_parses_archive_and_is_incremental(make_ctx):
    contents = {"Archive:Global_Contract_Database/KR/2019-06-01": PAGE_2019,
                "Archive:Global_Contract_Database/AME/2026-11-15": PAGE_2026}
    calls = []
    result = leaguepedia_gcd.collect(make_ctx(_lp_handler(contents, calls)))
    assert result.errors == []
    s19 = pd.read_parquet(dataset_path("leaguepedia_gcd", "snapshots", "2019")).sort_values("summoner")
    assert list(s19["summoner"]) == ["Key", "Lava"]
    assert list(s19["team"]) == ["Hanwha Life Esports", "HLE"]  # atributo de célula e link limpos
    assert list(s19["end_date"]) == ["2019-11-18", "2021-11-15"]
    assert set(s19["region"]) == {"KR"} and set(s19["snapshot_date"]) == {"2019-06-01"}
    assert not any("legal" in c.lower() or "contact" in c.lower() for c in s19.columns)
    s26 = pd.read_parquet(dataset_path("leaguepedia_gcd", "snapshots", "2026"))
    assert s26.iloc[0]["status"] == "Active" and s26.iloc[0]["position"] == "Top"

    calls.clear()
    leaguepedia_gcd.collect(make_ctx(_lp_handler(contents, calls)))  # nada novo: não baixa conteúdo
    assert not any(c.get("prop") == "revisions" for c in calls)


# ---------------------------------------------------------------- PandaSkill

POINTER = "version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize 123\n"


def test_pandaskill_downloads_only_when_lfs_pointer_changes(make_ctx):
    oids = {path: "a" * 64 for path in pandaskill.FILES.values()}
    downloads = []

    def handler(url, params):
        path = url.split("artifacts/data/")[1]
        if "raw.githubusercontent" in url:
            return FakeResponse(text=POINTER.format(oid=oids[path]))
        downloads.append(path)
        return FakeResponse(text="game_id,date,player_id,kills\n1,2020-01-01,10,3\n")

    pandaskill.collect(make_ctx(handler))
    assert len(downloads) == len(pandaskill.FILES)
    meta = pd.read_parquet(dataset_path("pandaskill", "game_metadata"))
    assert list(meta.columns) == ["game_id", "date", "player_id", "kills"]

    downloads.clear()
    result = pandaskill.collect(make_ctx(handler))  # mesmos ponteiros: nada a baixar
    assert downloads == [] and len(result.skipped) == len(pandaskill.FILES)

    oids["raw/game_metadata.csv"] = "b" * 64
    pandaskill.collect(make_ctx(handler))
    assert downloads == ["raw/game_metadata.csv"]


# ---------------------------------------------------------------- EsportsBench

def test_esportsbench_downloads_lol_split_and_skips_same_version(make_ctx):
    buf = io.BytesIO()
    pd.DataFrame({"date": ["2011-04-09"], "competitor_1": ["TSM"], "competitor_2": ["OGODBEARS"],
                  "outcome": [1.0], "match_id": ["m1"], "page": ["p"]}).to_parquet(buf)
    sha = {"v": "abc"}
    gets = []

    def handler(url, params):
        gets.append(url)
        if url.endswith("/parquet"):
            return FakeResponse(json={"default": {"league_of_legends": ["https://hf/x/lol.parquet"],
                                                  "dota2": ["https://hf/x/dota.parquet"]}})
        if url.endswith(".parquet"):
            return FakeResponse(content=buf.getvalue())
        return FakeResponse(json={"sha": sha["v"], "siblings": []})

    esportsbench.collect(make_ctx(handler))
    df = pd.read_parquet(dataset_path("esportsbench", "matches"))
    assert len(df) == 1 and df.iloc[0]["competitor_1"] == "TSM"
    assert not any("dota" in u for u in gets)
    assert load_manifest("esportsbench")["datasets"]["esportsbench/matches/all"]["hf_sha"] == "abc"

    gets.clear()
    esportsbench.collect(make_ctx(handler))
    assert not any(u.endswith(".parquet") for u in gets)


# ---------------------------------------------------------------- Esports Earnings

def test_esports_earnings_keeps_lol_and_fetches_results_once(make_ctx, monkeypatch):
    monkeypatch.setenv("ESPORTSEARNINGS_KEY", "chave-de-teste")
    recent = [
        {"TournamentId": 1, "GameId": 164, "TournamentName": "LoL Worlds", "EndDate": "2026-11-14", "Teamplay": 1},
        {"TournamentId": 2, "GameId": 245, "TournamentName": "Dota", "EndDate": "2026-11-10", "Teamplay": 1},
        {"TournamentId": 3, "GameId": 164, "TournamentName": "Solo 1v1", "EndDate": "2015-01-01", "Teamplay": 0},
    ]
    calls = []

    def handler(url, params):
        method = url.rsplit("/", 1)[1]
        calls.append((method, params.get("tournamentid"), params.get("offset")))
        assert params["apikey"] == "chave-de-teste"
        if method == "LookupRecentTournaments":
            return FakeResponse(text=json.dumps(recent) if params.get("offset", 0) == 0 else "")
        if method == "LookupTournamentTeamResultsByTournamentId":
            return FakeResponse(json=[{"Ranking": 1, "TeamId": 9, "TeamName": "T1", "PrizeUSD": 1000}])
        if method == "LookupTournamentTeamPlayersByTournamentId":
            return FakeResponse(json=[{"TournamentTeamId": 5, "PlayerId": 7, "CurrentHandle": "Faker"}])
        if method == "LookupTournamentResultsByTournamentId":
            return FakeResponse(json={"ErrorCode": 1001})
        raise AssertionError(method)

    result = esports_earnings.collect(make_ctx(handler))
    t = pd.read_parquet(dataset_path("esports_earnings", "tournaments"))
    assert set(t["TournamentId"].astype(str)) == {"1", "3"}  # só LoL
    tr = pd.read_parquet(dataset_path("esports_earnings", "team_results"))
    assert tr.iloc[0]["TeamName"] == "T1" and tr.iloc[0]["TournamentId"] == "1"
    assert pd.read_parquet(dataset_path("esports_earnings", "team_players")).iloc[0]["CurrentHandle"] == "Faker"
    assert any("torneio 3" in e for e in result.errors)

    calls.clear()
    esports_earnings.collect(make_ctx(handler))
    # torneio 1 terminou há pouco: recoletado; torneio 3 deu erro antes: tentado de novo
    asked = {c[1] for c in calls if c[1]}
    assert asked == {"1", "3"}


def test_esports_earnings_without_key_reports_clearly(make_ctx, monkeypatch):
    monkeypatch.delenv("ESPORTSEARNINGS_KEY", raising=False)
    result = esports_earnings.collect(make_ctx(lambda u, p: FakeResponse(json={})))
    assert result.status == "failed" and "ESPORTSEARNINGS_KEY" in result.errors[0]
