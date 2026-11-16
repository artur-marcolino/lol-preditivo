"""API do site lolesports.com (não documentada oficialmente).

É a mesma API que o site oficial usa para montar o calendário. A chave de acesso é pública
e vem embutida no próprio site; pode ser sobrescrita pela variável LOLESPORTS_API_KEY.

Tabelas geradas:
- `leagues`: ligas cobertas pelo site.
- `tournaments`: etapas/splits de cada liga, com datas.
- `schedule`: todas as séries (passadas e futuras) com placar, formato (Bo1/Bo3/Bo5) e estado.
  É a fonte natural para saber quais jogos ainda vão acontecer.
- `match_games`: os jogos (mapas) de cada série já concluída, com o lado de cada time e o
  `game_id`, que dá acesso ao feed de estatísticas ao vivo (livestats). Coleta incremental:
  só busca séries que ainda não estão no arquivo.
- `teams` e `team_players`: times e elencos atuais.
- `standings_rankings` e `standings_matches`: classificação e chave de cada etapa de cada
  torneio (fase de grupos, playoffs, play-in...), com a posição de cada time e a ordem das
  séries no chaveamento. A resposta bruta fica em `standings_raw` (JSON), para nada se perder
  se o formato mudar. Incremental: torneios já coletados só são revisitados enquanto estão em
  andamento ou terminaram há menos de 3 semanas.

Por ser uma API não oficial, pode mudar sem aviso. O coletor falha de forma isolada
(as outras fontes continuam) e registra o erro no `last_run.json`.
"""

from __future__ import annotations

import json
import os

import pandas as pd
import requests

from ..context import RunContext, now_utc
from ..http import RateLimiter
from ..storage import dataset_key, read_dataset, write_dataset
from .base import SourceResult, log

SOURCE = "lolesports"
BASE = "https://esports-api.lolesports.com/persisted/gw"
PUBLIC_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
LANG = "en-US"
CHECKPOINT_EVERY = 300  # grava match_games a cada N séries, para não perder progresso
STANDINGS_REFRESH_DAYS = 21


class Api:
    def __init__(self, session: requests.Session, min_interval: float = 0.3):
        self.session = session
        self.limiter = RateLimiter(min_interval)
        self.headers = {"x-api-key": os.environ.get("LOLESPORTS_API_KEY", PUBLIC_KEY)}

    def get(self, endpoint: str, **params) -> dict:
        self.limiter.wait()
        resp = self.session.get(f"{BASE}/{endpoint}", params={"hl": LANG, **params}, headers=self.headers, timeout=60)
        resp.raise_for_status()
        return resp.json().get("data", {})


# --------------------------------------------------------------------------- achatamento


def flatten_event(ev: dict) -> dict:
    """Uma série do calendário vira uma linha."""
    match = ev.get("match") or {}
    teams = match.get("teams") or [{}, {}]
    teams = (teams + [{}, {}])[:2]
    strategy = match.get("strategy") or {}
    league = ev.get("league") or {}
    row = {
        "match_id": match.get("id"),
        "start_time": ev.get("startTime"),
        "state": ev.get("state"),
        "type": ev.get("type"),
        "block_name": ev.get("blockName"),
        "league_name": league.get("name"),
        "league_slug": league.get("slug"),
        "strategy_type": strategy.get("type"),
        "best_of": strategy.get("count"),
    }
    for i, t in enumerate(teams, start=1):
        result = t.get("result") or {}
        record = t.get("record") or {}
        row.update({
            f"team{i}_name": t.get("name"),
            f"team{i}_code": t.get("code"),
            f"team{i}_outcome": result.get("outcome"),
            f"team{i}_game_wins": result.get("gameWins"),
            f"team{i}_record_wins": record.get("wins"),
            f"team{i}_record_losses": record.get("losses"),
        })
    return row


def flatten_games(match_id: str, event: dict) -> list[dict]:
    """Os mapas de uma série concluída, a partir de getEventDetails."""
    match = event.get("match") or {}
    teams = {t.get("id"): t for t in match.get("teams") or []}
    rows = []
    for g in match.get("games") or []:
        sides = {t.get("side"): t.get("id") for t in g.get("teams") or []}
        blue, red = sides.get("blue"), sides.get("red")
        rows.append({
            "match_id": match_id,
            "game_id": g.get("id"),
            "game_number": g.get("number"),
            "state": g.get("state"),
            "blue_team_id": blue,
            "blue_team_code": (teams.get(blue) or {}).get("code"),
            "red_team_id": red,
            "red_team_code": (teams.get(red) or {}).get("code"),
            "tournament_id": (event.get("tournament") or {}).get("id"),
            "league_slug": (event.get("league") or {}).get("slug"),
        })
    return rows


