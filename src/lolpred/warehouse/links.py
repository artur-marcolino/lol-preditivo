"""Camada de ligação entre as fontes (schema `link`), partidas futuras e contexto das séries.

Cada fonte identifica jogos, séries, times e jogadores do seu jeito: o Oracle's Elixir
(base do `core`) tem IDs próprios, a Leaguepedia usa nomes de página, o lolesports e o
PandaScore usam números, o GCD só tem o nick. Esta etapa liga tudo aos IDs do `core`
(`game_id`, `series_id`, `team_id`/`franchise_id`, `player_id`, `league`) e à página de
torneio da Leaguepedia (`core.dim_tournament`).

Princípio: primeiro ligamos **eventos** (jogos e séries), que têm data e dois times; as
correspondências de **entidades** (times, jogadores, ligas, torneios) saem da contagem de
"quem aparece no lugar de quem" nos eventos ligados. Isso é mais seguro do que casar nomes
soltos, porque cada nome é confirmado por muitos jogos.

Ligação de jogos e séries, por fonte:
- Leaguepedia: pelo ID da partida no servidor da Riot (`RiotPlatformGameId`), pelo `bmid`
  do site da LPL ou pelo link do histórico + número do jogo. Cobre ~99,9% dos jogos.
- lolesports, PandaScore, PandaSkill, OddsPapi: pela data (janela de ±30 h) e pelos dois
  times, cujos nomes são comparados com todos os nomes já vistos de cada time (nome
  exato, nome sem "Esports"/"Gaming", sigla e, por último, nome contido no outro). Depois
  de uma primeira rodada, os IDs de time de cada fonte aprendidos nas séries ligadas
  servem para ligar o que sobrou.
- EsportsBench: o `match_id` é o `MatchId` da Leaguepedia.

Tabelas criadas:
- `link.game`, `link.series`: (fonte, ID na fonte) -> jogo/série do core, com o método.
- `link.team`, `link.player`: (fonte, chave na fonte) -> time/jogador do core, com a
  quantidade de evidências, a fração delas e o período em que a correspondência foi vista.
  Um nome pode apontar para mais de um ID ao longo do tempo (renomeações, IDs repetidos);
  `is_primary` marca o mais frequente.
- `link.league`, `link.tournament`: ligas e torneios de cada fonte -> `league` do core e
  página de torneio da Leaguepedia.
- `link.team_alias`, `link.player_alias`: todos os nomes conhecidos de cada time/jogador.
- `link.series_xref`, `link.game_xref`, `link.player_xref`: uma linha por série/jogo/
  jogador com o ID principal em cada fonte (o jeito mais fácil de cruzar).
- `link.coverage`: quanto de cada fonte foi ligado.
- `core.dim_tournament`: torneios (Leaguepedia), com nível, se é playoff/qualificatória...
- `core.series_context`: para cada série, torneio, fase/rodada/grupo (Leaguepedia), etapa e
  posição na chave (lolesports) e nível do torneio (PandaScore).
- `core.fct_series_upcoming`: partidas futuras de todas as fontes, sem duplicatas, com os
  times já ligados ao core (quando o time já jogou alguma partida na base).

Nada aqui cria variáveis de modelo: é só a ligação entre as bases.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import duckdb

from ..sources.base import log

WINDOW_HOURS = 30  # janela entre o horário da fonte e o início da série no core
UPCOMING_GRACE_HOURS = 6  # partidas que começaram há pouco ainda contam como "futuras"


def has(con: duckdb.DuckDBPyConnection, schema: str, name: str) -> bool:
    return bool(con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
        [schema, name]).fetchone()[0])


def has_column(con: duckdb.DuckDBPyConnection, schema: str, table: str, column: str) -> bool:
    return bool(con.execute(
        "SELECT count(*) FROM information_schema.columns WHERE table_schema = ? AND table_name = ? AND column_name = ?",
        [schema, table, column]).fetchone()[0])


# ----------------------------------------------------------------------------- estrutura

SETUP_SQL = r"""
CREATE SCHEMA IF NOT EXISTS link;
CREATE SCHEMA IF NOT EXISTS stg;

-- nome normalizado: minúsculas, sem acento, só letras e números ("Weibo Gaming" -> "weibogaming")
CREATE OR REPLACE MACRO link.nm(s) AS
    nullif(regexp_replace(lower(strip_accents(coalesce(CAST(s AS VARCHAR), ''))), '[^a-z0-9]', '', 'g'), '');
-- forma "núcleo": sem "team" no início e sem esports/gaming/club no fim ("Team WE" -> "we")
CREATE OR REPLACE MACRO link.nm_core(s) AS
    nullif(regexp_replace(regexp_replace(link.nm(s), '^team', ''), '(esports|esport|gaming|club)$', ''), '');
-- nome sem o complemento entre parênteses da Leaguepedia ("Faker (Lee Sang-hyeok)" -> "faker")
CREATE OR REPLACE MACRO link.nm_page(s) AS link.nm(regexp_replace(CAST(s AS VARCHAR), '\s*\(.*\)\s*$', ''));
-- palavras do nome, sem as genéricas ("Kt Rolster Challengers" -> [kt, rolster, challengers])
CREATE OR REPLACE MACRO link.tokens(s) AS list_filter(
    string_split_regex(lower(strip_accents(coalesce(CAST(s AS VARCHAR), ''))), '[^a-z0-9]+'),
    lambda w: length(w) > 0 AND w NOT IN ('esports', 'esport', 'gaming', 'team', 'club', 'e', 'sports', 'the'));
CREATE OR REPLACE MACRO link.role_code(r) AS CASE lower(CAST(r AS VARCHAR))
    WHEN 'top' THEN 'top' WHEN 'jungle' THEN 'jng' WHEN 'jng' THEN 'jng' WHEN 'jun' THEN 'jng'
    WHEN 'mid' THEN 'mid' WHEN 'middle' THEN 'mid' WHEN 'bot' THEN 'bot' WHEN 'adc' THEN 'bot'
    WHEN 'bottom' THEN 'bot' WHEN 'support' THEN 'sup' WHEN 'sup' THEN 'sup' WHEN 'utility' THEN 'sup' END;

CREATE OR REPLACE TABLE link.game (
    source VARCHAR, source_game_id VARCHAR, game_id VARCHAR, series_id VARCHAR, method VARCHAR);
CREATE OR REPLACE TABLE link.series (
    source VARCHAR, source_series_id VARCHAR, series_id VARCHAR, swapped BOOLEAN, method VARCHAR,
    hours_diff DOUBLE);
CREATE OR REPLACE TABLE link.team (
    source VARCHAR, source_team_key VARCHAR, source_team_name VARCHAR, team_id VARCHAR,
    n_evidence INTEGER, share DOUBLE, first_seen TIMESTAMP, last_seen TIMESTAMP, method VARCHAR,
    is_primary BOOLEAN);
CREATE OR REPLACE TABLE link.player (
    source VARCHAR, source_player_key VARCHAR, source_player_name VARCHAR, player_id VARCHAR,
    n_evidence INTEGER, share DOUBLE, first_seen TIMESTAMP, last_seen TIMESTAMP, method VARCHAR,
    is_primary BOOLEAN);
CREATE OR REPLACE TABLE link.league (
    source VARCHAR, source_league_key VARCHAR, source_league_name VARCHAR, league VARCHAR,
    n_series INTEGER, share DOUBLE, is_primary BOOLEAN);
CREATE OR REPLACE TABLE link.tournament (
    source VARCHAR, source_tournament_key VARCHAR, source_tournament_name VARCHAR, tournament_page VARCHAR,
    n_series INTEGER, share DOUBLE, is_primary BOOLEAN);
