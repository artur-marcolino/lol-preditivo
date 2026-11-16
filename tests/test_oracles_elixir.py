from conftest import FakeResponse

from lolpred.context import now_utc
from lolpred.sources import oracles_elixir as oe
from lolpred.storage import load_manifest

LISTING = """
<div class="flip-entry" id="entry-AAA111bbb222ccc333ddd444" tabindex="0">
  <div class="flip-entry-info"><div class="flip-entry-title">2024_LoL_esports_match_data_from_OraclesElixir.csv</div></div>
</div>
<div class="flip-entry" id="entry-ZZZ999yyy888xxx777www666" tabindex="0">
  <div class="flip-entry-info"><div class="flip-entry-title">2014_LoL_esports_match_data_from_OraclesElixir.csv</div></div>
</div>
<div class="flip-entry" id="entry-README0000000000000000" tabindex="0">
  <div class="flip-entry-info"><div class="flip-entry-title">readme.txt</div></div>
</div>
"""
CSV = b"gameid,datacompleteness,league,date,position,result\nG1,complete,LCK,2024-01-17 08:00:00,team,1\nG1,complete,LCK,2024-01-17 08:00:00,team,0\n"
CONFIRM = """<html><title>Google Drive - Virus scan warning</title>
<form id="download-form" action="https://drive.usercontent.google.com/download" method="get">
<input type="hidden" name="id" value="AAA111bbb222ccc333ddd444"><input type="hidden" name="confirm" value="t">
<input type="hidden" name="uuid" value="abc"></form></html>"""


def test_parse_listing():
    files = oe.parse_folder_listing(LISTING)
    assert files["2024_LoL_esports_match_data_from_OraclesElixir.csv"] == "AAA111bbb222ccc333ddd444"
    by_year = oe.files_by_year(files)
    assert set(by_year) == {2014, 2024}


def test_collect_handles_confirm_page_and_quota(make_ctx):
    year = now_utc().year

    def handler(url, params):
        if "embeddedfolderview" in url:
            return FakeResponse(text=LISTING.replace("2024_", f"{year}_"))
        is_current = params.get("id") == "AAA111bbb222ccc333ddd444"
        if "usercontent" in url and is_current and "uuid" not in params:
            return FakeResponse(text=CONFIRM)  # 1ª tentativa: página de confirmação
        if "usercontent" in url and is_current:
            return FakeResponse(content=CSV)
        return FakeResponse(text="<html><title>Quota exceeded</title></html>")

    ctx = make_ctx(handler, years=[2014, year])
    result = oe.collect(ctx)
    assert result.written == [f"oracles_elixir/matches/{year}"]
    assert any("2014" in e for e in result.errors)  # 2014 só recebeu "Quota exceeded"
    assert result.status == "partial"
    meta = load_manifest("oracles_elixir")["datasets"][f"oracles_elixir/matches/{year}"]
    assert meta["rows"] == 2 and meta["drive_file_id"] == "AAA111bbb222ccc333ddd444"


def test_cold_years_are_not_redownloaded(make_ctx, tmp_data):
    import pandas as pd
    from lolpred.storage import write_dataset
    write_dataset(pd.DataFrame({"gameid": ["x"]}), "oracles_elixir", "matches", "2015")
    calls = []

    def handler(url, params):
        calls.append(url)
        return FakeResponse(status_code=404, text="nope")

    result = oe.collect(make_ctx(handler))
    assert "oracles_elixir/matches/2015" in result.skipped