def flatten_standings(tournament_id: str, data: dict) -> tuple[list[dict], list[dict]]:
    """Resposta de getStandings -> (linhas de classificação, linhas de séries do chaveamento)."""
    rankings, matches = [], []
    for standing in data.get("standings") or []:
        for s_order, stage in enumerate(standing.get("stages") or [], start=1):
            stage_info = {"tournament_id": tournament_id, "stage_order": s_order, "stage_name": stage.get("name"),
                          "stage_type": stage.get("type"), "stage_slug": stage.get("slug")}
            for sec_order, section in enumerate(stage.get("sections") or [], start=1):
                base = {**stage_info, "section_order": sec_order, "section_name": section.get("name")}
                for r in section.get("rankings") or []:
                    for t in r.get("teams") or []:
                        rec = t.get("record") or {}
                        rankings.append({**base, "ordinal": r.get("ordinal"), "team_id": t.get("id"),
                                         "team_code": t.get("code"), "team_name": t.get("name"),
                                         "wins": rec.get("wins"), "losses": rec.get("losses"),
                                         "ties": rec.get("ties")})
                for m_order, m in enumerate(section.get("matches") or [], start=1):
                    teams = (list(m.get("teams") or []) + [{}, {}])[:2]
                    row = {**base, "match_order": m_order, "match_id": m.get("id"), "state": m.get("state"),
                           "previous_match_ids": ",".join(str(x) for x in m.get("previousMatchIds") or [])}
                    for i, t in enumerate(teams, start=1):
                        res = t.get("result") or {}
                        row.update({f"team{i}_id": t.get("id"), f"team{i}_code": t.get("code"),
                                    f"team{i}_outcome": res.get("outcome"), f"team{i}_game_wins": res.get("gameWins")})
                    matches.append(row)
    return rankings, matches


# --------------------------------------------------------------------------- coleta


def collect_standings(api: "Api", ctx: RunContext, tournaments: pd.DataFrame, result: SourceResult) -> None:
    raw = read_dataset(SOURCE, "standings_raw")
    done = set(raw["tournament_id"]) if raw is not None else set()
    today = pd.Timestamp(now_utc()).normalize()
    t = tournaments.drop_duplicates("tournament_id").copy()
    start = pd.to_datetime(t["start_date"], errors="coerce", utc=True)
    end = pd.to_datetime(t["end_date"], errors="coerce", utc=True)
    started = start.isna() | (start <= today + pd.Timedelta(days=1))
    recent = end.isna() | (end >= today - pd.Timedelta(days=STANDINGS_REFRESH_DAYS))
    todo = t.loc[started & (~t["tournament_id"].isin(done) | recent), "tournament_id"].tolist()
    log.info("  classificações a buscar: %d torneios", len(todo))

    fetched: list[dict] = []

    def save() -> None:
        nonlocal raw
        if not fetched:
            return
        merged = pd.concat([raw, pd.DataFrame(fetched)], ignore_index=True) if raw is not None else pd.DataFrame(fetched)
        raw = merged.drop_duplicates("tournament_id", keep="last")
        fetched.clear()
        result.record(dataset_key(SOURCE, "standings_raw"), write_dataset(raw, SOURCE, "standings_raw"))

    errors = 0
    for i, tid in enumerate(todo, start=1):
        if ctx.budget.expired():
            result.incomplete = True
            break
        try:
            data = api.get("getStandings", tournamentId=tid)
        except requests.RequestException as e:
            errors += 1
            if errors <= 10:
                result.error(f"getStandings {tid}: {e}")
            continue
        fetched.append({"tournament_id": tid, "fetched_at": now_utc().isoformat(timespec="seconds"),
                        "raw_json": json.dumps(data, ensure_ascii=False)})
        if i % 200 == 0:
            save()
    save()
    if raw is None or raw.empty:
        return

    rankings, matches = [], []
    for row in raw.itertuples(index=False):
        try:
            r, m = flatten_standings(row.tournament_id, json.loads(row.raw_json))
        except (ValueError, AttributeError, TypeError) as e:
            result.error(f"standings {row.tournament_id}: formato inesperado ({e})")
            continue
        rankings += r
        matches += m
    if rankings:
        result.record(dataset_key(SOURCE, "standings_rankings"),
                      write_dataset(pd.DataFrame(rankings), SOURCE, "standings_rankings"))
    if matches:
        result.record(dataset_key(SOURCE, "standings_matches"),
                      write_dataset(pd.DataFrame(matches), SOURCE, "standings_matches"))


def fetch_schedule(api: Api, league_id: str) -> list[dict]:
    """Percorre o calendário da liga nos dois sentidos a partir da página atual."""
    events: list[dict] = []
    first = api.get("getSchedule", leagueId=league_id).get("schedule", {})
    events += first.get("events", [])
    for direction in ("older", "newer"):
        token = (first.get("pages") or {}).get(direction)
        while token:
            page = api.get("getSchedule", leagueId=league_id, pageToken=token).get("schedule", {})
            events += page.get("events", [])
            token = (page.get("pages") or {}).get(direction)
    return events