"""


def insert_votes(con, table: str, source: str, evidence_sql: str, method: str,
                 min_n: int = 2, min_share: float = 0.5) -> int:
    """Agrega evidências (key, name, target, t) em `link.team` ou `link.player`.

    Mantém os pares com pelo menos `min_n` evidências ou que sejam maioria para a chave
    (descarta ruído de um jogo só, como um nome digitado errado numa planilha).
    """
    con.execute(f"""
        INSERT INTO {table}
        WITH ev AS ({evidence_sql}),
        agg AS (
            SELECT CAST(key AS VARCHAR) k, any_value(CAST(name AS VARCHAR)) name, CAST(target AS VARCHAR) tgt,
                   count(*) n, min(t) first_seen, max(t) last_seen
            FROM ev WHERE key IS NOT NULL AND target IS NOT NULL GROUP BY k, tgt),
        ranked AS (
            SELECT *, n / sum(n) OVER (PARTITION BY k) share,
                   row_number() OVER (PARTITION BY k ORDER BY n DESC, last_seen DESC, tgt) = 1 is_primary
            FROM agg)
        SELECT '{source}', k, name, tgt, n, share, first_seen, last_seen, '{method}', is_primary
        FROM ranked WHERE n >= {min_n} OR share >= {min_share} OR is_primary
    """)
    return con.execute(f"SELECT count(*) FROM {table} WHERE source = ?", [source]).fetchone()[0]


def insert_category_votes(con, table: str, source: str, evidence_sql: str) -> None:
    """Ligas e torneios: (key, name, target) contados por série ligada."""
    con.execute(f"""
        INSERT INTO {table}
        WITH ev AS ({evidence_sql}),
        agg AS (SELECT CAST(key AS VARCHAR) k, any_value(CAST(name AS VARCHAR)) name, CAST(target AS VARCHAR) tgt,
                       count(*) n FROM ev WHERE key IS NOT NULL AND target IS NOT NULL GROUP BY k, tgt)
        SELECT '{source}', k, name, tgt, n, n / sum(n) OVER (PARTITION BY k),
               row_number() OVER (PARTITION BY k ORDER BY n DESC, tgt) = 1
        FROM agg
    """)


# ----------------------------------------------------------------------------- Leaguepedia

def link_leaguepedia(con) -> None:
    con.execute(r"""
        CREATE OR REPLACE TABLE stg.lp_games AS
        SELECT CAST(GameId AS VARCHAR) lp_game_id, nullif(CAST(MatchId AS VARCHAR), '') lp_match_id,
               nullif(OverviewPage, '') lp_tournament_page,
               nullif(WinTeam, '') win_team, nullif(LossTeam, '') loss_team,
               nullif(Team1, '') team1, nullif(Team2, '') team2,
               try_cast(N_GameInMatch AS INTEGER) n_game, try_cast(DateTime_UTC AS TIMESTAMP) game_time,
               upper(replace(coalesce(nullif(RiotPlatformGameId, ''),
                                      nullif(RiotPlatformId, '') || '_' || nullif(RiotGameId, '')), '/', '_')) k_plat,
               nullif(MatchHistory, '') url,
               nullif(regexp_extract(MatchHistory, 'bmid=(\d+)', 1), '') bmid
        FROM raw.lp_scoreboardgames
        WHERE nullif(CAST(GameId AS VARCHAR), '') IS NOT NULL
        QUALIFY row_number() OVER (PARTITION BY GameId ORDER BY DateTime_UTC) = 1;

        INSERT INTO link.game
        WITH g AS (SELECT game_id, series_id, game_number, url, upper(replace(game_id, '/', '_')) k_plat,
                          nullif(regexp_extract(url, 'bmid=(\d+)', 1), '') bmid FROM core.fct_game),
        cand AS (
            SELECT g.game_id, g.series_id, l.lp_game_id, 1 prio, 'riot_platform_id' method
            FROM g JOIN stg.lp_games l USING (k_plat)
            UNION ALL
            SELECT g.game_id, g.series_id, l.lp_game_id, 2, 'lpl_bmid_game_number'
            FROM g JOIN stg.lp_games l ON g.bmid = l.bmid AND g.game_number = l.n_game
            UNION ALL
            SELECT g.game_id, g.series_id, l.lp_game_id, 3, 'url_game_number'
            FROM g JOIN stg.lp_games l ON g.url = l.url AND g.game_number = l.n_game WHERE l.bmid IS NULL),
        r1 AS (SELECT * FROM cand QUALIFY row_number() OVER (PARTITION BY game_id ORDER BY prio, lp_game_id) = 1)
        SELECT 'leaguepedia', lp_game_id, game_id, series_id, method FROM r1
        QUALIFY row_number() OVER (PARTITION BY lp_game_id ORDER BY prio, game_id) = 1;
    """)
    # times: o vencedor do jogo no core é o WinTeam da Leaguepedia (não depende de lado)
    insert_votes(con, "link.team", "leaguepedia", """
        SELECT l.win_team key, l.win_team name,
               CASE WHEN g.blue_win = 1 THEN g.blue_team_id ELSE g.red_team_id END target, g.game_time t
        FROM link.game lg JOIN stg.lp_games l ON lg.source = 'leaguepedia' AND l.lp_game_id = lg.source_game_id
        JOIN core.fct_game g ON g.game_id = lg.game_id
        UNION ALL
        SELECT l.loss_team, l.loss_team, CASE WHEN g.blue_win = 1 THEN g.red_team_id ELSE g.blue_team_id END, g.game_time
        FROM link.game lg JOIN stg.lp_games l ON lg.source = 'leaguepedia' AND l.lp_game_id = lg.source_game_id
        JOIN core.fct_game g ON g.game_id = lg.game_id
    """, "games_winner_loser")
    # jogos que não tinham ID em comum: mesmo horário (±3 h), mesmo número do jogo e mesmos times
    con.execute("""
        INSERT INTO link.game
        WITH free_g AS (SELECT g.* FROM core.fct_game g
                        WHERE NOT EXISTS (SELECT 1 FROM link.game x WHERE x.source = 'leaguepedia' AND x.game_id = g.game_id)),
        free_l AS (SELECT l.* FROM stg.lp_games l
                   WHERE NOT EXISTS (SELECT 1 FROM link.game x WHERE x.source = 'leaguepedia' AND x.source_game_id = l.lp_game_id)),
        tm AS (SELECT source_team_key, team_id FROM link.team WHERE source = 'leaguepedia'),
        cand AS (
            SELECT g.game_id, g.series_id, l.lp_game_id, abs(epoch(g.game_time) - epoch(l.game_time)) dt
            FROM free_g g JOIN free_l l ON l.game_time BETWEEN g.game_time - INTERVAL 3 HOUR AND g.game_time + INTERVAL 3 HOUR
                 AND l.n_game = g.game_number
            JOIN tm w ON w.source_team_key = l.win_team
                 AND w.team_id = CASE WHEN g.blue_win = 1 THEN g.blue_team_id ELSE g.red_team_id END
            JOIN tm lo ON lo.source_team_key = l.loss_team
                 AND lo.team_id = CASE WHEN g.blue_win = 1 THEN g.red_team_id ELSE g.blue_team_id END),
        r1 AS (SELECT * FROM cand QUALIFY row_number() OVER (PARTITION BY game_id ORDER BY dt, lp_game_id) = 1)
        SELECT 'leaguepedia', lp_game_id, game_id, series_id, 'time_teams' FROM r1
        QUALIFY row_number() OVER (PARTITION BY lp_game_id ORDER BY dt, game_id) = 1
    """)
    # séries: a partida da Leaguepedia que tem mais jogos ligados à série
    con.execute("""
        INSERT INTO link.series
        WITH c AS (
            SELECT l.lp_match_id, lg.series_id, count(*) n
            FROM link.game lg JOIN stg.lp_games l ON lg.source = 'leaguepedia' AND l.lp_game_id = lg.source_game_id
            WHERE l.lp_match_id IS NOT NULL GROUP BY ALL),
        r1 AS (SELECT * FROM c QUALIFY row_number() OVER (PARTITION BY lp_match_id ORDER BY n DESC, series_id) = 1)
        SELECT 'leaguepedia', lp_match_id, series_id, NULL, 'via_games', 0 FROM r1
        QUALIFY row_number() OVER (PARTITION BY series_id ORDER BY n DESC, lp_match_id) = 1;

        CREATE OR REPLACE TABLE stg.series_lp_page AS
        SELECT lg.series_id, l.lp_tournament_page tournament_page, count(*) n
        FROM link.game lg JOIN stg.lp_games l ON lg.source = 'leaguepedia' AND l.lp_game_id = lg.source_game_id
        WHERE l.lp_tournament_page IS NOT NULL GROUP BY lg.series_id, l.lp_tournament_page
        QUALIFY row_number() OVER (PARTITION BY lg.series_id ORDER BY count(*) DESC, l.lp_tournament_page) = 1;
    """)
    if has(con, "raw", "lp_scoreboardplayers"):
        redirects = ""
        if has(con, "raw", "lp_playerredirects"):
            redirects = """LEFT JOIN (SELECT AllName, any_value(OverviewPage) page FROM raw.lp_playerredirects
                                     WHERE nullif(AllName, '') IS NOT NULL GROUP BY AllName) r ON r.AllName = sp.Link"""
        page = "coalesce(nullif(r.page, ''), sp.Link)" if redirects else "sp.Link"
        insert_votes(con, "link.player", "leaguepedia", f"""
            SELECT {page} key, sp.Name name, p.player_id target, p.game_time t
            FROM raw.lp_scoreboardplayers sp {redirects}
            JOIN link.game lg ON lg.source = 'leaguepedia' AND lg.source_game_id = CAST(sp.GameId AS VARCHAR)
            JOIN core.fct_player_game p ON p.game_id = lg.game_id
                 AND p.side = CASE CAST(sp.Side AS VARCHAR) WHEN '1' THEN 'blue' WHEN '2' THEN 'red' END
                 AND p.role = link.role_code(sp.Role)
            WHERE nullif(sp.Link, '') IS NOT NULL
        """, "games_side_role")


# ----------------------------------------------------------------------------- nomes conhecidos

def build_aliases(con) -> None:
    lp_codes = ""
    if has(con, "raw", "lp_teams"):
        lp_codes = """
            UNION ALL
            SELECT t.team_id, link.nm(x.Short), 'code', t.first_seen, t.last_seen, t.n_evidence
            FROM link.team t JOIN raw.lp_teams x ON t.source = 'leaguepedia'
                 AND t.source_team_key IN (x.Name, x.OverviewPage)
            WHERE nullif(x.Short, '') IS NOT NULL"""
    con.execute(f"""
        CREATE OR REPLACE TABLE link.team_alias AS
        WITH oe AS (SELECT team_id, team_name name, min(game_time) f, max(game_time) l, count(*) n
                    FROM core.fct_team_game GROUP BY ALL),
        lp AS (SELECT team_id, source_team_name name, first_seen f, last_seen l, n_evidence n
               FROM link.team WHERE source = 'leaguepedia'),
        names AS (SELECT * FROM oe UNION ALL SELECT * FROM lp),
        alias AS (
            SELECT team_id, link.nm(name) alias, 'name' kind, f, l, n FROM names
            UNION ALL SELECT team_id, link.nm_core(name), 'name', f, l, n FROM names
            {lp_codes})
        SELECT team_id, alias, kind, min(f) first_seen, max(l) last_seen, sum(n) n
        FROM alias WHERE alias IS NOT NULL GROUP BY ALL;

        CREATE OR REPLACE TABLE link.team_tokens AS
        SELECT DISTINCT team_id, link.tokens(name) tokens FROM (
            SELECT team_id, team_name name FROM core.fct_team_game
            UNION SELECT team_id, source_team_name FROM link.team WHERE source = 'leaguepedia')
        WHERE len(link.tokens(name)) > 0;

        CREATE OR REPLACE TABLE link.player_alias AS
        WITH oe AS (SELECT player_id, player_name name, min(game_time) f, max(game_time) l, count(*) n
                    FROM core.fct_player_game GROUP BY ALL),
        lp AS (SELECT player_id, source_player_name name, first_seen f, last_seen l, n_evidence n
               FROM link.player WHERE source = 'leaguepedia'
               UNION ALL
               SELECT player_id, regexp_replace(source_player_key, '\\s*\\(.*\\)\\s*$', ''), first_seen, last_seen, n_evidence
               FROM link.player WHERE source = 'leaguepedia')
        SELECT player_id, link.nm(name) alias, min(f) first_seen, max(l) last_seen, sum(n) n
        FROM (SELECT * FROM oe UNION ALL SELECT * FROM lp)
        WHERE link.nm(name) IS NOT NULL GROUP BY ALL;
    """)
    if has(con, "raw", "lp_playerredirects"):
        # nomes antigos/alternativos de cada página de jogador na Leaguepedia
        con.execute("""
            INSERT INTO link.player_alias
            SELECT p.player_id, link.nm_page(r.AllName), min(p.first_seen), max(p.last_seen), sum(p.n_evidence)
            FROM raw.lp_playerredirects r
            JOIN link.player p ON p.source = 'leaguepedia' AND p.source_player_key = r.OverviewPage AND p.is_primary
            WHERE link.nm_page(r.AllName) IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM link.player_alias a
                              WHERE a.player_id = p.player_id AND a.alias = link.nm_page(r.AllName))
            GROUP BY ALL
        """)


# ----------------------------------------------------------------------------- casamento por data + times

def match_series(con, source: str, ext_sql: str, window_hours: int = WINDOW_HOURS) -> int:
    """Liga séries de uma fonte às séries do core pela data e pelos dois times.

    `ext_sql` precisa devolver: ext_id, t (timestamp), n1, n2 (nomes), c1, c2 (siglas, podem
    ser nulas) e x1, x2 (IDs dos times na fonte, podem ser nulos).

    Cada lado da partida vira uma lista de times candidatos, em níveis de confiança:
    1 nome igual a um nome já visto do time (ou ID da fonte aprendido), 2 sigla igual,
    3 nome parcial (um nome contido no outro, ou todas as palavras de um presentes no
    outro: "kt Challengers" x "KT Rolster Challengers"). A comparação é por **franquia**,
    então um time renomeado (o PandaScore mostra o nome atual em partidas antigas) ainda bate.

    A série da fonte é ligada quando os dois lados batem com os dois times de uma série do
    core dentro da janela de tempo; empates ficam com o nível mais forte e a menor diferença
    de horário, e cada série do core recebe no máximo uma série da fonte. Por último, um
    lado só (nível 4): se um dos times é reconhecido com segurança e tem exatamente uma
    série no core perto do horário (±4 h, e nenhuma outra em ±8 h), a ligação é aceita.
    """
    con.execute(f"SET VARIABLE link_source = '{source}'")
    con.execute(f"CREATE OR REPLACE TEMP TABLE _ext AS SELECT * FROM ({ext_sql}) WHERE t IS NOT NULL")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _ext_names AS
        SELECT CAST(ext_id AS VARCHAR) ext_id, t, 1 side, n1 n, c1 c, CAST(x1 AS VARCHAR) x FROM _ext
        UNION ALL SELECT CAST(ext_id AS VARCHAR), t, 2, n2, c2, CAST(x2 AS VARCHAR) FROM _ext;

        CREATE OR REPLACE TEMP TABLE _side0 AS
        WITH nn AS (SELECT DISTINCT ext_id, t, side, link.nm(n) a FROM _ext_names
                    UNION SELECT DISTINCT ext_id, t, side, link.nm_core(n) FROM _ext_names)
        SELECT DISTINCT nn.ext_id, nn.t, nn.side, al.team_id, 1 lvl
        FROM nn JOIN link.team_alias al ON al.kind = 'name' AND al.alias = nn.a
        UNION
        SELECT DISTINCT e.ext_id, e.t, e.side, al.team_id, 2
        FROM _ext_names e JOIN link.team_alias al ON al.kind = 'code' AND al.alias = link.nm(e.c);

        INSERT INTO _side0
        WITH miss AS (
            SELECT DISTINCT e.ext_id, e.t, e.side, link.nm(e.n) a, link.tokens(e.n) tk FROM _ext_names e
            WHERE NOT EXISTS (SELECT 1 FROM _side0 s WHERE s.ext_id = e.ext_id AND s.side = e.side AND s.lvl = 1)),
        dn AS (SELECT DISTINCT a, tk FROM miss),
        by_sub AS (SELECT DISTINCT dn.a, dn.tk, al.team_id FROM dn JOIN link.team_alias al
                   ON al.kind = 'name' AND length(dn.a) >= 4 AND length(al.alias) >= 4
                  AND (contains(dn.a, al.alias) OR contains(al.alias, dn.a))),
        by_tok AS (SELECT DISTINCT dn.a, dn.tk, t.team_id FROM dn JOIN link.team_tokens t
                   ON len(dn.tk) >= 2 AND len(t.tokens) >= 2
                  AND (list_has_all(t.tokens, dn.tk) OR list_has_all(dn.tk, t.tokens)))
        SELECT DISTINCT m.ext_id, m.t, m.side, h.team_id, 3
        FROM miss m JOIN (SELECT * FROM by_sub UNION SELECT * FROM by_tok) h ON h.a = m.a AND h.tk = m.tk;
    """)
    _with_franchise(con)
    linked = _resolve(con, source, window_hours, "time_teams")

    # segunda rodada: IDs de time da fonte aprendidos nas séries ligadas
    if con.execute("SELECT count(*) FROM _ext_names WHERE x IS NOT NULL").fetchone()[0]:
        con.execute("""
            CREATE OR REPLACE TEMP TABLE _xmap AS
            WITH ev AS (
                SELECT e.x, CASE WHEN (e.side = 1) <> b.swapped THEN s.team1_id ELSE s.team2_id END team_id
                FROM _ext_names e JOIN link.series b ON b.source = getvariable('link_source') AND b.source_series_id = e.ext_id
                JOIN core.fct_series s ON s.series_id = b.series_id
                WHERE e.x IS NOT NULL)
            SELECT x, team_id, count(*) n FROM ev GROUP BY x, team_id
            QUALIFY row_number() OVER (PARTITION BY x ORDER BY count(*) DESC, team_id) = 1 AND count(*) >= 2;

            CREATE OR REPLACE TEMP TABLE _side0 AS
            SELECT DISTINCT e.ext_id, e.t, e.side, m.team_id, 1 lvl FROM _ext_names e JOIN _xmap m USING (x)
            WHERE NOT EXISTS (SELECT 1 FROM link.series b
                              WHERE b.source = getvariable('link_source') AND b.source_series_id = e.ext_id);
        """)
        _with_franchise(con)
        linked += _resolve(con, source, window_hours, "time_team_ids")

    # última rodada: um time só, com uma única série possível perto do horário
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _one AS
        WITH strong AS (
            SELECT s.* FROM _side s
            WHERE s.lvl <= 2 AND NOT EXISTS (SELECT 1 FROM link.series b
                                            WHERE b.source = '{source}' AND b.source_series_id = s.ext_id)),
        near AS (
            SELECT st.ext_id, st.side, ts.series_id, ts.is_team1, ts.start_time,
                   abs(epoch(ts.start_time) - epoch(st.t)) / 3600.0 hdiff
            FROM strong st JOIN core.fct_team_series ts ON ts.franchise_id = st.franchise_id
                 AND ts.start_time BETWEEN st.t - INTERVAL 8 HOUR AND st.t + INTERVAL 8 HOUR),
        uniq AS (SELECT * FROM near QUALIFY count(DISTINCT series_id) OVER (PARTITION BY ext_id, side) = 1)
        SELECT DISTINCT ext_id, series_id, (side = 1) <> (is_team1 = 1) swapped, hdiff FROM uniq WHERE hdiff <= 4
          AND NOT EXISTS (SELECT 1 FROM link.series b WHERE b.source = '{source}' AND b.series_id = uniq.series_id);

        INSERT INTO link.series
        SELECT '{source}', ext_id, series_id, swapped, 'one_team_time', round(hdiff, 2) FROM _one
        QUALIFY count(*) OVER (PARTITION BY ext_id) = 1 AND count(*) OVER (PARTITION BY series_id) = 1;
    """)
    return con.execute("SELECT count(*) FROM link.series WHERE source = ?", [source]).fetchone()[0]


def _with_franchise(con) -> None:
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _side AS
        SELECT s.ext_id, s.t, s.side, d.franchise_id, min(s.lvl) lvl
        FROM _side0 s JOIN core.dim_team d ON d.team_id = s.team_id
        GROUP BY ALL
    """)


