-- =====================================================================================
-- FEATURES PRÉ-PARTIDA
--
-- Como evitar vazamento (a ideia central desta camada):
--   1. Tabelas *_state guardam o "estado" de cada time/jogador logo DEPOIS de cada jogo
--      (médias móveis, contagens acumuladas), calculado com janelas que só olham para trás.
--   2. Para uma série que começa no instante T, pegamos o último estado com horário < T
--      via ASOF JOIN. Assim nenhum jogo da própria série (nem posterior) entra nas variáveis.
--
-- Quando dois jogos têm exatamente o mesmo horário (acontece em séries com horário
-- arredondado), o ASOF ficaria ambíguo; por isso os estados guardam só a última linha
-- de cada instante (QUALIFY ... = 1), que já inclui os dois jogos nas contagens.
--
-- O histórico de time segue a franquia (franchise_id), para não zerar quando o time
-- muda de nome. O histórico de jogador segue o player_id, que é estável.
-- =====================================================================================
CREATE SCHEMA IF NOT EXISTS feat;

-- ------------------------------------------------------------------ 1. estado do time após cada jogo
CREATE OR REPLACE TABLE feat.team_game_state AS
SELECT
    franchise_id, game_id, game_time, league, season, split, players,
    count(*)                        OVER w_all AS games_total,
    avg(win)                        OVER w_all AS winrate_total,
    avg(win)                        OVER w10   AS winrate_last10,
    avg(win)                        OVER w20   AS winrate_last20,
    avg(gold_diff_10)               OVER w10   AS gold_diff_10_last10,
    avg(gold_diff_15)               OVER w10   AS gold_diff_15_last10,
    count(gold_diff_15)             OVER w10   AS gold_diff_15_obs_last10,
    avg(xp_diff_15)                 OVER w10   AS xp_diff_15_last10,
    avg(cs_diff_15)                 OVER w10   AS cs_diff_15_last10,
    avg(first_blood)                OVER w20   AS first_blood_rate_last20,
    avg(first_dragon)               OVER w20   AS first_dragon_rate_last20,
    avg(first_herald)               OVER w20   AS first_herald_rate_last20,
    avg(first_tower)                OVER w20   AS first_tower_rate_last20,
    avg(first_baron)                OVER w20   AS first_baron_rate_last20,
    avg(dragons - opp_dragons)      OVER w20   AS dragon_diff_last20,
    avg(barons - opp_barons)        OVER w20   AS baron_diff_last20,
    avg(towers - opp_towers)        OVER w20   AS tower_diff_last20,
    avg(kills - deaths)             OVER w20   AS kill_diff_last20,
    avg(ckpm)                       OVER w20   AS combined_kpm_last20,
    avg(gamelength_s / 60)          OVER w20   AS game_minutes_last20,
    avg(earned_gpm)                 OVER w20   AS earned_gpm_last20,
    avg(dpm)                        OVER w20   AS dpm_last20,
    avg(vspm)                       OVER w20   AS vspm_last20,
    avg(wcpm)                       OVER w20   AS wcpm_last20,
    count(*)                        OVER w_split AS split_games,
    avg(win)                        OVER w_split AS split_winrate
FROM core.fct_team_game
WINDOW
    w_all   AS (PARTITION BY franchise_id ORDER BY game_time, game_id ROWS UNBOUNDED PRECEDING),
    w10     AS (PARTITION BY franchise_id ORDER BY game_time, game_id ROWS 9 PRECEDING),
    w20     AS (PARTITION BY franchise_id ORDER BY game_time, game_id ROWS 19 PRECEDING),
    w_split AS (PARTITION BY franchise_id, league, season, split ORDER BY game_time, game_id ROWS UNBOUNDED PRECEDING)
QUALIFY row_number() OVER (PARTITION BY franchise_id, game_time ORDER BY game_id DESC) = 1;

-- ------------------------------------------------------------------ 2. estado do time após cada série
CREATE OR REPLACE TABLE feat.team_series_state AS
SELECT
    franchise_id, series_id, start_time,
    count(*)                                   OVER w_all AS series_total,
    avg(win)                                   OVER w_all AS series_winrate_total,
    avg(win)                                   OVER w10   AS series_winrate_last10,
    sum(map_wins) OVER w10 / nullif(sum(map_wins + map_losses) OVER w10, 0) AS map_winrate_in_series_last10,
    avg(win) FILTER (WHERE best_of >= 3)       OVER w10   AS bo3plus_winrate_last10,
    avg(playoffs::DOUBLE)                      OVER w_all AS playoff_share_total,
    sum(playoffs)                              OVER w_all AS playoff_series_total
