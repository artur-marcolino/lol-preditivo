-- =====================================================================================
-- STAGING: limpeza e padronização do Oracle's Elixir (sem regras de negócio ainda)
--   * tipos corretos (datas como TIMESTAMP, flags como inteiros 0/1)
--   * patch como texto 'MM.mm' + patch_order numérico para ordenar
--   * chaves sempre preenchidas: se faltar o id da fonte, usa 'name:<nome>'
-- =====================================================================================
CREATE SCHEMA IF NOT EXISTS stg;

CREATE OR REPLACE MACRO stg.patch_text(p) AS
    CASE WHEN p IS NULL THEN NULL
         ELSE printf('%d.%02d', floor(p)::INT, round((p - floor(p)) * 100)::INT) END;

CREATE OR REPLACE MACRO stg.patch_num(p) AS
    CASE WHEN p IS NULL THEN NULL
         ELSE floor(p)::INT * 100 + round((p - floor(p)) * 100)::INT END;

-- Uma linha por jogo x linha original (12 por jogo), já tipada
CREATE OR REPLACE VIEW stg.oe_rows AS
SELECT
    gameid                                   AS game_id,
    league,
    year::INT                                AS season,
    nullif(trim(split), '')                  AS split,
    coalesce(playoffs, 0)::INT               AS playoffs,
    try_cast(date AS TIMESTAMP)              AS game_time,
    game::INT                                AS game_number,
    stg.patch_text(patch)                    AS patch,
    stg.patch_num(patch)                     AS patch_order,
    lower(side)                              AS side,
    position,
    coalesce(teamid, 'name:' || teamname)    AS team_id,
    teamname                                 AS team_name,
    CASE WHEN position <> 'team' THEN coalesce(playerid, 'name:' || playername) END AS player_id,
    CASE WHEN position <> 'team' THEN playername END AS player_name,
    champion,
    result::INT                              AS win,
    gamelength::DOUBLE                       AS gamelength_s,
    datacompleteness                         AS completeness,
    url,
    * EXCLUDE (gameid, league, year, split, playoffs, date, game, patch, side, position,
               teamid, teamname, playerid, playername, champion, result, gamelength,
               datacompleteness, url)
FROM raw.oe_matches;

-- Uma linha por jogo x time
CREATE OR REPLACE TABLE stg.oe_team_games AS
SELECT
    game_id, league, season, split, playoffs, game_time, game_number, patch, patch_order,
    side, team_id, team_name, win, gamelength_s, completeness, url,
    firstPick::INT AS first_pick,
    ban1, ban2, ban3, ban4, ban5, pick1, pick2, pick3, pick4, pick5,
    kills::DOUBLE AS kills, deaths::DOUBLE AS deaths, assists::DOUBLE AS assists,
    team_kpm::DOUBLE AS team_kpm, ckpm::DOUBLE AS ckpm,
    firstblood::DOUBLE AS first_blood,
    firstdragon::DOUBLE AS first_dragon, dragons::DOUBLE AS dragons, opp_dragons::DOUBLE AS opp_dragons,
    elders::DOUBLE AS elders,
    firstherald::DOUBLE AS first_herald, heralds::DOUBLE AS heralds,
    void_grubs::DOUBLE AS void_grubs, opp_void_grubs::DOUBLE AS opp_void_grubs,
    firstbaron::DOUBLE AS first_baron, barons::DOUBLE AS barons, opp_barons::DOUBLE AS opp_barons,
    atakhans::DOUBLE AS atakhans,
    firsttower::DOUBLE AS first_tower, towers::DOUBLE AS towers, opp_towers::DOUBLE AS opp_towers,
    firstmidtower::DOUBLE AS first_mid_tower, firsttothreetowers::DOUBLE AS first_three_towers,
    turretplates::DOUBLE AS turret_plates, opp_turretplates::DOUBLE AS opp_turret_plates,
    inhibitors::DOUBLE AS inhibitors,
    dpm::DOUBLE AS dpm, damagetakenperminute::DOUBLE AS dtpm,
    wpm::DOUBLE AS wpm, wcpm::DOUBLE AS wcpm, vspm::DOUBLE AS vspm,
    controlwardsbought::DOUBLE AS control_wards,
    earned_gpm::DOUBLE AS earned_gpm, gspd::DOUBLE AS gspd, cspm::DOUBLE AS cspm,
    golddiffat10::DOUBLE AS gold_diff_10, xpdiffat10::DOUBLE AS xp_diff_10, csdiffat10::DOUBLE AS cs_diff_10,
    golddiffat15::DOUBLE AS gold_diff_15, xpdiffat15::DOUBLE AS xp_diff_15, csdiffat15::DOUBLE AS cs_diff_15,
    golddiffat20::DOUBLE AS gold_diff_20, golddiffat25::DOUBLE AS gold_diff_25,
    killsat15::DOUBLE AS kills_15, deathsat15::DOUBLE AS deaths_15
FROM stg.oe_rows
WHERE position = 'team';

-- Uma linha por jogo x jogador
CREATE OR REPLACE TABLE stg.oe_player_games AS
SELECT
    game_id, league, season, split, playoffs, game_time, game_number, patch, patch_order,
    side, team_id, team_name, position AS role, participantid::INT AS participant_id,
    player_id, player_name, champion, win, gamelength_s, completeness,
    kills::DOUBLE AS kills, deaths::DOUBLE AS deaths, assists::DOUBLE AS assists,
    firstbloodkill::DOUBLE AS first_blood_kill, firstbloodvictim::DOUBLE AS first_blood_victim,
    dpm::DOUBLE AS dpm, damageshare::DOUBLE AS damage_share,
    damagetakenperminute::DOUBLE AS dtpm,
    earned_gpm::DOUBLE AS earned_gpm, earnedgoldshare::DOUBLE AS gold_share,
    cspm::DOUBLE AS cspm, vspm::DOUBLE AS vspm, wpm::DOUBLE AS wpm, wcpm::DOUBLE AS wcpm,
    golddiffat10::DOUBLE AS gold_diff_10, xpdiffat10::DOUBLE AS xp_diff_10, csdiffat10::DOUBLE AS cs_diff_10,
    golddiffat15::DOUBLE AS gold_diff_15, xpdiffat15::DOUBLE AS xp_diff_15, csdiffat15::DOUBLE AS cs_diff_15,
    killsat15::DOUBLE AS kills_15, assistsat15::DOUBLE AS assists_15, deathsat15::DOUBLE AS deaths_15
FROM stg.oe_rows
WHERE position <> 'team';
