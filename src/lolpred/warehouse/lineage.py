"""Linhagem de times: liga IDs que são a mesma organização com outro nome.

O Oracle's Elixir cria um ID novo quando um time muda de nome (SK Telecom T1 -> T1,
Samsung Galaxy -> KSV -> Gen.G). Sem tratar isso, o histórico do time "zera" na troca.

Regra (heurística, revisável em seeds/team_lineage_overrides.csv):
o time B é continuação do time A quando
  1. A jogou pela última vez antes de B jogar pela primeira vez, com no máximo 180 dias entre os dois;
  2. os jogadores dos últimos 5 jogos de A e dos primeiros 5 jogos de B têm pelo menos 3 em comum,
     ou 2 em comum se for a mesma liga e um nome contiver o outro (ex.: "T1" em "SK Telecom T1").
Cada time tem no máximo um antecessor e um sucessor (o de maior elenco em comum).
O `franchise_id` é o ID do primeiro time da cadeia; `franchise_name` é o nome mais recente.
"""

from __future__ import annotations

import csv
from importlib import resources

import duckdb
import pandas as pd

CANDIDATES_SQL = """
WITH ranked AS (
    SELECT team_id, team_name, league, game_time, players,
           row_number() OVER (PARTITION BY team_id ORDER BY game_time, game_id) AS rn_first,
           row_number() OVER (PARTITION BY team_id ORDER BY game_time DESC, game_id DESC) AS rn_last
    FROM core.fct_team_game
), span AS (
    SELECT team_id,
           arg_max(team_name, game_time) AS name,
           min(game_time) AS first_t, max(game_time) AS last_t,
           arg_max(league, game_time) AS last_league,
           arg_min(league, game_time) AS first_league,
           list_distinct(flatten(list(players) FILTER (WHERE rn_first <= 5))) AS first_players,
           list_distinct(flatten(list(players) FILTER (WHERE rn_last <= 5))) AS last_players
    FROM ranked GROUP BY team_id
)
SELECT a.team_id AS prev_id, b.team_id AS next_id, a.name AS prev_name, b.name AS next_name,
       date_diff('day', a.last_t, b.first_t) AS gap_days,
       len(list_intersect(a.last_players, b.first_players)) AS shared,
       a.last_league = b.first_league AS same_league
FROM span a
JOIN span b ON b.first_t > a.last_t AND b.first_t <= a.last_t + INTERVAL 180 DAY
WHERE len(list_intersect(a.last_players, b.first_players)) >= 2
"""


def _names_related(a: str, b: str) -> bool:
    a, b = (a or "").lower(), (b or "").lower()
    return bool(a and b) and (a in b or b in a)


def load_overrides() -> pd.DataFrame:
    path = resources.files("lolpred") / "seeds" / "team_lineage_overrides.csv"
    with path.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("prev_team_id") and not r["prev_team_id"].startswith("#")]
    return pd.DataFrame(rows, columns=["prev_team_id", "next_team_id", "action", "note"])


def build(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    cand = con.execute(CANDIDATES_SQL).df()
    related = pd.Series([_names_related(a, b) for a, b in zip(cand["prev_name"], cand["next_name"])],
                        index=cand.index, dtype=bool)
    ok = (cand["shared"] >= 3) | ((cand["shared"] >= 2) & cand["same_league"].fillna(False).astype(bool) & related)
    cand = cand[ok].copy()
    cand["reason"] = "heurística: elenco em comum"

    overrides = load_overrides()
    unlink = set(zip(overrides.loc[overrides["action"] == "unlink", "prev_team_id"],
                     overrides.loc[overrides["action"] == "unlink", "next_team_id"]))
    cand = cand[[(p, n) not in unlink for p, n in zip(cand["prev_id"], cand["next_id"])]]
    forced = overrides[overrides["action"] == "link"]
    if not forced.empty:
        cand = pd.concat([
            pd.DataFrame({"prev_id": forced["prev_team_id"], "next_id": forced["next_team_id"],
                          "shared": 99, "gap_days": 0, "reason": "manual: " + forced["note"].fillna("")}),
            cand,
        ], ignore_index=True)

    # Um antecessor por time e um sucessor por time: fica o par com mais jogadores em comum
    cand = cand.sort_values(["shared", "gap_days"], ascending=[False, True])
    cand = cand.drop_duplicates("next_id").drop_duplicates("prev_id")

    parent = dict(zip(cand["next_id"], cand["prev_id"]))
    reason = dict(zip(cand["next_id"], cand["reason"]))

    def root(team: str) -> str:
        seen = set()
        while team in parent and team not in seen:  # proteção contra ciclos
            seen.add(team)
            team = parent[team]
        return team

    teams = con.execute("""
        SELECT team_id, arg_max(team_name, game_time) AS team_name, max(game_time) AS last_game
        FROM core.fct_team_game GROUP BY 1
    """).df()
    teams["franchise_id"] = [root(t) for t in teams["team_id"]]
    teams["predecessor_team_id"] = teams["team_id"].map(parent)
    teams["link_reason"] = teams["team_id"].map(reason)
    latest = teams.sort_values("last_game").groupby("franchise_id")["team_name"].last()
    teams["franchise_name"] = teams["franchise_id"].map(latest)
    out = teams[["team_id", "franchise_id", "franchise_name", "predecessor_team_id", "link_reason"]]
    con.register("_lineage", out)
    con.execute("CREATE OR REPLACE TABLE core.team_lineage AS SELECT * FROM _lineage")
    con.unregister("_lineage")
    return out
