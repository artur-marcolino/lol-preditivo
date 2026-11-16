-- =====================================================================================
-- CORE: o modelo de dados. Dimensões (quem/o quê) e fatos (o que aconteceu).
--
--   fct_series  1 ── N  fct_game  1 ── 2  fct_team_game  1 ── 5  fct_player_game
--                                   └── N fct_draft (picks e bans)
--
-- Ainda sem `franchise_id`: ele é calculado em Python (linhagem de times) e adicionado
-- em 25_franchise.sql.
-- =====================================================================================
CREATE SCHEMA IF NOT EXISTS core;

-- ------------------------------------------------------------------ dim_league
CREATE OR REPLACE TABLE core.dim_league AS
WITH obs AS (
    SELECT league, count(DISTINCT game_id) AS n_games, min(game_time) AS first_game, max(game_time) AS last_game
    FROM stg.oe_team_games GROUP BY 1
)
SELECT
    o.league,
    coalesce(s.league_name, o.league) AS league_name,
    s.region,
    coalesce(s.tier, 4) AS tier,
    CASE coalesce(s.tier, 4) WHEN 1 THEN 'internacional' WHEN 2 THEN 'liga principal'
         WHEN 3 THEN 'segunda divisão / regional' ELSE 'outras' END AS tier_label,
    o.n_games, o.first_game, o.last_game
FROM obs o LEFT JOIN core.seed_leagues s USING (league);

-- ------------------------------------------------------------------ jogos (base)
CREATE OR REPLACE TEMP TABLE game_base AS
SELECT
    b.game_id, b.league, b.season, b.split, b.playoffs, b.game_time, b.game_number,
    b.patch, b.patch_order,
    b.team_id AS blue_team_id, r.team_id AS red_team_id,
    b.team_name AS blue_team_name, r.team_name AS red_team_name,
    CASE WHEN b.win = 1 AND r.win = 0 THEN 1 WHEN b.win = 0 AND r.win = 1 THEN 0 END AS blue_win,
    b.gamelength_s, b.completeness, b.url
FROM stg.oe_team_games b
JOIN stg.oe_team_games r ON r.game_id = b.game_id AND b.side = 'blue' AND r.side = 'red'
-- erro da fonte: o mesmo time nos dois lados (12 jogos). Ficam de fora do modelo.
WHERE b.team_id <> r.team_id;

-- ------------------------------------------------------------------ séries
-- A fonte não tem id de série. Regra: jogos da mesma liga entre o mesmo par de times
-- pertencem à mesma série enquanto o número do jogo cresce (1, 2, 3...) e não passam
-- mais de 18 h entre um jogo e o seguinte.
CREATE OR REPLACE TEMP TABLE game_series AS
WITH g AS (
    SELECT game_id, league, game_time, game_number,
           least(blue_team_id, red_team_id) AS pair_a, greatest(blue_team_id, red_team_id) AS pair_b
    FROM game_base
), o AS (
    SELECT *, lag(game_time) OVER w AS prev_time, lag(game_number) OVER w AS prev_number
    FROM g
    WINDOW w AS (PARTITION BY league, pair_a, pair_b ORDER BY game_time, game_number, game_id)
), f AS (
    SELECT *,
        CASE WHEN prev_time IS NULL OR game_number IS NULL OR prev_number IS NULL
                  OR game_number <= prev_number
                  OR game_time - prev_time > INTERVAL 18 HOUR THEN 1 ELSE 0 END AS new_series
    FROM o
), s AS (
    SELECT *, sum(new_series) OVER (PARTITION BY league, pair_a, pair_b
                                    ORDER BY game_time, game_number, game_id ROWS UNBOUNDED PRECEDING) AS seq
    FROM f
)
SELECT game_id,
       's_' || first_value(game_id) OVER (PARTITION BY league, pair_a, pair_b, seq
                                          ORDER BY game_time, game_number, game_id) AS series_id
FROM s;

