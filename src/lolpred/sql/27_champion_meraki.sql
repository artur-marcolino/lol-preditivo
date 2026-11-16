-- =====================================================================================
-- Enriquece core.dim_champion com o Meraki (classes detalhadas, posições, notas).
-- O Meraki parou no patch 25.15: campeões lançados depois ficam com essas colunas vazias
-- (mk_available = false). Só roda se raw.mk_champions existir.
-- =====================================================================================
CREATE OR REPLACE MACRO core.norm_name(s) AS regexp_replace(lower(s), '[^a-z0-9]', '', 'g');

CREATE OR REPLACE TEMP TABLE _dim_champion_base AS SELECT * FROM core.dim_champion;

CREATE OR REPLACE TABLE core.dim_champion AS
SELECT
    d.*,
    m.name IS NOT NULL               AS mk_available,
    m.roles                          AS mk_roles,
    m.positions                      AS mk_positions,
    m.attackType                     AS mk_attack_type,
    m.adaptiveType                   AS mk_adaptive_type,
    m.releaseDate                    AS mk_release_date,
    m.rating_damage                  AS mk_rating_damage,
    m.rating_toughness               AS mk_rating_toughness,
    m.rating_control                 AS mk_rating_control,
    m.rating_mobility                AS mk_rating_mobility,
    m.rating_utility                 AS mk_rating_utility,
    m.rating_difficulty              AS mk_rating_difficulty
FROM _dim_champion_base d
LEFT JOIN raw.mk_champions m
  ON core.norm_name(m.name) = core.norm_name(d.champion)
  OR core.norm_name(m.champion_key) = core.norm_name(d.champion_id);
