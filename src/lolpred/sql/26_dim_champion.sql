-- =====================================================================================
-- dim_champion: atributos do Data Dragon na versão mais recente de cada campeão.
-- A tabela completa por patch continua em raw.dd_champions.
-- (Só roda se raw.dd_champions existir.)
-- =====================================================================================
CREATE OR REPLACE TABLE core.dim_champion AS
WITH v AS (
    SELECT *, string_split(version, '.') AS parts FROM raw.dd_champions
), ranked AS (
    SELECT *, row_number() OVER (PARTITION BY name
        ORDER BY parts[1]::INT DESC, parts[2]::INT DESC, parts[3]::INT DESC) AS rn
    FROM v
)
SELECT name AS champion, champion_id, key AS champion_key, title, partype, tag_primary, tag_secondary, tags,
       COLUMNS('^info_'), version AS dd_version
FROM ranked WHERE rn = 1;
