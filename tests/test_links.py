"""Testa a camada de ligação entre as fontes com dados sintéticos de respostas conhecidas.

Usa o mesmo mini-campeonato de test_warehouse (S1–S4, com a Alpha renomeada para Alpha
Nova na S4) e acrescenta as mesmas partidas vistas por outras fontes, cada uma com seus
próprios IDs e nomes:

- Leaguepedia: chama a Alpha de "Team Alpha"; o jogo da S2 não tem ID em comum (tem de
  ser achado por horário + times); há uma partida futura.
- lolesports: "Alpha Esports" na S1; na S3 só a sigla bate ("Alpha Club Seoul", ALP);
  a mesma partida futura; a chave do torneio inclui um confronto antigo sem partida no
  calendário (S2), que só pode ser ligado por ser o único Beta x Gamma do torneio.
- PandaScore: mostra o nome atual ("Alpha Nova") numa partida em que o time ainda se
  chamava Alpha (S1): só bate porque a comparação é por franquia.
- PandaSkill, EsportsBench e GCD com as chaves de cada um.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import duckdb
import pandas as pd
import pytest
from test_warehouse import ROLES, make_oe

from lolpred import db
from lolpred.storage import write_dataset
from lolpred.warehouse import checks

PAGE = "LCK/2026 Season/Spring Season"
LP_NAME = {"Alpha": "Team Alpha", "Beta": "Beta", "Gamma": "Gamma", "Alpha Nova": "Alpha Nova"}
LP_ROLE = {"top": "Top", "jng": "Jungle", "mid": "Mid", "bot": "Bot", "sup": "Support"}
FUTURE = (datetime.now(timezone.utc) + timedelta(days=10)).replace(tzinfo=None, microsecond=0)


def ts(x: datetime) -> str:
    return x.strftime("%Y-%m-%d %H:%M:%S")


def iso(x: datetime) -> str:
    return x.strftime("%Y-%m-%dT%H:%M:%SZ")


def team_games(oe: pd.DataFrame) -> pd.DataFrame:
    t = oe[oe.position == "team"]
    blue = t[t.side == "Blue"].set_index("gameid")
    red = t[t.side == "Red"].set_index("gameid")
    out = pd.DataFrame({"blue": blue.teamname, "red": red.teamname, "blue_win": blue.result,
                        "date": blue.date, "game": blue.game.astype(int)})
    out["winner"] = out.blue.where(out.blue_win == 1, out.red)
    out["loser"] = out.red.where(out.blue_win == 1, out.blue)
    return out


def write_sources(oe: pd.DataFrame) -> None:
    g = team_games(oe)
    match_of = {sid: f"{PAGE}_Week 1_{i}" for i, sid in enumerate(["S1", "S2", "S3", "S4"], start=1)}

    # ---------------- Leaguepedia
    sg = []
    for gid, r in g.iterrows():
        sid = gid.split("-")[0]
        has_id = sid != "S2"  # o jogo da S2 não tem ID em comum com o Oracle's Elixir
        sg.append({"GameId": f"{match_of[sid]}_{r.game}", "MatchId": match_of[sid], "OverviewPage": PAGE,
                   "Team1": LP_NAME[r.blue], "Team2": LP_NAME[r.red], "WinTeam": LP_NAME[r.winner],
                   "LossTeam": LP_NAME[r.loser], "N_GameInMatch": str(r.game), "DateTime_UTC": r.date,
                   "RiotPlatformGameId": gid if has_id else "", "RiotPlatformId": "", "RiotGameId": "",
                   "MatchHistory": ""})
    write_dataset(pd.DataFrame(sg), "leaguepedia", "ScoreboardGames")
    sp = []
    for gid in ("S1-G1", "S3-G1"):
        r = g.loc[gid]
        for side_num, team in (("1", r.blue), ("2", r.red)):
            roster = {"Alpha": "A", "Beta": "B", "Gamma": "G"}[team]
            for role in ROLES:
                nick = f"{roster}{role}"
                sp.append({"GameId": f"{match_of[gid[:2]]}_{r.game}", "Link": f"{nick} (Jogador {nick})",
                           "Name": nick, "Side": side_num, "Role": LP_ROLE[role]})
    write_dataset(pd.DataFrame(sp), "leaguepedia", "ScoreboardPlayers")
    write_dataset(pd.DataFrame([
        {"AllName": "Atop (Jogador Atop)", "OverviewPage": "Atop (Jogador Atop)", "ID": "Atop"},
        {"AllName": "VelhoNick", "OverviewPage": "Atop (Jogador Atop)", "ID": "VelhoNick"}]),
        "leaguepedia", "PlayerRedirects")
    write_dataset(pd.DataFrame([
        {"Name": "Team Alpha", "OverviewPage": "Team Alpha", "Short": "ALP"},
        {"Name": "Beta", "OverviewPage": "Beta", "Short": "BET"},
        {"Name": "Gamma", "OverviewPage": "Gamma", "Short": "GAM"},
        {"Name": "Alpha Nova", "OverviewPage": "Alpha Nova", "Short": "ALN"}]), "leaguepedia", "Teams")
    write_dataset(pd.DataFrame([{
        "OverviewPage": PAGE, "Name": "LCK 2026 Spring", "League": "LoL Champions Korea", "Region": "Korea",
        "DateStart": "2026-03-01", "Date": "2026-03-31", "TournamentLevel": "Primary", "IsPlayoffs": "0",
        "IsQualifier": "0", "IsOfficial": "1", "Split": "Spring", "Year": "2026", "EventType": "Offline",
        "Country": "Korea", "Prizepool": "", "Currency": ""}]), "leaguepedia", "Tournaments")
    ms = []
    series_first = g.groupby(g.index.str[:2]).first()
    for sid, r in series_first.iterrows():
        ms.append({"MatchId": match_of[sid], "DateTime_UTC": r.date, "Winner": "1", "Team1": LP_NAME[r.blue],
                   "Team2": LP_NAME[r.red], "OverviewPage": PAGE, "BestOf": "3", "Phase": "Regular Season",
                   "Round": "", "Tab": "Week 1", "ShownRound": "", "GroupName": "", "MatchDay": "1",
                   "IsTiebreaker": "0"})
    ms.append({"MatchId": f"{PAGE}_Week 9_1", "DateTime_UTC": ts(FUTURE), "Winner": "", "Team1": "Alpha Nova",
               "Team2": "Gamma", "OverviewPage": PAGE, "BestOf": "3", "Phase": "Regular Season", "Round": "",
               "Tab": "Week 9", "ShownRound": "", "GroupName": "", "MatchDay": "9", "IsTiebreaker": "0"})
    write_dataset(pd.DataFrame(ms), "leaguepedia", "MatchSchedule")

    # ---------------- lolesports
    s1 = datetime.fromisoformat(series_first.loc["S1"].date)
    s3 = datetime.fromisoformat(series_first.loc["S3"].date)
    base = {"type": "match", "strategy_type": "bestOf", "league_slug": "lck", "league_name": "LCK",
            "block_name": "Week 1", "team1_record_wins": 0, "team1_record_losses": 0,
            "team2_record_wins": 0, "team2_record_losses": 0}
    write_dataset(pd.DataFrame([
        {**base, "match_id": "les1", "start_time": iso(s1 - timedelta(hours=1)), "state": "completed", "best_of": 3,
         "team1_name": "Alpha Esports", "team1_code": "ALP", "team1_outcome": "win", "team1_game_wins": 2,
         "team2_name": "Beta", "team2_code": "BET", "team2_outcome": "loss", "team2_game_wins": 1},
        {**base, "match_id": "les3", "start_time": iso(s3 - timedelta(hours=1)), "state": "completed", "best_of": 3,
         "team1_name": "Alpha Club Seoul", "team1_code": "ALP", "team1_outcome": "win", "team1_game_wins": 2,
         "team2_name": "Gamma", "team2_code": "GAM", "team2_outcome": "loss", "team2_game_wins": 0},
        {**base, "match_id": "lesF", "start_time": iso(FUTURE + timedelta(minutes=30)), "state": "unstarted",
         "best_of": 3, "team1_name": "Alpha Nova", "team1_code": "ALN", "team1_outcome": None, "team1_game_wins": 0,
         "team2_name": "Gamma", "team2_code": "GAM", "team2_outcome": None, "team2_game_wins": 0},
    ]), "lolesports", "schedule")
    mg = []
    for match_id, sid, other, ocode in (("les1", "S1", "lt_beta", "BET"), ("les3", "S3", "lt_gamma", "GAM")):
        for n in range(1, len(g[g.index.str.startswith(sid)]) + 1):
            # azul/vermelho do lolesports invertidos de propósito: o coletor não deve depender deles
            mg.append({"match_id": match_id, "game_id": f"{match_id}_g{n}", "game_number": n, "state": "completed",
                       "blue_team_id": other, "blue_team_code": ocode, "red_team_id": "lt_alpha",
                       "red_team_code": "ALP", "tournament_id": "ltour1", "league_slug": "lck"})
    write_dataset(pd.DataFrame(mg), "lolesports", "match_games")
    write_dataset(pd.DataFrame([
        {"team_id": "lt_alpha", "slug": "alpha", "name": "Alpha Esports", "code": "ALP"},
        {"team_id": "lt_beta", "slug": "beta", "name": "Beta", "code": "BET"},
        {"team_id": "lt_gamma", "slug": "gamma", "name": "Gamma", "code": "GAM"}]), "lolesports", "teams")
    write_dataset(pd.DataFrame([{"team_id": "lt_alpha", "team_code": "ALP", "player_id": "lp_atop",
                                 "summoner_name": "Atop", "role": "top"}]), "lolesports", "team_players")
    write_dataset(pd.DataFrame([
        {"league_id": "l1", "league_slug": "lck", "tournament_id": "ltour1", "slug": "lck_spring_2026",
         "start_date": "2026-03-01", "end_date": "2026-03-31"}]), "lolesports", "tournaments")
    st = {"tournament_id": "ltour1", "stage_order": 1, "stage_name": "Regular Season", "stage_type": "groups",
          "stage_slug": "regular_season", "section_order": 1, "section_name": "Regular Season", "state": "completed",
          "previous_match_ids": "", "team1_code": "", "team1_outcome": "", "team1_game_wins": 0,
          "team2_code": "", "team2_outcome": "", "team2_game_wins": 0}
    write_dataset(pd.DataFrame([
        {**st, "match_order": 1, "match_id": "les1", "team1_id": "lt_alpha", "team2_id": "lt_beta"},
        {**st, "match_order": 2, "match_id": "lesOld", "team1_id": "lt_beta", "team2_id": "lt_gamma"}]),
        "lolesports", "standings_matches")

    # ---------------- PandaScore (nome atual "Alpha Nova" numa partida antiga da Alpha)
    def ps_match(mid, sid, n1, i1, n2, i2, winner):
        games = [{"id": mid * 10 + n, "position": n, "status": "finished"}
                 for n in range(1, len(g[g.index.str.startswith(sid)]) + 1)]
        first = series_first.loc[sid]
        return {"id": mid, "name": f"{n1} vs {n2}", "status": "finished", "match_type": "best_of",
                "number_of_games": 3, "begin_at": iso(datetime.fromisoformat(first.date)), "end_at": None,
                "scheduled_at": None, "original_scheduled_at": None, "rescheduled": False, "forfeit": False,
                "draw": False, "winner_id": winner, "league_id": 1, "serie_id": 7, "tournament_id": 77,
                "modified_at": None, "team1_id": i1, "team1_name": n1, "team1_acronym": n1[:3].upper(),
                "team1_score": 0, "team2_id": i2, "team2_name": n2, "team2_acronym": n2[:3].upper(),
                "team2_score": 0,
                "raw_json": json.dumps({"games": games, "league": {"name": "LCK"},
                                        "serie": {"full_name": "Spring 2026"}, "tournament": {"name": "Regular Season"}})}
    write_dataset(pd.DataFrame([ps_match(9001, "S1", "Alpha Nova", 501, "Beta", 502, 501),
                                ps_match(9002, "S2", "Beta", 502, "Gamma", 503, 503)]), "pandascore", "matches_past")
    write_dataset(pd.DataFrame([{"id": 501, "name": "Alpha Nova", "acronym": "ALN", "slug": "a", "location": None,
                                 "modified_at": None, "raw_json": "{}"}]), "pandascore", "teams")
    write_dataset(pd.DataFrame([{"id": 77, "name": "Regular Season", "slug": "rs", "league_id": 1, "serie_id": 7,
                                 "tier": "s", "type": "online", "region": None, "country": None, "begin_at": None,
                                 "end_at": None, "winner_id": None, "prizepool": None, "has_bracket": False,
                                 "modified_at": None, "raw_json": "{}"}]), "pandascore", "tournaments")

    # ---------------- PandaSkill (IDs próprios, anonimizados), só a S3
    meta, stats = [], []
    for k, gid in enumerate(("S3-G1", "S3-G2")):
        r = g.loc[gid]
        meta.append({"game_id": k, "date": r.date, "match_id": 0, "tournament_id": 0,
                     "tournament_name": "Regular Season", "series_id": 0, "series_name": "LCK Spring 2026",
                     "league_id": 0, "league_name": "LCK"})
        for team, team_id, roster in (("Alpha", 10, "A"), ("Gamma", 11, "G")):
            for i, role in enumerate(ROLES):
                stats.append({"game_id": k, "player_id": team_id * 10 + i, "player_name": f"{roster}{role}",
                              "team_id": team_id, "team_name": team, "team_acronym": team[:3].upper(),
                              "role": LP_ROLE[role], "win": team == r.winner})
    write_dataset(pd.DataFrame(meta), "pandaskill", "game_metadata")
    write_dataset(pd.DataFrame(stats), "pandaskill", "game_players_stats")

    # ---------------- EsportsBench e GCD
    write_dataset(pd.DataFrame([{"date": "2026-03-02", "competitor_1": "Team Alpha", "competitor_2": "Beta",
                                 "outcome": 1.0, "match_id": match_of["S1"], "page": "x"}]), "esportsbench", "matches")
    write_dataset(pd.DataFrame([{"region": "KR", "league": "LCK", "team": "Alpha Nova", "summoner": "Atop",
                                 "position": "Top", "end_date_raw": "", "end_date": "2027-11-20", "residency": "",
                                 "status": "Active", "quadcode": "", "sheet_last_update": "", "player_key": "k",
                                 "content_hash": "h"}]), "riot_gcd", "contracts")


@pytest.fixture
def con(tmp_data):
    oe = make_oe()
    write_dataset(oe, "oracles_elixir", "matches", "2026", date_col="date")
    write_sources(oe)
    db.build()
    c = duckdb.connect(str(tmp_data / "lol.duckdb"), read_only=True)
    yield c
    c.close()


def rows(con, sql):
    return con.execute(sql).fetchall()


def test_leaguepedia_games_series_teams_players(con):
    games = dict(rows(con, "SELECT game_id, method FROM link.game WHERE source = 'leaguepedia'"))
    assert len(games) == 7 and games["S2-G1"] == "time_teams"  # sem ID em comum: horário + times
    assert set(games.values()) == {"riot_platform_id", "time_teams"}
    series = dict(rows(con, "SELECT series_id, source_series_id FROM link.series WHERE source = 'leaguepedia'"))
    assert series["s_S1-G1"] == f"{PAGE}_Week 1_1" and len(series) == 4
    team = rows(con, """SELECT team_id FROM link.team WHERE source = 'leaguepedia' AND source_team_key = 'Team Alpha'
                        AND is_primary""")
    assert team == [("oe:team:alpha",)]
    player = rows(con, """SELECT player_id FROM link.player WHERE source = 'leaguepedia'
                          AND source_player_key = 'Atop (Jogador Atop)'""")
    assert player == [("oe:player:a0",)]
    # nome antigo na Leaguepedia vira apelido do jogador
    assert rows(con, "SELECT player_id FROM link.player_alias WHERE alias = 'velhonick'") == [("oe:player:a0",)]


def test_lolesports_by_name_code_and_bracket(con):
    series = {s: m for s, m in rows(con, "SELECT series_id, method FROM link.series WHERE source = 'lolesports'")}
    assert series == {"s_S1-G1": "time_teams_name", "s_S3-G1": "time_teams_code"}
    games = rows(con, "SELECT source_game_id, game_id FROM link.game WHERE source = 'lolesports' ORDER BY 1")
    assert games[:3] == [("les1_g1", "S1-G1"), ("les1_g2", "S1-G2"), ("les1_g3", "S1-G3")]
    # azul/vermelho invertidos na fonte não confundem o ID do time
    assert rows(con, """SELECT team_id FROM link.team WHERE source = 'lolesports' AND source_team_key = 'lt_alpha'
                        AND is_primary""") == [("oe:team:alpha",)]
    assert rows(con, "SELECT player_id, method FROM link.player WHERE source = 'lolesports'") == \
        [("oe:player:a0", "roster_team_name")]
    ctx = dict(rows(con, "SELECT series_id, les_context_method FROM core.series_context WHERE les_context_method IS NOT NULL"))
    assert ctx == {"s_S1-G1": "lolesports_match_id", "s_S2-G1": "tournament_teams_unique"}


def test_pandascore_matches_renamed_team_by_franchise(con):
    series = dict(rows(con, "SELECT series_id, swapped FROM link.series WHERE source = 'pandascore'"))
    assert series == {"s_S1-G1": False, "s_S2-G1": False}
    assert rows(con, "SELECT count(*) FROM link.game WHERE source = 'pandascore'") == [(4,)]
    ps = rows(con, "SELECT team_id FROM link.team WHERE source = 'pandascore' AND source_team_key = '501'")
    assert ps == [("oe:team:alpha",)]


def test_pandaskill_esportsbench_gcd(con):
    assert rows(con, "SELECT series_id FROM link.series WHERE source = 'pandaskill'") == [("s_S3-G1",)]
    assert rows(con, "SELECT game_id FROM link.game WHERE source = 'pandaskill' ORDER BY 1") == [("S3-G1",), ("S3-G2",)]
    assert rows(con, "SELECT player_id FROM link.player WHERE source = 'pandaskill' AND source_player_key = '100'") == \
        [("oe:player:a0",)]
    assert rows(con, "SELECT series_id FROM link.series WHERE source = 'esportsbench'") == [("s_S1-G1",)]
    assert rows(con, "SELECT team_id FROM link.team WHERE source = 'gcd'") == [("oe:team:alpha2",)]
    assert rows(con, "SELECT player_id, method FROM link.player WHERE source = 'gcd'") == \
        [("oe:player:a0", "roster_team_name")]


def test_context_and_xrefs(con):
    ctx = rows(con, """SELECT tournament_level, lp_phase, lp_tab, les_stage_name, ps_tier
                       FROM core.series_context WHERE series_id = 's_S1-G1'""")
    assert ctx == [("Primary", "Regular Season", "Week 1", "Regular Season", "s")]
    x = rows(con, "SELECT lp_match_id, les_match_id, ps_match_id, ebench_match_id FROM link.series_xref WHERE series_id = 's_S1-G1'")
    assert x == [(f"{PAGE}_Week 1_1", "les1", "9001", f"{PAGE}_Week 1_1")]
    assert rows(con, "SELECT lp_player_page, les_player_id, pskill_player_id FROM link.player_xref WHERE player_id = 'oe:player:a0'") == \
        [("Atop (Jogador Atop)", "lp_atop", "100")]
    assert rows(con, "SELECT league FROM link.league WHERE source = 'leaguepedia' AND is_primary") == [("LCK",)]


def test_upcoming_merges_sources_and_links_teams(con):
    up = rows(con, """SELECT sources, league, team1_id, team2_id, team1_franchise_id, lp_match_id, les_match_id
                      FROM core.fct_series_upcoming""")
    assert up == [("leaguepedia,lolesports", "LCK", "oe:team:alpha2", "oe:team:gamma", "oe:team:alpha",
                   f"{PAGE}_Week 9_1", "lesF")]


def test_link_checks_pass(con):
    failures = [(name, n) for name, n in checks.run(con) if n]
    assert failures == []