FROM core.fct_team_series
WHERE win IS NOT NULL
WINDOW
    w_all AS (PARTITION BY franchise_id ORDER BY start_time, series_id ROWS UNBOUNDED PRECEDING),
    w10   AS (PARTITION BY franchise_id ORDER BY start_time, series_id ROWS 9 PRECEDING)
QUALIFY row_number() OVER (PARTITION BY franchise_id, start_time ORDER BY series_id DESC) = 1;

-- ------------------------------------------------------------------ 3. confronto direto após cada série
CREATE OR REPLACE TABLE feat.h2h_state AS
SELECT
    franchise_id, opp_franchise_id, series_id, start_time,
    count(*)        OVER w AS h2h_series,
    avg(win)        OVER w AS h2h_series_winrate,
    sum(map_wins)   OVER w / nullif(sum(map_wins + map_losses) OVER w, 0) AS h2h_map_winrate
FROM core.fct_team_series
WHERE win IS NOT NULL
WINDOW w AS (PARTITION BY franchise_id, opp_franchise_id ORDER BY start_time, series_id ROWS UNBOUNDED PRECEDING)
QUALIFY row_number() OVER (PARTITION BY franchise_id, opp_franchise_id, start_time ORDER BY series_id DESC) = 1;

-- ------------------------------------------------------------------ 4. estado do jogador após cada jogo
CREATE OR REPLACE TABLE feat.player_game_state AS
SELECT
    player_id, franchise_id, game_id, game_time, role, champion,
    row_number()                    OVER w_all AS player_seq,
    count(*)                        OVER w_all AS games_total,
    count(*)                        OVER w_team AS games_with_franchise,
    avg(win)                        OVER w20 AS winrate_last20,
    sum(kills + assists) OVER w20 / greatest(sum(deaths) OVER w20, 1) AS kda_last20,
    avg(kills)                      OVER w20 AS kills_last20,
    avg(deaths)                     OVER w20 AS deaths_last20,
    avg(gold_diff_15)               OVER w20 AS gold_diff_15_last20,
    avg(xp_diff_15)                 OVER w20 AS xp_diff_15_last20,
    avg(cs_diff_15)                 OVER w20 AS cs_diff_15_last20,
    avg(damage_share)               OVER w20 AS damage_share_last20,
    avg(gold_share)                 OVER w20 AS gold_share_last20,
    avg(dpm)                        OVER w20 AS dpm_last20,
    avg(cspm)                       OVER w20 AS cspm_last20,
    avg(vspm)                       OVER w20 AS vspm_last20,
    count(DISTINCT champion)        OVER w30 AS champion_pool_last30
FROM core.fct_player_game
WINDOW
    w_all  AS (PARTITION BY player_id ORDER BY game_time, game_id ROWS UNBOUNDED PRECEDING),
    w_team AS (PARTITION BY player_id, franchise_id ORDER BY game_time, game_id ROWS UNBOUNDED PRECEDING),
    w20    AS (PARTITION BY player_id ORDER BY game_time, game_id ROWS 19 PRECEDING),
    w30    AS (PARTITION BY player_id ORDER BY game_time, game_id ROWS 29 PRECEDING);

-- versão para o ASOF: uma linha por jogador e instante (a tabela completa serve ao histórico de campeões)
CREATE OR REPLACE VIEW feat.player_game_state_asof AS
SELECT * FROM feat.player_game_state
QUALIFY row_number() OVER (PARTITION BY player_id, game_time ORDER BY player_seq DESC) = 1;

-- ------------------------------------------------------------------ 5. base: uma linha por série x time
-- A escalação é a do jogo 1 da série (anunciada antes da partida e do draft).
CREATE OR REPLACE TABLE feat.series_team_base AS
SELECT ts.series_id, ts.start_time, ts.league, ts.season, ts.split, ts.playoffs, ts.patch, ts.patch_order,
       ts.best_of, ts.is_clean, ts.team_id, ts.franchise_id, ts.opp_team_id, ts.opp_franchise_id,
       ts.is_team1, ts.win, g1.game_id AS first_game_id, g1.players AS lineup_players
FROM core.fct_team_series ts
JOIN core.fct_team_game g1 ON g1.series_id = ts.series_id AND g1.series_game_seq = 1 AND g1.team_id = ts.team_id;

