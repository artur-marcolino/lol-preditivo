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


QUOTA_PAGE = "<html><title>Google Drive - Quota exceeded</title></html>"


def _quota_everywhere(url, params):
    if "embeddedfolderview" in url:
        return FakeResponse(text=LISTING.replace("2024_", f"{now_utc().year}_"))
    if "amazonaws" in url:
        return FakeResponse(status_code=404, text="nope")
    return FakeResponse(text=QUOTA_PAGE)


def _seed_copy(year, downloaded_at):
    import pandas as pd
    from lolpred.storage import touch_dataset, write_dataset
    write_dataset(pd.DataFrame({"gameid": ["x"]}), "oracles_elixir", "matches", str(year))
    touch_dataset("oracles_elixir", "matches", str(year), checked_at=downloaded_at.isoformat(timespec="seconds"))


def test_quota_with_recent_copy_is_deferred_not_failed(make_ctx):
    from datetime import timedelta
    year = now_utc().year
    _seed_copy(year, now_utc() - timedelta(hours=6))
    result = oe.collect(make_ctx(_quota_everywhere, years=[year]))
    assert result.errors == []
    assert result.status == "deferred"
    assert "cota de download" in result.deferred[0]
    assert result.to_dict()["deferred"] == result.deferred


def test_quota_becomes_error_after_grace_period(make_ctx):
    from datetime import timedelta
    year = now_utc().year
    _seed_copy(year, now_utc() - timedelta(days=3))
    result = oe.collect(make_ctx(_quota_everywhere, years=[year]))
    assert result.deferred == []
    assert result.status == "failed"


def test_successful_download_records_checked_at(make_ctx):
    year = now_utc().year

    def handler(url, params):
        if "embeddedfolderview" in url:
            return FakeResponse(text=LISTING.replace("2024_", f"{year}_"))
        return FakeResponse(content=CSV)

    oe.collect(make_ctx(handler, years=[year]))
    first = load_manifest("oracles_elixir")["datasets"][f"oracles_elixir/matches/{year}"]
    result = oe.collect(make_ctx(handler, years=[year]))  # mesmo conteúdo: não regrava o arquivo
    assert result.unchanged == [f"oracles_elixir/matches/{year}"]
    again = load_manifest("oracles_elixir")["datasets"][f"oracles_elixir/matches/{year}"]
    assert "checked_at" in first and again["checked_at"] >= first["checked_at"]
    assert again["fingerprint"] == first["fingerprint"]


def test_deferred_run_is_not_marked_as_success(make_ctx, monkeypatch):
    from datetime import timedelta
    from lolpred import collect
    from lolpred.sources import SOURCES
    year = now_utc().year
    _seed_copy(year, now_utc() - timedelta(hours=1))
    fake_ctx = make_ctx(_quota_everywhere, years=[year])  # sessão falsa: nada sai para a rede
    monkeypatch.setitem(SOURCES, "oracles_elixir", lambda ctx: oe.collect(fake_ctx))
    report = collect.run(["oracles_elixir"], years=[year])
    assert report["oracles_elixir"]["status"] == "deferred"
    status = load_manifest("oracles_elixir")["status"]
    assert status["status"] == "deferred" and "last_success" not in status
    assert "cota de download" in status["message"]
