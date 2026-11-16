import pandas as pd

from lolpred import catalog, db
from lolpred.context import hot_years
from lolpred.storage import dataset_path, load_manifest, write_dataset
from datetime import datetime, timezone


def test_write_dataset_skips_identical_content():
    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"], "date": ["2024-01-01", "2024-02-01"]})
    assert write_dataset(df, "src", "tab", "2024", date_col="date") is True
    assert write_dataset(df, "src", "tab", "2024", date_col="date") is False
    meta = load_manifest("src")["datasets"]["src/tab/2024"]
    assert meta["rows"] == 2 and meta["date_min"].startswith("2024-01-01")
    df2 = pd.concat([df, df.iloc[:1]])
    assert write_dataset(df2, "src", "tab", "2024") is True
    assert pd.read_parquet(dataset_path("src", "tab", "2024")).shape[0] == 3


def test_mixed_types_are_saved_as_string():
    df = pd.DataFrame({"mixed": [1, "a", None]})
    write_dataset(df, "src", "mixed")
    out = pd.read_parquet(dataset_path("src", "mixed"))
    assert list(out["mixed"].astype(object).where(out["mixed"].notna(), None)) == ["1", "a", None]


def test_catalog_and_duckdb(tmp_data):
    write_dataset(pd.DataFrame({"x": [1]}), "oracles_elixir", "matches", "2023")
    write_dataset(pd.DataFrame({"x": [2], "y": [3]}), "oracles_elixir", "matches", "2024")
    text = catalog.render()
    assert "`oe_matches`" in text and "| 2 |" in text
    names = db.build()
    assert names == ["oe_matches"]
    import duckdb
    con = duckdb.connect(str(tmp_data / "lol.duckdb"))
    assert con.execute("select count(*), count(y) from oe_matches").fetchone() == (2, 1)


def test_hot_years():
    assert hot_years(datetime(2026, 11, 1, tzinfo=timezone.utc)) == {2026}
    assert hot_years(datetime(2026, 1, 15, tzinfo=timezone.utc)) == {2025, 2026}


def test_reconcile_registers_untracked_files(tmp_data):
    import pyarrow as pa, pyarrow.parquet as pq
    from lolpred.storage import reconcile
    path = tmp_data / "processed" / "oracles_elixir" / "matches" / "2019.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(pa.table({"gameid": ["a", "b"], "date": ["2019-01-01", "2019-06-01"]}), path)
    assert reconcile(mark_published=True) == ["oracles_elixir/matches/2019"]
    m = load_manifest("oracles_elixir")
    assert m["datasets"]["oracles_elixir/matches/2019"]["rows"] == 2
    assert m["published"]["oracles_elixir/matches/2019"] == m["datasets"]["oracles_elixir/matches/2019"]["fingerprint"]
    assert reconcile() == []