-- ------------------------------------------------------------------ fct_game
CREATE OR REPLACE TABLE core.fct_game AS
SELECT g.*, s.series_id,
       row_number() OVER (PARTITION BY s.series_id ORDER BY g.game_time, g.game_number, g.game_id)::INT AS series_game_seq
FROM game_base g JOIN game_series s USING (game_id);

-- ------------------------------------------------------------------ fct_series
-- team1 = time do lado azul no 1º jogo (em séries melhor-de, a escolha de lado do jogo 1
-- é definida antes da série; em Bo1 o lado já vem no calendário).
CREATE OR REPLACE TABLE core.fct_series AS
WITH first_game AS (SELECT * FROM core.fct_game WHERE series_game_seq = 1),
agg AS (
    SELECT g.series_id,
        count(*)::INT AS games_played,
        min(g.game_time) AS start_time,
        max(g.game_time) AS last_game_time,
        sum(CASE WHEN (g.blue_team_id = f.blue_team_id AND g.blue_win = 1)
                   OR (g.red_team_id  = f.blue_team_id AND g.blue_win = 0) THEN 1 ELSE 0 END)::INT AS team1_wins,
        sum(CASE WHEN (g.blue_team_id = f.red_team_id AND g.blue_win = 1)
                   OR (g.red_team_id  = f.red_team_id AND g.blue_win = 0) THEN 1 ELSE 0 END)::INT AS team2_wins,
        count(*) FILTER (WHERE g.blue_win IS NULL)::INT AS games_without_result,
        min(g.game_number) AS min_game_number
    FROM core.fct_game g JOIN first_game f USING (series_id)
    GROUP BY 1
)
SELECT
    a.series_id, f.league, f.season, f.split, f.playoffs, f.patch, f.patch_order,
    a.start_time, a.last_game_time,
    f.blue_team_id AS team1_id, f.red_team_id AS team2_id,
    f.blue_team_name AS team1_name, f.red_team_name AS team2_name,
    a.games_played, a.team1_wins, a.team2_wins,
    CASE WHEN a.team1_wins > a.team2_wins THEN 1 WHEN a.team1_wins < a.team2_wins THEN 0 END AS team1_win,
    -- formato inferido pelo placar: 2-0 ou 2-1 -> Bo3; 3-x -> Bo5; 1-0 -> Bo1
    CASE WHEN a.team1_wins <> a.team2_wins THEN 2 * greatest(a.team1_wins, a.team2_wins) - 1 END AS best_of,
    (a.games_without_result = 0 AND a.team1_wins <> a.team2_wins AND coalesce(a.min_game_number, 1) = 1) AS is_clean
FROM agg a JOIN first_game f USING (series_id);

-- ------------------------------------------------------------------ escalações
CREATE OR REPLACE TEMP TABLE lineups AS
SELECT game_id, team_id,
    max(player_id) FILTER (WHERE role = 'top') AS top_player_id,
    max(player_id) FILTER (WHERE role = 'jng') AS jng_player_id,
    max(player_id) FILTER (WHERE role = 'mid') AS mid_player_id,
    max(player_id) FILTER (WHERE role = 'bot') AS bot_player_id,
    max(player_id) FILTER (WHERE role = 'sup') AS sup_player_id,
    list(player_id ORDER BY player_id) AS players
FROM stg.oe_player_games GROUP BY 1, 2;

-- ------------------------------------------------------------------ fct_team_game
CREATE OR REPLACE TABLE core.fct_team_game AS
SELECT
    t.game_id, g.series_id, g.series_game_seq, t.game_time, t.league, t.season, t.split, t.playoffs,
    t.patch, t.patch_order, t.side, t.team_id, t.team_name,
    o.team_id AS opp_team_id, o.team_name AS opp_team_name,
    t.win, t.gamelength_s, t.completeness, t.first_pick,
    l.top_player_id, l.jng_player_id, l.mid_player_id, l.bot_player_id, l.sup_player_id, l.players,
    t.* EXCLUDE (game_id, game_time, league, season, split, playoffs, patch, patch_order, side,
                 team_id, team_name, win, gamelength_s, completeness, first_pick, url, game_number,
                 ban1, ban2, ban3, ban4, ban5, pick1, pick2, pick3, pick4, pick5)
