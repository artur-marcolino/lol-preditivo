import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("plan", Path(__file__).parents[1] / ".github" / "scripts" / "plan.py")
plan_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan_mod)

KNOWN = ["oracles_elixir", "leaguepedia", "lolesports", "ddragon", "pandascore", "lolwiki"]


def test_registry_parsing():
    text = Path("src/lolpred/sources/__init__.py").read_text()
    assert "oracles_elixir" in plan_mod.all_sources(text)


def test_push_runs_only_changed_collectors():
    files = ["src/lolpred/sources/lolesports.py", "src/lolpred/sql/30_feat.sql", "README.md"]
    assert plan_mod.plan("push", KNOWN, changed_files=files)[0] == ["lolesports"]
    assert plan_mod.plan("push", KNOWN, changed_files=["docs/x.md"])[0] == []


def test_commit_message_tag_overrides():
    assert plan_mod.plan("push", KNOWN, message="fix\n\n[coleta: leaguepedia ddragon]")[0] == ["leaguepedia", "ddragon"]
    assert plan_mod.plan("push", KNOWN, message="[coleta: todas]")[0] == KNOWN[:4]  # sem pandascore e lolwiki
    assert plan_mod.plan("push", KNOWN, message="[coleta: lolwiki]")[0] == ["lolwiki"]  # manual pedida pelo nome


def test_optional_sources_need_secret_and_busy_are_skipped():
    sources, notes = plan_mod.plan("schedule", KNOWN, secrets={"HAS_PANDASCORE": True}, busy={"leaguepedia"})
    assert sources == ["oracles_elixir", "lolesports", "ddragon", "pandascore"]
    assert any("leaguepedia" in n for n in notes)


def test_dispatch_input():
    assert plan_mod.plan("workflow_dispatch", KNOWN, dispatch_input="ddragon lolesports")[0] == ["ddragon", "lolesports"]


def test_manual_sources_never_run_automatically():
    assert "lolwiki" not in plan_mod.plan("schedule", KNOWN)[0]
    assert plan_mod.plan("push", KNOWN, changed_files=["src/lolpred/sources/lolwiki.py"])[0] == []
    assert plan_mod.plan("workflow_dispatch", KNOWN, dispatch_input="lolwiki")[0] == ["lolwiki"]