def _resolve(con, source: str, window_hours: int, method: str) -> int:
    con.execute(f"SET VARIABLE link_source = '{source}'")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _cand AS
        SELECT s1.ext_id, ts.series_id, greatest(s1.lvl, s2.lvl) lvl,
               abs(epoch(ts.start_time) - epoch(s1.t)) / 3600.0 hdiff, ts.is_team1 = 0 swapped
        FROM _side s1
        JOIN core.fct_team_series ts ON ts.franchise_id = s1.franchise_id
             AND ts.start_time BETWEEN s1.t - INTERVAL {window_hours} HOUR AND s1.t + INTERVAL {window_hours} HOUR
        JOIN _side s2 ON s2.ext_id = s1.ext_id AND s2.side = 2 AND s2.franchise_id = ts.opp_franchise_id
        WHERE s1.side = 1
          AND NOT EXISTS (SELECT 1 FROM link.series b WHERE b.source = '{source}' AND b.series_id = ts.series_id)
          AND NOT EXISTS (SELECT 1 FROM link.series b WHERE b.source = '{source}' AND b.source_series_id = s1.ext_id);

        INSERT INTO link.series
        WITH c AS (SELECT ext_id, series_id, swapped, min(lvl) lvl, min(hdiff) hdiff FROM _cand GROUP BY ALL),
        r1 AS (SELECT * FROM c QUALIFY row_number() OVER (PARTITION BY ext_id ORDER BY lvl, hdiff, series_id) = 1)
        SELECT '{source}', ext_id, series_id, swapped,
               '{method}_' || CASE lvl WHEN 1 THEN 'name' WHEN 2 THEN 'code' ELSE 'partial_name' END, round(hdiff, 2)
        FROM r1 QUALIFY row_number() OVER (PARTITION BY series_id ORDER BY lvl, hdiff, ext_id) = 1;
    """)
    return con.execute("SELECT count(*) FROM link.series WHERE source = ? AND method LIKE ?",
                       [source, method + "%"]).fetchone()[0]


def series_team_votes(con, source: str, team_names_sql: str | None = None) -> None:
    """IDs de time da fonte -> time do core, a partir das séries ligadas (respeitando a ordem)."""
    names = f"LEFT JOIN ({team_names_sql}) tn ON tn.x = e.x" if team_names_sql else ""
    name_col = "coalesce(tn.name, e.n)" if team_names_sql else "e.n"
    insert_votes(con, "link.team", source, f"""
        SELECT e.x key, {name_col} name,
               CASE WHEN (e.side = 1) <> b.swapped THEN s.team1_id ELSE s.team2_id END target, s.start_time t
        FROM _ext_names e {names}
        JOIN link.series b ON b.source = '{source}' AND b.source_series_id = e.ext_id
        JOIN core.fct_series s ON s.series_id = b.series_id
        WHERE e.x IS NOT NULL
    """, "series_teams")


def roster_player_links(con, source: str, roster_sql: str) -> None:
    """Jogadores de elencos (key, name, team_x, t) -> jogador do core.

    Primeiro procura, entre os jogadores que já jogaram pela franquia do time ligado a
    `team_x`, um com o mesmo nome; se não houver, aceita o nome quando ele é de um único
    jogador na base inteira.
    """
    con.execute(f"CREATE OR REPLACE TEMP TABLE _roster AS SELECT * FROM ({roster_sql}) WHERE key IS NOT NULL")
    con.execute("""
            CREATE OR REPLACE TEMP TABLE _team_player_names AS
            SELECT franchise_id, player_id, link.nm(player_name) a, count(*) n, max(game_time) last_game
            FROM core.fct_player_game GROUP BY ALL""")
    con.execute(f"""
        INSERT INTO link.player
        WITH r AS (SELECT CAST(key AS VARCHAR) key, any_value(name) name, link.nm(any_value(name)) a,
                          CAST(team_x AS VARCHAR) team_x, max(t) t FROM _roster GROUP BY key, team_x),
        by_team AS (
            SELECT r.key, r.name, p.player_id, sum(p.n) n, max(p.last_game) last_game
            FROM r JOIN link.team lt ON lt.source = '{source}' AND lt.source_team_key = r.team_x
            JOIN core.dim_team d ON d.team_id = lt.team_id
            JOIN _team_player_names p ON p.franchise_id = d.franchise_id AND p.a = r.a
            GROUP BY ALL),
        best_team AS (SELECT * FROM by_team
                      QUALIFY row_number() OVER (PARTITION BY key ORDER BY n DESC, last_game DESC, player_id) = 1),
        uniq AS (
            SELECT r.key, any_value(r.name) name, any_value(a.player_id) player_id
            FROM r JOIN link.player_alias a ON a.alias = r.a
            WHERE r.key NOT IN (SELECT key FROM best_team)
            GROUP BY r.key HAVING count(DISTINCT a.player_id) = 1)
        SELECT '{source}', key, name, player_id, n, 1.0, NULL, last_game, 'roster_team_name', true FROM best_team
        UNION ALL
        SELECT '{source}', key, name, player_id, 0, 1.0, NULL, NULL, 'name_unique', true FROM uniq
    """)


# ----------------------------------------------------------------------------- lolesports

def link_lolesports(con) -> None:
    has_games = has(con, "raw", "les_match_games")
    mg_cte = """, mg AS (SELECT CAST(match_id AS VARCHAR) match_id,
                         any_value(CAST(blue_team_id AS VARCHAR)) b_id, any_value(blue_team_code) b_code,
                         any_value(CAST(red_team_id AS VARCHAR)) r_id, any_value(red_team_code) r_code
                  FROM raw.les_match_games WHERE try_cast(game_number AS INTEGER) = 1 GROUP BY 1)""" if has_games else ""
    xcols = """CASE WHEN mg.b_code = s.team1_code THEN mg.b_id WHEN mg.r_code = s.team1_code THEN mg.r_id END x1,
               CASE WHEN mg.b_code = s.team2_code THEN mg.b_id WHEN mg.r_code = s.team2_code THEN mg.r_id END x2""" \
        if has_games else "NULL x1, NULL x2"
    mg_join = "LEFT JOIN mg ON mg.match_id = CAST(s.match_id AS VARCHAR)" if has_games else ""
    n = match_series(con, "lolesports", f"""
        WITH base AS (SELECT 1){mg_cte}
        SELECT CAST(s.match_id AS VARCHAR) ext_id, try_cast(s.start_time AS TIMESTAMP) t,
               s.team1_name n1, s.team2_name n2, s.team1_code c1, s.team2_code c2, {xcols}
        FROM raw.les_schedule s {mg_join}
        WHERE s.state = 'completed' AND coalesce(s.type, 'match') = 'match'
    """)
    log.info("    lolesports: %d séries ligadas", n)
    team_names = "SELECT CAST(team_id AS VARCHAR) x, any_value(name) name FROM raw.les_teams GROUP BY 1" \
        if has(con, "raw", "les_teams") else None
    if has_games:
        con.execute("""
            INSERT INTO link.game
            SELECT 'lolesports', CAST(mg.game_id AS VARCHAR), g.game_id, g.series_id, 'series_game_number'
            FROM raw.les_match_games mg
            JOIN link.series ls ON ls.source = 'lolesports' AND ls.source_series_id = CAST(mg.match_id AS VARCHAR)
            JOIN core.fct_game g ON g.series_id = ls.series_id AND g.series_game_seq = try_cast(mg.game_number AS INTEGER)
            WHERE coalesce(mg.state, 'completed') = 'completed'
            QUALIFY row_number() OVER (PARTITION BY mg.game_id ORDER BY g.game_id) = 1
        """)
    # os campos azul/vermelho do lolesports nem sempre correspondem ao lado real do jogo
    # (em 2023-24 vêm invertidos em boa parte das partidas); por isso os IDs de time são
    # ligados pela orientação da série, que vem dos nomes
    series_team_votes(con, "lolesports", team_names)
    if has(con, "raw", "les_team_players"):
        roster_player_links(con, "lolesports", """
            SELECT CAST(player_id AS VARCHAR) key, summoner_name name, CAST(team_id AS VARCHAR) team_x, NULL::TIMESTAMP t
            FROM raw.les_team_players""")
    insert_category_votes(con, "link.league", "lolesports", """
        SELECT s.league_slug key, s.league_name name, f.league target
        FROM link.series b JOIN raw.les_schedule s ON b.source = 'lolesports' AND CAST(s.match_id AS VARCHAR) = b.source_series_id
        JOIN core.fct_series f ON f.series_id = b.series_id""")
    if has_games:
        insert_category_votes(con, "link.tournament", "lolesports", """
            SELECT DISTINCT CAST(mg.tournament_id AS VARCHAR) key, mg.league_slug name, p.tournament_page target, b.series_id
            FROM link.series b JOIN raw.les_match_games mg ON b.source = 'lolesports' AND CAST(mg.match_id AS VARCHAR) = b.source_series_id
            JOIN stg.series_lp_page p ON p.series_id = b.series_id""")


# ----------------------------------------------------------------------------- PandaScore

def link_pandascore(con) -> None:
    n = match_series(con, "pandascore", """
        SELECT CAST(id AS VARCHAR) ext_id, try_cast(coalesce(begin_at, scheduled_at) AS TIMESTAMP) t,
               team1_name n1, team2_name n2, team1_acronym c1, team2_acronym c2,
               CAST(try_cast(team1_id AS BIGINT) AS VARCHAR) x1, CAST(try_cast(team2_id AS BIGINT) AS VARCHAR) x2
        FROM raw.ps_matches_past WHERE status = 'finished'
    """)
    log.info("    PandaScore: %d séries ligadas", n)
    team_names = "SELECT CAST(id AS VARCHAR) x, any_value(name) name FROM raw.ps_teams GROUP BY 1" \
        if has(con, "raw", "ps_teams") else None
    series_team_votes(con, "pandascore", team_names)
    con.execute("""
        INSERT INTO link.game
        WITH g AS (
            SELECT CAST(m.id AS VARCHAR) match_id, gm.id game_id, gm.position, gm.status
            FROM raw.ps_matches_past m,
                 unnest(from_json(json_extract(m.raw_json, '$.games'),
                                  '[{"id": "BIGINT", "position": "INTEGER", "status": "VARCHAR"}]')) AS u(gm))
        SELECT 'pandascore', CAST(g.game_id AS VARCHAR), f.game_id, f.series_id, 'series_game_position'
        FROM g JOIN link.series ls ON ls.source = 'pandascore' AND ls.source_series_id = g.match_id
        JOIN core.fct_game f ON f.series_id = ls.series_id AND f.series_game_seq = g.position
        WHERE coalesce(g.status, 'finished') = 'finished'
        QUALIFY row_number() OVER (PARTITION BY g.game_id ORDER BY f.game_id) = 1
    """)
    rosters = []
    if has(con, "raw", "ps_team_players"):
        rosters.append("SELECT CAST(player_id AS VARCHAR) key, player_name name, CAST(team_id AS VARCHAR) team_x, "
                       "NULL::TIMESTAMP t FROM raw.ps_team_players")
    if has(con, "raw", "ps_players"):
        rosters.append("SELECT CAST(id AS VARCHAR), name, CAST(try_cast(current_team_id AS BIGINT) AS VARCHAR), "
                       "NULL::TIMESTAMP FROM raw.ps_players")
    if rosters:
        roster_player_links(con, "pandascore", " UNION ALL ".join(rosters))
    league_name = "coalesce(l.name, json_extract_string(m.raw_json, '$.league.name'))" if has(con, "raw", "ps_leagues") \
        else "json_extract_string(m.raw_json, '$.league.name')"
    league_join = "LEFT JOIN raw.ps_leagues l ON l.id = m.league_id" if has(con, "raw", "ps_leagues") else ""
    insert_category_votes(con, "link.league", "pandascore", f"""
        SELECT CAST(m.league_id AS VARCHAR) key, {league_name} name, f.league target
        FROM link.series b JOIN raw.ps_matches_past m ON b.source = 'pandascore' AND CAST(m.id AS VARCHAR) = b.source_series_id
        {league_join} JOIN core.fct_series f ON f.series_id = b.series_id""")
    insert_category_votes(con, "link.tournament", "pandascore", """
        SELECT CAST(m.tournament_id AS VARCHAR) key,
               json_extract_string(m.raw_json, '$.serie.full_name') || ' - ' || json_extract_string(m.raw_json, '$.tournament.name') name,
               p.tournament_page target
        FROM link.series b JOIN raw.ps_matches_past m ON b.source = 'pandascore' AND CAST(m.id AS VARCHAR) = b.source_series_id
        JOIN stg.series_lp_page p ON p.series_id = b.series_id""")


# ----------------------------------------------------------------------------- PandaSkill

def link_pandaskill(con) -> None:
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _ps_games AS
        SELECT CAST(game_id AS VARCHAR) game_id, CAST(match_id AS VARCHAR) match_id, try_cast(date AS TIMESTAMP) t,
               any_value(series_name) series_name, any_value(tournament_name) tournament_name, any_value(league_name) league_name
        FROM raw.pskill_game_metadata GROUP BY ALL;
        CREATE OR REPLACE TEMP TABLE _ps_teams AS
        SELECT CAST(game_id AS VARCHAR) game_id, CAST(team_id AS VARCHAR) team_id, any_value(team_name) team_name,
               any_value(team_acronym) acr, bool_or(CAST(win AS BOOLEAN)) win
        FROM raw.pskill_game_players_stats GROUP BY ALL;
    """)
    n = match_series(con, "pandaskill", """
        WITH mt AS (SELECT g.match_id, t.team_id, any_value(t.team_name) n, any_value(t.acr) c, min(g.t) t
                    FROM _ps_games g JOIN _ps_teams t USING (game_id) GROUP BY ALL),
        m2 AS (SELECT match_id, min(t) t, list(team_id ORDER BY team_id) ids, list(n ORDER BY team_id) ns,
                      list(c ORDER BY team_id) cs
               FROM mt GROUP BY match_id HAVING count(*) = 2)
        SELECT match_id ext_id, t, ns[1] n1, ns[2] n2, cs[1] c1, cs[2] c2, ids[1] x1, ids[2] x2 FROM m2
    """)
    log.info("    PandaSkill: %d séries ligadas", n)
    con.execute("""
        INSERT INTO link.game
        WITH g AS (SELECT *, row_number() OVER (PARTITION BY match_id ORDER BY t, game_id) pos FROM _ps_games)
        SELECT 'pandaskill', g.game_id, f.game_id, f.series_id, 'series_game_order'
        FROM g JOIN link.series ls ON ls.source = 'pandaskill' AND ls.source_series_id = g.match_id
        JOIN core.fct_game f ON f.series_id = ls.series_id AND f.series_game_seq = g.pos
    """)
    # times e jogadores pelo jogo ligado: o time vencedor de um lado é o vencedor do outro
    insert_votes(con, "link.team", "pandaskill", """
        SELECT t.team_id key, t.team_name name,
               CASE WHEN t.win = (f.blue_win = 1) THEN f.blue_team_id ELSE f.red_team_id END target, f.game_time t
        FROM link.game lg JOIN _ps_teams t ON lg.source = 'pandaskill' AND t.game_id = lg.source_game_id
        JOIN core.fct_game f ON f.game_id = lg.game_id
    """, "games_winner_loser")
    insert_votes(con, "link.player", "pandaskill", """
        SELECT CAST(s.player_id AS VARCHAR) key, s.player_name name, p.player_id target, p.game_time t
        FROM raw.pskill_game_players_stats s
        JOIN link.game lg ON lg.source = 'pandaskill' AND lg.source_game_id = CAST(s.game_id AS VARCHAR)
        JOIN core.fct_game f ON f.game_id = lg.game_id
        JOIN core.fct_player_game p ON p.game_id = lg.game_id AND p.role = link.role_code(s.role)
             AND p.side = CASE WHEN CAST(s.win AS BOOLEAN) = (f.blue_win = 1) THEN 'blue' ELSE 'red' END
    """, "games_result_role")
    insert_category_votes(con, "link.league", "pandaskill", """
        SELECT DISTINCT g.league_name key, g.league_name name, f.league target, b.series_id
        FROM link.series b JOIN _ps_games g ON b.source = 'pandaskill' AND g.match_id = b.source_series_id
        JOIN core.fct_series f ON f.series_id = b.series_id""")
    insert_category_votes(con, "link.tournament", "pandaskill", """
        SELECT DISTINCT g.series_name || ' - ' || g.tournament_name key, g.series_name || ' - ' || g.tournament_name name,
               p.tournament_page target, b.series_id
        FROM link.series b JOIN _ps_games g ON b.source = 'pandaskill' AND g.match_id = b.source_series_id
        JOIN stg.series_lp_page p ON p.series_id = b.series_id""")