FROM stg.oe_team_games t
JOIN stg.oe_team_games o ON o.game_id = t.game_id AND o.side <> t.side
JOIN core.fct_game g ON g.game_id = t.game_id
LEFT JOIN lineups l ON l.game_id = t.game_id AND l.team_id = t.team_id;

-- ------------------------------------------------------------------ fct_player_game
CREATE OR REPLACE TABLE core.fct_player_game AS
SELECT
    p.game_id, g.series_id, g.series_game_seq, p.game_time, p.league, p.season, p.split, p.playoffs,
    p.patch, p.patch_order, p.side, p.team_id, p.team_name, p.role, p.player_id, p.player_name, p.champion,
    o.team_id AS opp_team_id, o.player_id AS lane_opp_player_id, o.champion AS lane_opp_champion,
    p.win, p.gamelength_s, p.completeness,
    p.* EXCLUDE (game_id, game_time, league, season, split, playoffs, patch, patch_order, side, team_id,
                 team_name, role, player_id, player_name, champion, win, gamelength_s, completeness,
                 game_number, participant_id)
FROM stg.oe_player_games p
JOIN core.fct_game g ON g.game_id = p.game_id
LEFT JOIN stg.oe_player_games o ON o.game_id = p.game_id AND o.side <> p.side AND o.role = p.role;

-- ------------------------------------------------------------------ fct_draft (formato longo)
CREATE OR REPLACE TABLE core.fct_draft AS
WITH bans AS (
    UNPIVOT (SELECT game_id, team_id, side, ban1, ban2, ban3, ban4, ban5 FROM stg.oe_team_games)
    ON ban1, ban2, ban3, ban4, ban5 INTO NAME slot VALUE champion
), pick_order AS (
    UNPIVOT (SELECT game_id, team_id, pick1, pick2, pick3, pick4, pick5 FROM stg.oe_team_games)
    ON pick1, pick2, pick3, pick4, pick5 INTO NAME slot VALUE champion
), d AS (
    SELECT game_id, team_id, side, 'ban' AS action, replace(slot, 'ban', '')::INT AS action_number,
           champion, NULL::VARCHAR AS role, NULL::VARCHAR AS player_id
    FROM bans
    UNION ALL
    SELECT p.game_id, p.team_id, p.side, 'pick', replace(o.slot, 'pick', '')::INT, p.champion, p.role, p.player_id
    FROM stg.oe_player_games p
    LEFT JOIN pick_order o ON o.game_id = p.game_id AND o.team_id = p.team_id AND o.champion = p.champion
)
SELECT d.*, g.series_id, g.game_time, g.league, g.season, g.patch, g.patch_order,
       CASE WHEN d.action = 'pick' THEN
           CASE WHEN (d.side = 'blue') = (g.blue_win = 1) THEN 1 ELSE 0 END END AS win
FROM d JOIN core.fct_game g USING (game_id);

-- ------------------------------------------------------------------ dim_patch
CREATE OR REPLACE TABLE core.dim_patch AS
SELECT patch, patch_order, count(*) AS n_games, min(game_time) AS first_game, max(game_time) AS last_game,
       lag(patch) OVER (ORDER BY patch_order) AS prev_patch,
       lag(patch_order) OVER (ORDER BY patch_order) AS prev_patch_order
FROM core.fct_game WHERE patch IS NOT NULL GROUP BY 1, 2;

-- ------------------------------------------------------------------ dim_player
CREATE OR REPLACE TABLE core.dim_player AS
SELECT player_id,
       arg_max(player_name, game_time) AS player_name,
       mode(role) AS main_role,
       arg_max(team_id, game_time) AS last_team_id,
       arg_max(team_name, game_time) AS last_team_name,
       count(*) AS n_games, min(game_time) AS first_game, max(game_time) AS last_game,
       starts_with(player_id, 'name:') AS id_from_name
FROM core.fct_player_game GROUP BY player_id;