-- ------------------------------------------------------------------ 6. forma do time (ASOF: último jogo antes da série)
CREATE OR REPLACE TABLE feat.team_form AS
SELECT
    b.series_id, b.team_id,
    coalesce(st.games_total, 0) AS games_total,
    st.* EXCLUDE (franchise_id, game_id, game_time, league, season, split, players,
                  games_total, split_games, split_winrate),
    CASE WHEN st.league = b.league AND st.season = b.season AND st.split IS NOT DISTINCT FROM b.split
         THEN st.split_games ELSE 0 END AS split_games,
    CASE WHEN st.league = b.league AND st.season = b.season AND st.split IS NOT DISTINCT FROM b.split
         THEN st.split_winrate END AS split_winrate,
    date_diff('day', st.game_time, b.start_time) AS days_since_last_game,
    len(list_intersect(st.players, b.lineup_players)) AS lineup_continuity,
    (st.league IS DISTINCT FROM b.league) AS league_changed
FROM feat.series_team_base b
ASOF LEFT JOIN feat.team_game_state st
    ON st.franchise_id = b.franchise_id AND b.start_time > st.game_time;

-- ------------------------------------------------------------------ 7. forma em séries e confronto direto
CREATE OR REPLACE TABLE feat.team_series_form AS
SELECT b.series_id, b.team_id,
       coalesce(ss.series_total, 0) AS series_total,
       ss.* EXCLUDE (franchise_id, series_id, start_time, series_total),
       coalesce(h.h2h_series, 0) AS h2h_series,
       h.h2h_series_winrate, h.h2h_map_winrate
FROM feat.series_team_base b
ASOF LEFT JOIN feat.team_series_state ss
    ON ss.franchise_id = b.franchise_id AND b.start_time > ss.start_time
ASOF LEFT JOIN feat.h2h_state h
    ON h.franchise_id = b.franchise_id AND h.opp_franchise_id = b.opp_franchise_id AND b.start_time > h.start_time;

-- ------------------------------------------------------------------ 8. escalação: forma de cada jogador
CREATE OR REPLACE TABLE feat.lineup_player_form AS
WITH lp AS (
    SELECT b.series_id, b.team_id, b.franchise_id, b.start_time, b.patch_order, p.role, p.player_id
    FROM feat.series_team_base b
    JOIN core.fct_player_game p
      ON p.series_id = b.series_id AND p.series_game_seq = 1 AND p.team_id = b.team_id
)
SELECT
    lp.series_id, lp.team_id, lp.franchise_id, lp.start_time, lp.patch_order, lp.role, lp.player_id,
    ps.player_seq,
    coalesce(ps.games_total, 0) AS games_total,
    CASE WHEN ps.franchise_id = lp.franchise_id THEN ps.games_with_franchise ELSE 0 END AS games_with_franchise,
    ps.* EXCLUDE (player_id, franchise_id, game_id, game_time, role, champion, player_seq,
                  games_total, games_with_franchise)
FROM lp
ASOF LEFT JOIN feat.player_game_state_asof ps
    ON ps.player_id = lp.player_id AND lp.start_time > ps.game_time;

-- ------------------------------------------------------------------ 9. meta de campeões por patch e posição
-- pick_rate = em quantos % dos jogos do patch o campeão foi escolhido naquela posição.
CREATE OR REPLACE TABLE feat.champion_patch_role AS
WITH games AS (SELECT patch_order, count(*) AS n_games FROM core.fct_game GROUP BY 1)
SELECT d.patch_order, d.role, d.champion, count(*) AS picks, avg(d.win) AS winrate,
       count(*) / any_value(g.n_games) AS pick_rate,
       row_number() OVER (PARTITION BY d.patch_order, d.role ORDER BY count(*) DESC, d.champion) AS rank_in_role
FROM core.fct_draft d JOIN games g USING (patch_order)
WHERE d.action = 'pick' AND d.patch_order IS NOT NULL
GROUP BY d.patch_order, d.role, d.champion;

-- Para cada patch, o patch "de referência" anterior com volume suficiente (>= 100 jogos).
-- O meta usado numa série é o desse patch anterior: ele já estava fechado quando a série começou.
CREATE OR REPLACE TABLE feat.patch_reference AS
WITH significant AS (SELECT patch_order FROM core.dim_patch WHERE n_games >= 100)
SELECT p.patch_order,
       (SELECT max(s.patch_order) FROM significant s WHERE s.patch_order < p.patch_order) AS ref_patch_order
FROM core.dim_patch p;