# ----------------------------------------------------------------------------- EsportsBench e OddsPapi

def link_esportsbench(con) -> None:
    con.execute("""
        INSERT INTO link.series
        SELECT DISTINCT 'esportsbench', e.match_id, lp.series_id, NULL::BOOLEAN, 'leaguepedia_match_id', 0
        FROM raw.ebench_matches e
        JOIN link.series lp ON lp.source = 'leaguepedia' AND lp.source_series_id = e.match_id
        QUALIFY row_number() OVER (PARTITION BY e.match_id ORDER BY lp.series_id) = 1
    """)


def link_oddspapi(con) -> None:
    n = match_series(con, "oddspapi", """
        SELECT CAST(fixtureId AS VARCHAR) ext_id, try_cast(startTime AS TIMESTAMP) t,
               participant1Name n1, participant2Name n2, NULL c1, NULL c2,
               CAST(participant1Id AS VARCHAR) x1, CAST(participant2Id AS VARCHAR) x2
        FROM raw.odds_fixtures
    """)
    log.info("    OddsPapi: %d séries ligadas", n)
    series_team_votes(con, "oddspapi")


# ----------------------------------------------------------------------------- GCD (contratos)

def link_gcd(con) -> None:
    parts = []
    if has(con, "raw", "lpgcd_snapshots"):
        parts.append("SELECT league, team, summoner, try_cast(snapshot_date AS TIMESTAMP) d FROM raw.lpgcd_snapshots")
    if has(con, "raw", "gcd_contracts_history"):
        parts.append("SELECT league, team, summoner, try_cast(seen_at AS TIMESTAMP) d FROM raw.gcd_contracts_history")
    if has(con, "raw", "gcd_contracts"):
        parts.append("SELECT league, team, summoner, current_timestamp::TIMESTAMP d FROM raw.gcd_contracts")
    if not parts:
        return
    con.execute(f"""
        CREATE OR REPLACE TABLE stg.gcd_entries AS
        SELECT nullif(trim(league), '') league, trim(team) team, trim(summoner) summoner,
               min(d) first_seen, max(d) last_seen
        FROM ({' UNION ALL '.join(parts)})
        WHERE nullif(trim(team), '') IS NOT NULL GROUP BY ALL;

        -- time: nome igual (ou forma núcleo) a um nome conhecido; com mais de um candidato,
        -- fica o que jogava no período do contrato (maior sobreposição de datas)
        INSERT INTO link.team
        WITH k0 AS (SELECT coalesce(league, '') || '|' || team key, any_value(team) team,
                           min(first_seen) f, max(last_seen) l FROM stg.gcd_entries GROUP BY 1),
        k AS (SELECT *, link.nm(team) a FROM k0 UNION SELECT *, link.nm_core(team) FROM k0),
        cand AS (
            SELECT k.key, k.team, a.team_id, a.n, a.last_seen,
                   greatest(0, date_diff('day', greatest(k.f, a.first_seen), least(k.l, a.last_seen))) overlap
            FROM k JOIN link.team_alias a ON a.kind = 'name' AND a.alias = k.a),
        best AS (SELECT key, team, team_id, max(overlap) overlap, max(n) n, max(last_seen) last_seen FROM cand GROUP BY ALL)
        SELECT 'gcd', key, team, team_id, n, 1.0, NULL, last_seen, 'name_period', true FROM best
        QUALIFY row_number() OVER (PARTITION BY key ORDER BY overlap DESC, last_seen DESC, n DESC, team_id) = 1;
    """)
    roster_player_links(con, "gcd", """
        SELECT coalesce(league, '') || '|' || team || '|' || summoner key, summoner name,
               coalesce(league, '') || '|' || team team_x, max(last_seen) t
        FROM stg.gcd_entries WHERE nullif(summoner, '') IS NOT NULL GROUP BY ALL""")


