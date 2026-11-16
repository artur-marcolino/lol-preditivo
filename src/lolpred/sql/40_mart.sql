-- =====================================================================================
-- MART: tabelas prontas para análise (as de modelagem pré-partida são montadas em
-- warehouse/marts.py, porque as colunas t1_/t2_/diff_ são geradas automaticamente).
-- =====================================================================================
CREATE SCHEMA IF NOT EXISTS mart;

-- Meta por patch: quanto cada campeão foi escolhido/banido e quanto venceu.
-- scope = 'todas' (todas as ligas) ou 'tier1-2' (internacionais + ligas principais).
CREATE OR REPLACE TABLE mart.champion_patch_meta AS
WITH d AS (
    SELECT d.*, coalesce(l.tier, 4) AS tier
    FROM core.fct_draft d LEFT JOIN core.dim_league l USING (league)
    WHERE d.patch_order IS NOT NULL
), scoped AS (
    SELECT *, 'todas' AS scope FROM d
    UNION ALL
    SELECT *, 'tier1-2' AS scope FROM d WHERE tier <= 2
), games AS (
    SELECT scope, patch_order, count(DISTINCT game_id) AS n_games FROM scoped GROUP BY 1, 2
)
SELECT s.scope, s.patch_order, any_value(s.patch) AS patch, s.champion,
       any_value(g.n_games) AS n_games,
       count(*) FILTER (WHERE s.action = 'pick') AS picks,
       count(*) FILTER (WHERE s.action = 'ban')  AS bans,
       count(*) FILTER (WHERE s.action = 'pick') / any_value(g.n_games) AS pick_rate,
       count(*) FILTER (WHERE s.action = 'ban')  / any_value(g.n_games) AS ban_rate,
       count(*) / any_value(g.n_games) AS presence,
       avg(s.win) FILTER (WHERE s.action = 'pick') AS winrate,
       mode(s.role) FILTER (WHERE s.action = 'pick') AS main_role
FROM scoped s JOIN games g USING (scope, patch_order)
GROUP BY s.scope, s.patch_order, s.champion;

-- Campeões de cada time em cada patch (base para "o time X rende mais quando Y está forte").
CREATE OR REPLACE TABLE mart.team_champion_patch AS
SELECT t.franchise_id, any_value(l.franchise_name) AS franchise_name, d.patch_order, any_value(d.patch) AS patch,
       d.champion, d.role,
       count(*) AS picks, sum(d.win) AS wins, avg(d.win) AS winrate
FROM core.fct_draft d
JOIN core.fct_team_game t ON t.game_id = d.game_id AND t.team_id = d.team_id
LEFT JOIN core.team_lineage l ON l.team_id = t.team_id
WHERE d.action = 'pick'
GROUP BY t.franchise_id, d.patch_order, d.champion, d.role;

-- Visão rápida do desempenho de cada franquia por split.
CREATE OR REPLACE TABLE mart.team_split_summary AS
SELECT t.franchise_id, any_value(l.franchise_name) AS franchise_name, t.league, t.season, t.split,
       count(*) AS games, avg(t.win) AS winrate,
       avg(t.gold_diff_15) AS gold_diff_15, avg(t.first_dragon) AS first_dragon_rate,
       avg(t.first_tower) AS first_tower_rate, avg(t.gamelength_s) / 60 AS game_minutes,
       min(t.game_time) AS first_game, max(t.game_time) AS last_game
FROM core.fct_team_game t LEFT JOIN core.team_lineage l USING (team_id)
GROUP BY t.franchise_id, t.league, t.season, t.split;