-- Aderência ao meta: dos campeões que o jogador jogou nos últimos 30 jogos (antes da série),
-- qual a popularidade média deles na posição, no patch de referência?
CREATE OR REPLACE TABLE feat.lineup_meta_fit AS
WITH lp AS (
    SELECT l.series_id, l.team_id, l.player_id, l.role, l.player_seq, r.ref_patch_order
    FROM feat.lineup_player_form l
    LEFT JOIN feat.patch_reference r USING (patch_order)
    WHERE l.player_seq IS NOT NULL
), hist AS (
    SELECT lp.series_id, lp.team_id, lp.player_id, lp.ref_patch_order, ps.role, ps.champion
    FROM lp
    JOIN feat.player_game_state ps
      ON ps.player_id = lp.player_id AND ps.player_seq BETWEEN lp.player_seq - 29 AND lp.player_seq
)
SELECT h.series_id, h.team_id, h.player_id,
       avg(coalesce(m.pick_rate, 0)) AS meta_fit,
       count(DISTINCT CASE WHEN m.rank_in_role <= 10 THEN h.champion END) AS meta_top10_in_pool
FROM hist h
LEFT JOIN feat.champion_patch_role m
  ON m.patch_order = h.ref_patch_order AND m.role = h.role AND m.champion = h.champion
GROUP BY 1, 2, 3;

-- ------------------------------------------------------------------ 10. agregados da escalação por time
CREATE OR REPLACE TABLE feat.lineup_form AS
SELECT
    l.series_id, l.team_id,
    count(*)                                   AS lineup_size,
    avg(l.games_total)                         AS lineup_games_mean,
    min(l.games_total)                         AS lineup_games_min,
    count(*) FILTER (WHERE l.games_total < 20) AS lineup_rookies,
    avg(l.games_with_franchise)                AS lineup_games_with_team_mean,
    count(*) FILTER (WHERE l.games_with_franchise = 0) AS lineup_new_to_team,
    avg(l.winrate_last20)                      AS lineup_winrate_last20_mean,
    avg(l.kda_last20)                          AS lineup_kda_last20_mean,
    avg(l.gold_diff_15_last20)                 AS lineup_gold_diff_15_last20_mean,
    avg(l.champion_pool_last30)                AS lineup_champion_pool_mean,
    avg(m.meta_fit)                            AS lineup_meta_fit_mean,
    avg(m.meta_top10_in_pool)                  AS lineup_meta_top10_mean,
    max(l.gold_diff_15_last20) FILTER (WHERE l.role = 'top') AS top_gold_diff_15_last20,
    max(l.gold_diff_15_last20) FILTER (WHERE l.role = 'jng') AS jng_gold_diff_15_last20,
    max(l.gold_diff_15_last20) FILTER (WHERE l.role = 'mid') AS mid_gold_diff_15_last20,
    max(l.gold_diff_15_last20) FILTER (WHERE l.role = 'bot') AS bot_gold_diff_15_last20,
    max(l.gold_diff_15_last20) FILTER (WHERE l.role = 'sup') AS sup_gold_diff_15_last20,
    max(l.kda_last20) FILTER (WHERE l.role = 'top') AS top_kda_last20,
    max(l.kda_last20) FILTER (WHERE l.role = 'jng') AS jng_kda_last20,
    max(l.kda_last20) FILTER (WHERE l.role = 'mid') AS mid_kda_last20,
    max(l.kda_last20) FILTER (WHERE l.role = 'bot') AS bot_kda_last20,
    max(l.kda_last20) FILTER (WHERE l.role = 'sup') AS sup_kda_last20
FROM feat.lineup_player_form l
LEFT JOIN feat.lineup_meta_fit m USING (series_id, team_id, player_id)
GROUP BY 1, 2;

-- ------------------------------------------------------------------ 11. tudo junto: uma linha por série x time
CREATE OR REPLACE TABLE feat.series_team AS
SELECT
    b.series_id, b.team_id, b.franchise_id, b.opp_team_id, b.opp_franchise_id, b.is_team1, b.win,
    e.elo_pre,
    f.* EXCLUDE (series_id, team_id),
    s.* EXCLUDE (series_id, team_id),
    l.* EXCLUDE (series_id, team_id)
FROM feat.series_team_base b
LEFT JOIN feat.team_elo e ON e.game_id = b.first_game_id AND e.franchise_id = b.franchise_id
LEFT JOIN feat.team_form f USING (series_id, team_id)
LEFT JOIN feat.team_series_form s USING (series_id, team_id)
LEFT JOIN feat.lineup_form l USING (series_id, team_id);