# ----------------------------------------------------------------------------- torneios, ligas e contexto

def build_tournaments(con) -> None:
    if has(con, "raw", "lp_tournaments"):
        con.execute("""
            CREATE OR REPLACE TABLE core.dim_tournament AS
            SELECT OverviewPage tournament_page, nullif(Name, '') name, nullif(League, '') lp_league,
                   nullif(Region, '') region, try_cast(nullif(DateStart, '') AS DATE) date_start,
                   try_cast(nullif(Date, '') AS DATE) date_end, nullif(TournamentLevel, '') tournament_level,
                   coalesce(IsPlayoffs IN ('1', 'true', 'Yes'), false) is_playoffs,
                   coalesce(IsQualifier IN ('1', 'true', 'Yes'), false) is_qualifier,
                   coalesce(IsOfficial IN ('1', 'true', 'Yes'), false) is_official,
                   nullif(Split, '') split, try_cast(nullif(Year, '') AS INTEGER) "year",
                   nullif(EventType, '') event_type, nullif(Country, '') country,
                   nullif(Prizepool, '') prizepool, nullif(Currency, '') currency
            FROM raw.lp_tournaments WHERE nullif(OverviewPage, '') IS NOT NULL
            QUALIFY row_number() OVER (PARTITION BY OverviewPage ORDER BY Name) = 1
        """)
        insert_category_votes(con, "link.league", "leaguepedia", """
            SELECT t.lp_league key, t.lp_league name, f.league target
            FROM stg.series_lp_page p JOIN core.dim_tournament t USING (tournament_page)
            JOIN core.fct_series f USING (series_id)""")