def collect(ctx: RunContext) -> SourceResult:
    result = SourceResult()
    api = Api(ctx.session)

    # Ligas
    try:
        leagues = api.get("getLeagues").get("leagues", [])
    except requests.RequestException as e:
        result.error(f"getLeagues: {e}")
        return result
    leagues_df = pd.DataFrame([{
        "league_id": lg.get("id"), "slug": lg.get("slug"), "name": lg.get("name"),
        "region": lg.get("region"), "priority": lg.get("priority"),
        "display_priority": (lg.get("displayPriority") or {}).get("position"),
        "display_status": (lg.get("displayPriority") or {}).get("status"),
    } for lg in leagues])
    result.record(dataset_key(SOURCE, "leagues"), write_dataset(leagues_df, SOURCE, "leagues"))

    # Torneios e calendário, liga por liga
    tournaments, events = [], []
    for lg in leagues:
        if ctx.budget.expired():
            result.incomplete = True
            break
        lid = lg.get("id")
        try:
            for item in api.get("getTournamentsForLeague", leagueId=lid).get("leagues", []):
                for t in item.get("tournaments", []):
                    tournaments.append({"league_id": lid, "league_slug": lg.get("slug"), "tournament_id": t.get("id"),
                                        "slug": t.get("slug"), "start_date": t.get("startDate"), "end_date": t.get("endDate")})
            league_events = fetch_schedule(api, lid)
            events += league_events
            log.info("  %s: %d eventos", lg.get("slug"), len(league_events))
        except requests.RequestException as e:
            result.error(f"{lg.get('slug')}: {e}")
    if result.incomplete:
        return result  # calendário parcial não substitui o completo
    if tournaments:
        result.record(dataset_key(SOURCE, "tournaments"),
                      write_dataset(pd.DataFrame(tournaments).drop_duplicates(), SOURCE, "tournaments", date_col="start_date"))
    schedule = pd.DataFrame([flatten_event(e) for e in events])
    if not schedule.empty:
        schedule = schedule.drop_duplicates(subset=["match_id", "start_time"]).sort_values("start_time")
        result.record(dataset_key(SOURCE, "schedule"), write_dataset(schedule, SOURCE, "schedule", date_col="start_time"))

    # Mapas de cada série concluída (incremental)
    games = read_dataset(SOURCE, "match_games")
    done = set(games["match_id"].dropna()) if games is not None else set()
    todo = []
    if not schedule.empty:
        mask = (schedule["state"] == "completed") & (schedule["type"] == "match") & schedule["match_id"].notna()
        todo = [m for m in schedule.loc[mask, "match_id"].tolist() if m not in done]
    log.info("  séries concluídas sem detalhes: %d", len(todo))
    new_rows: list[dict] = []

    def flush() -> None:
        nonlocal games, new_rows
        if not new_rows:
            return
        merged = pd.concat([games, pd.DataFrame(new_rows)], ignore_index=True) if games is not None else pd.DataFrame(new_rows)
        games = merged.drop_duplicates(subset=["match_id", "game_id"])
        new_rows = []
        result.record(dataset_key(SOURCE, "match_games"), write_dataset(games, SOURCE, "match_games"))

    for i, match_id in enumerate(todo, start=1):
        if ctx.budget.expired():
            result.incomplete = True
            break
        try:
            event = api.get("getEventDetails", id=match_id).get("event") or {}
            new_rows += flatten_games(match_id, event)
        except requests.RequestException as e:
            # Algumas séries antigas não têm detalhes; registra sem interromper
            if len(result.errors) < 20:
                result.error(f"getEventDetails {match_id}: {e}")
        if i % CHECKPOINT_EVERY == 0:
            flush()
    flush()

    # Times e elencos
    if not ctx.budget.expired():
        try:
            teams = api.get("getTeams").get("teams", [])
            result.record(dataset_key(SOURCE, "teams"), write_dataset(pd.DataFrame([{
                "team_id": t.get("id"), "slug": t.get("slug"), "name": t.get("name"), "code": t.get("code"),
                "status": t.get("status"),
                "home_league": (t.get("homeLeague") or {}).get("name"),
                "home_league_region": (t.get("homeLeague") or {}).get("region"),
            } for t in teams]), SOURCE, "teams"))
            result.record(dataset_key(SOURCE, "team_players"), write_dataset(pd.DataFrame([{
                "team_id": t.get("id"), "team_code": t.get("code"), "player_id": p.get("id"),
                "summoner_name": p.get("summonerName"), "role": p.get("role"),
            } for t in teams for p in t.get("players") or []]), SOURCE, "team_players"))
        except requests.RequestException as e:
            result.error(f"getTeams: {e}")

    # Classificação e chaveamento de cada torneio (incremental)
    if tournaments and not ctx.budget.expired():
        try:
            collect_standings(api, ctx, pd.DataFrame(tournaments), result)
        except Exception as e:  # parte nova e não documentada: não derruba o resto da fonte
            result.error(f"standings: {type(e).__name__}: {e}")
    return result
