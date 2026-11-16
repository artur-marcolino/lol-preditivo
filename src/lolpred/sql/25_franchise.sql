-- =====================================================================================
-- Liga cada time à sua franquia (linhagem calculada em warehouse/lineage.py)
-- =====================================================================================
ALTER TABLE core.fct_team_game ADD COLUMN IF NOT EXISTS franchise_id VARCHAR;
ALTER TABLE core.fct_team_game ADD COLUMN IF NOT EXISTS opp_franchise_id VARCHAR;
UPDATE core.fct_team_game t SET franchise_id = l.franchise_id FROM core.team_lineage l WHERE l.team_id = t.team_id;
UPDATE core.fct_team_game t SET opp_franchise_id = l.franchise_id FROM core.team_lineage l WHERE l.team_id = t.opp_team_id;

ALTER TABLE core.fct_player_game ADD COLUMN IF NOT EXISTS franchise_id VARCHAR;
UPDATE core.fct_player_game p SET franchise_id = l.franchise_id FROM core.team_lineage l WHERE l.team_id = p.team_id;

-- ------------------------------------------------------------------ dim_team
CREATE OR REPLACE TABLE core.dim_team AS
SELECT t.team_id,
       arg_max(t.team_name, t.game_time) AS team_name,
       l.franchise_id, l.franchise_name, l.predecessor_team_id, l.link_reason,
       mode(t.league) AS main_league,
       arg_max(t.league, t.game_time) AS last_league,
       count(*) AS n_games, min(t.game_time) AS first_game, max(t.game_time) AS last_game,
       starts_with(t.team_id, 'name:') AS id_from_name
FROM core.fct_team_game t
LEFT JOIN core.team_lineage l USING (team_id)
GROUP BY t.team_id, l.franchise_id, l.franchise_name, l.predecessor_team_id, l.link_reason;

-- ------------------------------------------------------------------ séries em formato "uma linha por time"
-- Facilita tudo que é por time: forma em séries, confronto direto, etc.
CREATE OR REPLACE TABLE core.fct_team_series AS
WITH s AS (
    SELECT s.*, l1.franchise_id AS team1_franchise_id, l2.franchise_id AS team2_franchise_id
    FROM core.fct_series s
    LEFT JOIN core.team_lineage l1 ON l1.team_id = s.team1_id
    LEFT JOIN core.team_lineage l2 ON l2.team_id = s.team2_id
)
SELECT series_id, league, season, split, playoffs, patch, patch_order, start_time, last_game_time,
       best_of, games_played, is_clean,
       team1_id AS team_id, team1_franchise_id AS franchise_id, team1_name AS team_name,
       team2_id AS opp_team_id, team2_franchise_id AS opp_franchise_id,
       1 AS is_team1, team1_wins AS map_wins, team2_wins AS map_losses, team1_win AS win
FROM s
UNION ALL
SELECT series_id, league, season, split, playoffs, patch, patch_order, start_time, last_game_time,
       best_of, games_played, is_clean,
       team2_id, team2_franchise_id, team2_name,
       team1_id, team1_franchise_id,
       0, team2_wins, team1_wins, 1 - team1_win
FROM s;