def build_context(con) -> None:
    con.execute("""
        CREATE OR REPLACE TABLE core.series_context AS
        SELECT s.series_id, s.league, s.start_time,
               NULL::VARCHAR tournament_page, NULL::VARCHAR tournament_name, NULL::VARCHAR tournament_level,
               NULL::BOOLEAN is_playoffs, NULL::BOOLEAN is_qualifier,
               NULL::VARCHAR lp_match_id, NULL::VARCHAR lp_phase, NULL::VARCHAR lp_round, NULL::VARCHAR lp_tab,
               NULL::VARCHAR lp_shown_round, NULL::VARCHAR lp_group_name, NULL::VARCHAR lp_match_day,
               NULL::BOOLEAN lp_is_tiebreaker, NULL::INTEGER lp_best_of,
               NULL::VARCHAR les_match_id, NULL::VARCHAR les_block_name, NULL::VARCHAR les_tournament_id,
               NULL::VARCHAR les_stage_name, NULL::VARCHAR les_stage_type, NULL::VARCHAR les_section_name,
               NULL::INTEGER les_match_order, NULL::VARCHAR les_previous_match_ids, NULL::VARCHAR les_context_method,
               NULL::VARCHAR ps_match_id, NULL::VARCHAR ps_tournament_id, NULL::VARCHAR ps_tier,
               NULL::VARCHAR ps_tournament_type, NULL::VARCHAR ps_serie_id
        FROM core.fct_series s
    """)
    if has(con, "stg", "series_lp_page"):
        con.execute("""
            UPDATE core.series_context c SET tournament_page = p.tournament_page
            FROM stg.series_lp_page p WHERE p.series_id = c.series_id
        """)
    if has(con, "core", "dim_tournament"):
        con.execute("""
            UPDATE core.series_context c SET tournament_name = t.name, tournament_level = t.tournament_level,
                   is_playoffs = t.is_playoffs, is_qualifier = t.is_qualifier
            FROM core.dim_tournament t WHERE t.tournament_page = c.tournament_page
        """)
    if has(con, "raw", "lp_matchschedule"):
        con.execute("""
            UPDATE core.series_context c SET lp_match_id = b.source_series_id, lp_phase = nullif(m.Phase, ''),
                   lp_round = nullif(m.Round, ''), lp_tab = nullif(m.Tab, ''), lp_shown_round = nullif(m.ShownRound, ''),
                   lp_group_name = nullif(m.GroupName, ''), lp_match_day = nullif(CAST(m.MatchDay AS VARCHAR), ''),
                   lp_is_tiebreaker = CAST(m.IsTiebreaker AS VARCHAR) IN ('1', 'true', 'Yes'),
                   lp_best_of = try_cast(m.BestOf AS INTEGER)
            FROM link.series b JOIN raw.lp_matchschedule m ON CAST(m.MatchId AS VARCHAR) = b.source_series_id
            WHERE b.source = 'leaguepedia' AND b.series_id = c.series_id
        """)
    if has(con, "raw", "les_schedule"):
        con.execute("""
            UPDATE core.series_context c SET les_match_id = b.source_series_id, les_block_name = s.block_name
            FROM link.series b JOIN raw.les_schedule s ON CAST(s.match_id AS VARCHAR) = b.source_series_id
            WHERE b.source = 'lolesports' AND b.series_id = c.series_id
        """)
    if has(con, "raw", "les_standings_matches"):
        _link_standings(con)
    if has(con, "raw", "ps_matches_past"):
        if has(con, "raw", "ps_tournaments"):
            tier, ttype = "pt.tier", "pt.type"
            tjoin = "LEFT JOIN raw.ps_tournaments pt ON pt.id = m.tournament_id"
        else:
            tier = "json_extract_string(m.raw_json, '$.tournament.tier')"
            ttype = "json_extract_string(m.raw_json, '$.tournament.type')"
            tjoin = ""
        con.execute(f"""
            UPDATE core.series_context c SET ps_match_id = x.ps_match_id, ps_tournament_id = x.tid,
                   ps_tier = x.tier, ps_tournament_type = x.ttype, ps_serie_id = x.serie
            FROM (SELECT b.series_id, b.source_series_id ps_match_id, CAST(m.tournament_id AS VARCHAR) tid,
                         CAST(m.serie_id AS VARCHAR) serie, {tier} tier, {ttype} ttype
                  FROM link.series b JOIN raw.ps_matches_past m ON CAST(m.id AS VARCHAR) = b.source_series_id
                  {tjoin} WHERE b.source = 'pandascore') x
            WHERE x.series_id = c.series_id
        """)


