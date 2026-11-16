"""Verificações de integridade e de vazamento no banco construído (`lolpred check-db`).

As verificações de vazamento recalculam algumas variáveis "na mão", com uma consulta
direta (contar os jogos anteriores ao início da série), e comparam com o que a camada
feat produziu via janelas + ASOF. Se a lógica temporal estiver certa, não há divergência.
"""

from __future__ import annotations

import duckdb

CHECKS: list[tuple[str, str]] = [
    ("jogos com exatamente 2 linhas de time",
     "SELECT count(*) FROM (SELECT game_id FROM core.fct_team_game GROUP BY 1 HAVING count(*) <> 2)"),
    ("jogos com exatamente 10 linhas de jogador",
     "SELECT count(*) FROM (SELECT game_id FROM core.fct_player_game GROUP BY 1 HAVING count(*) <> 10)"),
    ("todo jogo pertence a uma série",
     "SELECT count(*) FROM core.fct_game WHERE series_id IS NULL"),
    ("série com no máximo 5 jogos",
     "SELECT count(*) FROM core.fct_series WHERE games_played > 5"),
    ("time nunca enfrenta a própria franquia",
     "SELECT count(*) FROM core.fct_team_game WHERE franchise_id = opp_franchise_id"),
    ("todo time tem franquia",
     "SELECT count(*) FROM core.fct_team_game WHERE franchise_id IS NULL"),
    ("uma linha por série x time em feat.series_team",
     "SELECT count(*) FROM (SELECT series_id, team_id FROM feat.series_team GROUP BY ALL HAVING count(*) > 1)"),
    ("estado usado nunca é de um jogo da própria série",
     """SELECT count(*) FROM feat.series_team_base b
        ASOF JOIN feat.team_game_state st ON st.franchise_id = b.franchise_id AND b.start_time > st.game_time
        JOIN core.fct_game g ON g.game_id = st.game_id WHERE g.series_id = b.series_id"""),
    ("games_total = jogos da franquia antes da série (amostra)",
     """WITH st AS (SELECT * FROM feat.series_team USING SAMPLE 3000 ROWS (reservoir, 42))
        SELECT count(*) FROM st JOIN feat.series_team_base b USING (series_id, team_id)
        WHERE st.games_total <> (SELECT count(*) FROM core.fct_team_game t
                                 WHERE t.franchise_id = st.franchise_id AND t.game_time < b.start_time)"""),
    ("winrate_last10 = média dos 10 jogos anteriores (amostra)",
     """WITH st AS (SELECT * FROM feat.series_team USING SAMPLE 3000 ROWS (reservoir, 42))
        SELECT count(*) FROM st JOIN feat.series_team_base b USING (series_id, team_id)
        WHERE abs(coalesce(st.winrate_last10, -1) - coalesce((
            SELECT avg(win) FROM (SELECT t.win FROM core.fct_team_game t
                                  WHERE t.franchise_id = st.franchise_id AND t.game_time < b.start_time
                                  ORDER BY t.game_time DESC, t.game_id DESC LIMIT 10)), -1)) > 1e-9"""),
    ("experiência da escalação = jogos anteriores de cada jogador (amostra)",
     """WITH st AS (SELECT * FROM feat.series_team USING SAMPLE 3000 ROWS (reservoir, 42))
        SELECT count(*) FROM st
        WHERE abs(coalesce(st.lineup_games_mean, -1) - coalesce((
            SELECT avg((SELECT count(*) FROM core.fct_player_game x
                        WHERE x.player_id = p.player_id AND x.game_time < b.start_time))
            FROM feat.series_team_base b
            JOIN core.fct_player_game p ON p.series_id = b.series_id AND p.series_game_seq = 1 AND p.team_id = b.team_id
            WHERE b.series_id = st.series_id AND b.team_id = st.team_id), -1)) > 1e-9"""),
    ("mart.pregame_series tem uma linha por série",
     "SELECT (SELECT count(*) FROM core.fct_series) - (SELECT count(DISTINCT series_id) FROM mart.pregame_series)"),
    ("mart.pregame_games tem uma linha por jogo",
     "SELECT (SELECT count(*) FROM core.fct_game) - (SELECT count(DISTINCT game_id) FROM mart.pregame_games)"),
]


def run(con: duckdb.DuckDBPyConnection) -> list[tuple[str, int]]:
    """Devolve [(descrição, nº de violações)]. Zero violações = ok."""
    return [(name, int(con.execute(sql).fetchone()[0])) for name, sql in CHECKS]