def _link_standings(con) -> None:
    """Etapa/chave do lolesports: pela partida ligada ou, para torneios antigos (fora do
    calendário coletado), pelo único confronto entre os dois times no período do torneio."""
    tdates = """LEFT JOIN (SELECT CAST(tournament_id AS VARCHAR) tid, try_cast(start_date AS DATE) d0,
                                  try_cast(end_date AS DATE) d1 FROM raw.les_tournaments) td ON td.tid = sm.tid""" \
        if has(con, "raw", "les_tournaments") else ""
    dates = "td.d0, td.d1" if tdates else "NULL::DATE d0, NULL::DATE d1"
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _sm AS
        SELECT sm.*, {dates} FROM (
            SELECT CAST(match_id AS VARCHAR) match_id, CAST(tournament_id AS VARCHAR) tid, stage_name, stage_type,
                   section_name, try_cast(match_order AS INTEGER) match_order, CAST(previous_match_ids AS VARCHAR) prev,
                   CAST(team1_id AS VARCHAR) t1, CAST(team2_id AS VARCHAR) t2
            FROM raw.les_standings_matches
            QUALIFY row_number() OVER (PARTITION BY match_id ORDER BY stage_order, section_order) = 1) sm {tdates};

        CREATE OR REPLACE TEMP TABLE _sm_link AS
        SELECT b.series_id, sm.*, 'lolesports_match_id' method
        FROM _sm sm JOIN link.series b ON b.source = 'lolesports' AND b.source_series_id = sm.match_id;

        INSERT INTO _sm_link
        WITH free AS (SELECT * FROM _sm WHERE match_id NOT IN (SELECT match_id FROM _sm_link)
                      AND d0 IS NOT NULL AND t1 IS NOT NULL AND t2 IS NOT NULL),
        tm AS (SELECT source_team_key, team_id FROM link.team WHERE source = 'lolesports'),
        cand AS (
            SELECT DISTINCT f.*, s.series_id
            FROM free f JOIN tm a ON a.source_team_key = f.t1 JOIN tm b ON b.source_team_key = f.t2
            JOIN core.fct_team_series s ON s.team_id = a.team_id AND s.opp_team_id = b.team_id
                 AND s.start_time BETWEEN f.d0 - INTERVAL 1 DAY AND coalesce(f.d1, f.d0) + INTERVAL 2 DAY),
        uniq AS (SELECT * FROM cand
                 QUALIFY count(*) OVER (PARTITION BY match_id) = 1 AND count(*) OVER (PARTITION BY series_id) = 1)
        SELECT series_id, match_id, tid, stage_name, stage_type, section_name, match_order, prev, t1, t2, d0, d1,
               'tournament_teams_unique' FROM uniq
        WHERE series_id NOT IN (SELECT series_id FROM _sm_link);

        UPDATE core.series_context c SET les_tournament_id = l.tid, les_stage_name = l.stage_name,
               les_stage_type = l.stage_type, les_section_name = l.section_name, les_match_order = l.match_order,
               les_previous_match_ids = l.prev, les_context_method = l.method,
               les_match_id = coalesce(c.les_match_id, l.match_id)
        FROM (SELECT * FROM _sm_link QUALIFY row_number() OVER (PARTITION BY series_id ORDER BY method) = 1) l
        WHERE l.series_id = c.series_id;
    """)


# ----------------------------------------------------------------------------- partidas futuras

def build_upcoming(con, now: datetime) -> None:
    """Partidas futuras de todas as fontes, sem duplicatas, com os times ligados ao core."""
    since = now.replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    con.execute(f"SET VARIABLE upcoming_since = TIMESTAMP '{since}' - INTERVAL {UPCOMING_GRACE_HOURS} HOUR")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _up (
            source VARCHAR, ext_id VARCHAR, t TIMESTAMP, n1 VARCHAR, n2 VARCHAR, c1 VARCHAR, c2 VARCHAR,
            x1 VARCHAR, x2 VARCHAR, league_key VARCHAR, tournament_key VARCHAR, best_of INTEGER,
            phase VARCHAR, round VARCHAR)
    """)
    if has(con, "raw", "lp_matchschedule"):
        con.execute("""
            INSERT INTO _up
            SELECT 'leaguepedia', CAST(MatchId AS VARCHAR), try_cast(DateTime_UTC AS TIMESTAMP), nullif(Team1, ''),
                   nullif(Team2, ''), NULL, NULL, nullif(Team1, ''), nullif(Team2, ''), NULL, nullif(OverviewPage, ''),
                   try_cast(BestOf AS INTEGER), nullif(Phase, ''), nullif(Round, '')
            FROM raw.lp_matchschedule
            WHERE try_cast(DateTime_UTC AS TIMESTAMP) >= getvariable('upcoming_since')
              AND coalesce(nullif(CAST(Winner AS VARCHAR), ''), '') = ''
              AND nullif(CAST(MatchId AS VARCHAR), '') IS NOT NULL
        """)
    if has(con, "raw", "les_schedule"):
        con.execute("""
            INSERT INTO _up
            SELECT 'lolesports', CAST(match_id AS VARCHAR), try_cast(start_time AS TIMESTAMP), team1_name, team2_name,
                   team1_code, team2_code, NULL, NULL, league_slug, NULL, try_cast(best_of AS INTEGER), block_name, NULL
            FROM raw.les_schedule
            WHERE state IN ('unstarted', 'inProgress') AND try_cast(start_time AS TIMESTAMP) >= getvariable('upcoming_since')
        """)
    if has(con, "raw", "ps_matches_upcoming"):
        con.execute("""
            INSERT INTO _up
            SELECT 'pandascore', CAST(id AS VARCHAR), try_cast(coalesce(scheduled_at, begin_at) AS TIMESTAMP),
                   team1_name, team2_name, team1_acronym, team2_acronym,
                   CAST(try_cast(team1_id AS BIGINT) AS VARCHAR), CAST(try_cast(team2_id AS BIGINT) AS VARCHAR),
                   CAST(league_id AS VARCHAR), CAST(tournament_id AS VARCHAR), try_cast(number_of_games AS INTEGER), NULL, NULL
            FROM raw.ps_matches_upcoming
            WHERE try_cast(coalesce(scheduled_at, begin_at) AS TIMESTAMP) >= getvariable('upcoming_since')
        """)
    if has(con, "raw", "odds_fixtures"):
        con.execute("""
            INSERT INTO _up
            SELECT 'oddspapi', CAST(fixtureId AS VARCHAR), try_cast(startTime AS TIMESTAMP), participant1Name,
                   participant2Name, NULL, NULL, CAST(participant1Id AS VARCHAR), CAST(participant2Id AS VARCHAR),
                   NULL, tournamentName, NULL, NULL, NULL
            FROM raw.odds_fixtures
            WHERE try_cast(startTime AS TIMESTAMP) >= getvariable('upcoming_since')
              AND coalesce(statusName, '') NOT IN ('Finished', 'Cancelled', 'Canceled')
        """)
    # time de cada lado: pelo ID da fonte (link.team) ou pelo nome/sigla conhecidos; com
    # mais de um candidato, o que jogou mais recentemente
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _up_side AS
        WITH sides AS (
            SELECT source, ext_id, t, 1 side, n1 n, c1 c, x1 x FROM _up
            UNION ALL SELECT source, ext_id, t, 2, n2, c2, x2 FROM _up),
        cand AS (
            SELECT s.source, s.ext_id, s.side, lt.team_id, 0 lvl, lt.last_seen FROM sides s
            JOIN link.team lt ON lt.source = s.source AND lt.source_team_key = s.x
            UNION ALL
            SELECT s.source, s.ext_id, s.side, a.team_id, 1, a.last_seen FROM sides s
            JOIN link.team_alias a ON a.kind = 'name' AND a.alias = link.nm(s.n)
            UNION ALL
            SELECT s.source, s.ext_id, s.side, a.team_id, 1, a.last_seen FROM sides s
            JOIN link.team_alias a ON a.kind = 'name' AND a.alias = link.nm_core(s.n)
            UNION ALL
            SELECT s.source, s.ext_id, s.side, a.team_id, 2, a.last_seen FROM sides s
            JOIN link.team_alias a ON a.kind = 'code' AND a.alias = link.nm(s.c))
        SELECT source, ext_id, side, team_id, lvl FROM cand
        QUALIFY row_number() OVER (PARTITION BY source, ext_id, side ORDER BY lvl, last_seen DESC NULLS LAST, team_id) = 1;

        CREATE OR REPLACE TEMP TABLE _up2 AS
        SELECT u.*, s1.team_id team1_id, s2.team_id team2_id,
               CASE u.source WHEN 'leaguepedia' THEN 1 WHEN 'lolesports' THEN 2 WHEN 'pandascore' THEN 3 ELSE 4 END prio
        FROM _up u
        LEFT JOIN _up_side s1 ON s1.source = u.source AND s1.ext_id = u.ext_id AND s1.side = 1
        LEFT JOIN _up_side s2 ON s2.source = u.source AND s2.ext_id = u.ext_id AND s2.side = 2;
    """)
    # mesma partida em fontes diferentes: horário a até 12 h e o mesmo par de times
    # (pelos IDs do core ou, quando o time é novo, pelo par de nomes normalizados)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE _up_pairs AS
        SELECT a.source a_src, a.ext_id a_id, b.source b_src, b.ext_id b_id
        FROM _up2 a JOIN _up2 b ON a.prio < b.prio AND abs(epoch(a.t) - epoch(b.t)) <= 12 * 3600
         AND (
              (a.team1_id IS NOT NULL AND a.team2_id IS NOT NULL
               AND least(a.team1_id, a.team2_id) = least(b.team1_id, b.team2_id)
               AND greatest(a.team1_id, a.team2_id) = greatest(b.team1_id, b.team2_id))
           OR (least(link.nm(a.n1), link.nm(a.n2)) = least(link.nm(b.n1), link.nm(b.n2))
               AND greatest(link.nm(a.n1), link.nm(a.n2)) = greatest(link.nm(b.n1), link.nm(b.n2))
               -- "TBD x TBD" não identifica partida nenhuma
               AND link.nm(a.n1) NOT IN ('tbd', 'tba') AND link.nm(a.n2) NOT IN ('tbd', 'tba')));

        -- cada linha secundária vai para a partida principal mais prioritária
        CREATE OR REPLACE TEMP TABLE _up_parent AS
        SELECT b_src, b_id, a_src, a_id FROM _up_pairs
        QUALIFY row_number() OVER (PARTITION BY b_src, b_id ORDER BY a_src = 'leaguepedia' DESC, a_id) = 1;
        -- se a principal também é secundária de outra, sobe até a raiz (no máximo 3 saltos)
        UPDATE _up_parent c SET a_src = p.a_src, a_id = p.a_id FROM _up_parent p WHERE p.b_src = c.a_src AND p.b_id = c.a_id;
        UPDATE _up_parent c SET a_src = p.a_src, a_id = p.a_id FROM _up_parent p WHERE p.b_src = c.a_src AND p.b_id = c.a_id;
        UPDATE _up_parent c SET a_src = p.a_src, a_id = p.a_id FROM _up_parent p WHERE p.b_src = c.a_src AND p.b_id = c.a_id;

        CREATE OR REPLACE TABLE core.fct_series_upcoming AS
        WITH roots AS (
            SELECT u.* FROM _up2 u
            WHERE NOT EXISTS (SELECT 1 FROM _up_parent p WHERE p.b_src = u.source AND p.b_id = u.ext_id)),
        members AS (
            SELECT r.source root_src, r.ext_id root_id, r.source, r.ext_id FROM roots r
            UNION ALL
            SELECT p.a_src, p.a_id, p.b_src, p.b_id FROM _up_parent p),
        ids AS (
            SELECT root_src, root_id,
                   max(CASE WHEN source = 'leaguepedia' THEN ext_id END) lp_match_id,
                   max(CASE WHEN source = 'lolesports' THEN ext_id END) les_match_id,
                   max(CASE WHEN source = 'pandascore' THEN ext_id END) ps_match_id,
                   max(CASE WHEN source = 'oddspapi' THEN ext_id END) odds_fixture_id,
                   string_agg(DISTINCT source, ',' ORDER BY source) sources
            FROM members GROUP BY ALL),
        les_league AS (SELECT source_league_key, league FROM link.league WHERE source = 'lolesports' AND is_primary),
        ps_league AS (SELECT source_league_key, league FROM link.league WHERE source = 'pandascore' AND is_primary),
        lp_league AS (SELECT source_league_key, league FROM link.league WHERE source = 'leaguepedia' AND is_primary)
        SELECT r.source || ':' || r.ext_id upcoming_id, r.t start_time, i.sources,
               coalesce(lpl.league, lel.league, psl.league) league,
               CASE WHEN r.source = 'leaguepedia' THEN r.tournament_key END tournament_page,
               r.n1 team1_name, r.n2 team2_name, r.team1_id, r.team2_id,
               d1.franchise_id team1_franchise_id, d2.franchise_id team2_franchise_id,
               r.best_of, r.phase, r.round, i.lp_match_id, i.les_match_id, i.ps_match_id, i.odds_fixture_id
        FROM roots r JOIN ids i ON i.root_src = r.source AND i.root_id = r.ext_id
        LEFT JOIN core.dim_team d1 ON d1.team_id = r.team1_id
        LEFT JOIN core.dim_team d2 ON d2.team_id = r.team2_id
        LEFT JOIN les_league lel ON r.source = 'lolesports' AND lel.source_league_key = r.league_key
        LEFT JOIN ps_league psl ON r.source = 'pandascore' AND psl.source_league_key = r.league_key
        LEFT JOIN (SELECT t.tournament_page, l.league FROM lp_league l
                   JOIN core.dim_tournament t ON t.lp_league = l.source_league_key) lpl
             ON r.source = 'leaguepedia' AND lpl.tournament_page = r.tournament_key
        ORDER BY start_time, upcoming_id
    """ if has(con, "core", "dim_tournament") else """
        CREATE OR REPLACE TEMP TABLE _up_pairs AS SELECT NULL a_src, NULL a_id, NULL b_src, NULL b_id WHERE false;
        CREATE OR REPLACE TABLE core.fct_series_upcoming AS
        SELECT u.source || ':' || u.ext_id upcoming_id, u.t start_time, u.source sources, NULL::VARCHAR league,
               NULL::VARCHAR tournament_page, u.n1 team1_name, u.n2 team2_name, u.team1_id, u.team2_id,
               d1.franchise_id team1_franchise_id, d2.franchise_id team2_franchise_id, u.best_of, u.phase, u.round,
               CASE WHEN u.source = 'leaguepedia' THEN u.ext_id END lp_match_id,
               CASE WHEN u.source = 'lolesports' THEN u.ext_id END les_match_id,
               CASE WHEN u.source = 'pandascore' THEN u.ext_id END ps_match_id,
               CASE WHEN u.source = 'oddspapi' THEN u.ext_id END odds_fixture_id
        FROM _up2 u LEFT JOIN core.dim_team d1 ON d1.team_id = u.team1_id
        LEFT JOIN core.dim_team d2 ON d2.team_id = u.team2_id
        ORDER BY start_time, upcoming_id
    """)


# ----------------------------------------------------------------------------- visões de cruzamento

def build_xrefs(con) -> None:
    con.execute("""
        CREATE OR REPLACE VIEW link.series_xref AS
        SELECT s.series_id,
               max(CASE WHEN l.source = 'leaguepedia' THEN l.source_series_id END) lp_match_id,
               max(CASE WHEN l.source = 'lolesports' THEN l.source_series_id END) les_match_id,
               max(CASE WHEN l.source = 'pandascore' THEN l.source_series_id END) ps_match_id,
               max(CASE WHEN l.source = 'pandaskill' THEN l.source_series_id END) pskill_match_id,
               max(CASE WHEN l.source = 'esportsbench' THEN l.source_series_id END) ebench_match_id,
               max(CASE WHEN l.source = 'oddspapi' THEN l.source_series_id END) odds_fixture_id
        FROM core.fct_series s LEFT JOIN link.series l USING (series_id) GROUP BY s.series_id;

        CREATE OR REPLACE VIEW link.game_xref AS
        SELECT g.game_id, g.series_id,
               max(CASE WHEN l.source = 'leaguepedia' THEN l.source_game_id END) lp_game_id,
               max(CASE WHEN l.source = 'lolesports' THEN l.source_game_id END) les_game_id,
               max(CASE WHEN l.source = 'pandascore' THEN l.source_game_id END) ps_game_id,
               max(CASE WHEN l.source = 'pandaskill' THEN l.source_game_id END) pskill_game_id
        FROM core.fct_game g LEFT JOIN link.game l USING (game_id) GROUP BY g.game_id, g.series_id;

        CREATE OR REPLACE VIEW link.player_xref AS
        SELECT p.player_id, p.player_name,
               max(CASE WHEN l.source = 'leaguepedia' THEN l.source_player_key END) lp_player_page,
               max(CASE WHEN l.source = 'lolesports' THEN l.source_player_key END) les_player_id,
               max(CASE WHEN l.source = 'pandascore' THEN l.source_player_key END) ps_player_id,
               max(CASE WHEN l.source = 'pandaskill' THEN l.source_player_key END) pskill_player_id
        FROM core.dim_player p
        LEFT JOIN (SELECT * FROM link.player
                   QUALIFY row_number() OVER (PARTITION BY source, player_id ORDER BY n_evidence DESC, source_player_key) = 1) l
             USING (player_id)
        GROUP BY p.player_id, p.player_name;
    """)


def build_coverage(con) -> None:
    rows = []
    totals = {"game": "SELECT count(*) FROM core.fct_game", "series": "SELECT count(*) FROM core.fct_series",
              "player": "SELECT count(*) FROM core.dim_player"}
    for entity, key in (("game", "game_id"), ("series", "series_id"), ("player", "player_id")):
        total = con.execute(totals[entity]).fetchone()[0]
        for source, linked in con.execute(
                f"SELECT source, count(DISTINCT {key}) FROM link.{entity} GROUP BY 1 ORDER BY 1").fetchall():
            rows.append((source, entity, total, linked))
    con.execute("CREATE OR REPLACE TABLE link.coverage (source VARCHAR, entity VARCHAR, core_total BIGINT, "
                "core_linked BIGINT, core_share DOUBLE)")
    if rows:
        con.executemany("INSERT INTO link.coverage VALUES (?, ?, ?, ?, round(?::DOUBLE / ?, 4))",
                        [(s, e, t, n, n, t) for s, e, t, n in rows])


# ----------------------------------------------------------------------------- orquestração

def build(con: duckdb.DuckDBPyConnection, now: datetime | None = None) -> dict[str, float]:
    now = now or datetime.now(timezone.utc)
    timings: dict[str, float] = {}

    def step(name, fn, *needs):
        if not all(has(con, "raw", n) for n in needs):
            log.info("    %-22s (fonte ausente)", name)
            return
        t0 = time.monotonic()
        fn()
        timings[name] = round(time.monotonic() - t0, 1)
        log.info("    %-22s %5.1fs", name, timings[name])

    con.execute(SETUP_SQL)
    step("leaguepedia", lambda: link_leaguepedia(con), "lp_scoreboardgames")
    step("aliases", lambda: build_aliases(con))
    step("tournaments", lambda: build_tournaments(con))
    step("lolesports", lambda: link_lolesports(con), "les_schedule")
    step("pandascore", lambda: link_pandascore(con), "ps_matches_past")
    step("pandaskill", lambda: link_pandaskill(con), "pskill_game_metadata", "pskill_game_players_stats")
    step("esportsbench", lambda: link_esportsbench(con), "ebench_matches", "lp_scoreboardgames")
    step("oddspapi", lambda: link_oddspapi(con), "odds_fixtures")
    step("gcd", lambda: link_gcd(con))
    step("context", lambda: build_context(con))
    step("upcoming", lambda: build_upcoming(con, now))
    step("xrefs", lambda: build_xrefs(con))
    step("coverage", lambda: build_coverage(con))
    return timings
