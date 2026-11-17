# Catálogo de dados

Gerado automaticamente em 2026-11-17 00:36 UTC a partir de `data/manifest/`. Não edite à mão.

## Fontes

| Fonte | Status | Última tentativa | Último sucesso | Observação |
|---|---|---|---|---|
| ddragon | ok | 2026-11-16T19:52:21+00:00 | 2026-11-16T19:52:21+00:00 |  |
| esportsbench | ok | 2026-11-17T00:18:24+00:00 | 2026-11-17T00:18:24+00:00 |  |
| leaguepedia | em andamento (carga histórica) | 2026-11-17T00:27:53+00:00 | 2026-11-17T00:27:53+00:00 |  |
| leaguepedia_gcd | ok | 2026-11-17T00:35:36+00:00 | 2026-11-17T00:35:36+00:00 |  |
| lolesports | ok | 2026-11-16T19:53:40+00:00 | 2026-11-16T19:53:40+00:00 |  |
| lolwiki | falhou | 2026-11-16T04:02:31+00:00 |  | Module:ChampionData/data: api.php: HTTP 403 (<html lang="en"> <head> <title>Request blocked</title> <meta name="viewport" content="width=dev… |
| meraki | ok | 2026-11-16T19:52:43+00:00 | 2026-11-16T19:52:43+00:00 |  |
| oddspapi | ok | 2026-11-16T22:25:19+00:00 | 2026-11-16T22:25:19+00:00 |  |
| oracles_elixir | adiado (limite temporário da fonte) | 2026-11-16T19:52:42+00:00 | 2026-11-16T03:26:29+00:00 | 2026: cota de download do Google Drive excedida; mantida a cópia baixada em 2026-11-16 02:15 UTC, nova tentativa na próxima coleta |
| pandascore | ok | 2026-11-16T23:01:25+00:00 | 2026-11-16T23:01:25+00:00 |  |
| pandaskill | ok | 2026-11-17T00:18:07+00:00 | 2026-11-17T00:18:07+00:00 |  |
| riot_gcd | ok | 2026-11-17T00:22:26+00:00 | 2026-11-17T00:22:26+00:00 |  |

## Tabelas

| View no DuckDB | Fonte/tabela | Linhas | Colunas | Partições | Período | Tamanho | Atualizada em |
|---|---|---:|---:|---|---|---:|---|
| `raw.dd_champions` | ddragon/champions | 47,070 | 35 | única | — | 0.2 MB | 2026-11-16T02:15 |
| `raw.dd_items` | ddragon/items | 106,889 | 26 | única | — | 0.3 MB | 2026-11-16T02:16 |
| `raw.ebench_matches` | esportsbench/matches | 163,653 | 6 | única | 2011-03-14 a 2026-09-30 | 1.9 MB | 2026-11-17T00:18 |
| `raw.lp_leagues` | leaguepedia/Leagues | 194 | 6 | única | — | 0.0 MB | 2026-11-16T04:18 |
| `raw.lp_matchschedule` | leaguepedia/MatchSchedule | 174,218 | 74 | 2011–2026 (16) | 2011-03-14 a 2026-12-13 | 9.5 MB | 2026-11-16T10:15 |
| `raw.lp_picksandbanss7` | leaguepedia/PicksAndBansS7 | 99,106 | 55 | 2011–2026 (16) | — | 7.3 MB | 2026-11-16T10:28 |
| `raw.lp_playerredirects` | leaguepedia/PlayerRedirects | 33,465 | 4 | única | — | 1.0 MB | 2026-11-16T10:08 |
| `raw.lp_players` | leaguepedia/Players | 20,881 | 16 | única | — | 0.7 MB | 2026-11-16T09:48 |
| `raw.lp_scoreboardgames` | leaguepedia/ScoreboardGames | 138,751 | 70 | 2011–2026 (16) | 2011-06-18 a 2026-11-15 | 18.4 MB | 2026-11-16T10:24 |
| `raw.lp_scoreboardplayers` | leaguepedia/ScoreboardPlayers | 1,121,980 | 45 | 2011–2024 (14) | 2011-06-18 a 2024-12-22 | 75.8 MB | 2026-11-17T00:00 |
| `raw.lp_teamredirects` | leaguepedia/TeamRedirects | 5,827 | 4 | única | — | 0.1 MB | 2026-11-16T09:35 |
| `raw.lp_teams` | leaguepedia/Teams | 3,582 | 24 | única | — | 0.3 MB | 2026-11-16T09:31 |
| `raw.lp_tournamentplayers` | leaguepedia/TournamentPlayers | 452,548 | 12 | 2011–2026 (16) | — | 6.9 MB | 2026-11-16T12:30 |
| `raw.lp_tournaments` | leaguepedia/Tournaments | 10,562 | 34 | única | — | 0.8 MB | 2026-11-16T09:28 |
| `raw.lpgcd_pages` | leaguepedia_gcd/pages | 33,174 | 5 | única | 2018-02-20 a 2026-11-15 | 0.1 MB | 2026-11-17T00:35 |
| `raw.lpgcd_snapshots` | leaguepedia_gcd/snapshots | 4,045,305 | 10 | 2018–2026 (9) | 2018-02-20 a 2026-11-15 | 3.9 MB | 2026-11-17T00:35 |
| `raw.les_leagues` | lolesports/leagues | 51 | 7 | única | — | 0.0 MB | 2026-11-16T02:15 |
| `raw.les_match_games` | lolesports/match_games | 28,100 | 10 | única | — | 0.4 MB | 2026-11-16T02:15 |
| `raw.les_schedule` | lolesports/schedule | 11,442 | 21 | única | 2023-11-19 a 2026-11-14 | 0.2 MB | 2026-11-16T02:15 |
| `raw.les_standings_matches` | lolesports/standings_matches | 33,929 | 19 | única | — | 0.4 MB | 2026-11-16T14:53 |
| `raw.les_standings_rankings` | lolesports/standings_rankings | 4,457 | 14 | única | — | 0.1 MB | 2026-11-16T04:02 |
| `raw.les_standings_raw` | lolesports/standings_raw | 514 | 3 | única | — | 0.7 MB | 2026-11-16T19:53 |
| `raw.les_team_players` | lolesports/team_players | 5,436 | 5 | única | — | 0.1 MB | 2026-11-16T19:53 |
| `raw.les_teams` | lolesports/teams | 1,587 | 7 | única | — | 0.1 MB | 2026-11-16T19:53 |
| `raw.les_tournaments` | lolesports/tournaments | 514 | 6 | única | 2011-06-18 a 2026-11-09 | 0.0 MB | 2026-11-16T02:15 |
| `raw.mk_champions` | meraki/champions | 171 | 152 | única | — | 0.7 MB | 2026-11-16T03:59 |
| `raw.mk_champions_history` | meraki/champions_history | 171 | 153 | única | 2026-11-16 a 2026-11-16 | 0.7 MB | 2026-11-16T03:59 |
| `raw.mk_items` | meraki/items | 320 | 42 | única | — | 0.1 MB | 2026-11-16T03:59 |
| `raw.mk_items_history` | meraki/items_history | 320 | 43 | única | 2026-11-16 a 2026-11-16 | 0.1 MB | 2026-11-16T03:59 |
| `raw.odds_calls` | oddspapi/calls | 4 | 5 | 2026-11 | — | 0.3 MB | 2026-11-16T22:25 |
| `raw.odds_fixtures` | oddspapi/fixtures | 1 | 14 | única | 2026-11-14 a 2026-11-14 | 0.0 MB | 2026-11-16T22:22 |
| `raw.odds_historical_odds_raw` | oddspapi/historical_odds_raw | 1 | 4 | única | 2026-11-16 a 2026-11-16 | 0.3 MB | 2026-11-16T22:25 |
| `raw.oe_matches` | oracles_elixir/matches | 1,237,800 | 165 | 2014–2026 (13) | 2014-01-14 a 2026-11-14 | 205.4 MB | 2026-11-16T02:15 |
| `raw.ps_leagues` | pandascore/leagues | 136 | 5 | única | — | 0.1 MB | 2026-11-16T22:22 |
| `raw.ps_matches_past` | pandascore/matches_past | 48,034 | 26 | única | 2014-06-01 a 2026-11-14 | 12.3 MB | 2026-11-16T23:01 |
| `raw.ps_matches_upcoming` | pandascore/matches_upcoming | 6 | 26 | única | 2026-12-05 a 2026-12-05 | 0.0 MB | 2026-11-16T22:31 |
| `raw.ps_players` | pandascore/players | 9,916 | 11 | única | — | 0.8 MB | 2026-11-16T22:31 |
| `raw.ps_series` | pandascore/series | 870 | 11 | única | — | 0.2 MB | 2026-11-16T22:22 |
| `raw.ps_state` | pandascore/state | 1 | 2 | única | — | 0.0 MB | 2026-11-16T23:01 |
| `raw.ps_team_players` | pandascore/team_players | 2,675 | 4 | única | — | 0.0 MB | 2026-11-16T22:25 |
| `raw.ps_teams` | pandascore/teams | 1,948 | 7 | única | — | 0.3 MB | 2026-11-16T22:25 |
| `raw.ps_tournaments` | pandascore/tournaments | 2,209 | 16 | única | — | 7.9 MB | 2026-11-16T22:24 |
| `raw.pskill_game_events` | pandaskill/game_events | 1,884,740 | 8 | única | — | 14.5 MB | 2026-11-17T00:18 |
| `raw.pskill_game_metadata` | pandaskill/game_metadata | 37,459 | 9 | única | 2019-09-15 a 2024-09-14 | 0.6 MB | 2026-11-17T00:17 |
| `raw.pskill_game_players_stats` | pandaskill/game_players_stats | 374,554 | 28 | única | — | 9.3 MB | 2026-11-17T00:18 |
| `raw.pskill_pscores` | pandaskill/pscores | 373,880 | 3 | única | — | 2.1 MB | 2026-11-17T00:18 |
| `raw.pskill_skill_ratings` | pandaskill/skill_ratings | 373,880 | 20 | única | — | 37.3 MB | 2026-11-17T00:18 |
| `raw.gcd_contracts` | riot_gcd/contracts | 1,060 | 13 | única | — | 0.0 MB | 2026-11-17T00:22 |
| `raw.gcd_contracts_history` | riot_gcd/contracts_history | 1,401 | 14 | única | 2026-11-17 a 2026-11-17 | 0.0 MB | 2026-11-17T00:22 |
| `raw.gcd_sheets` | riot_gcd/sheets | 5 | 3 | única | — | 0.0 MB | 2026-11-17T00:18 |

**Total:** 10,894,797 linhas, 422.3 MB em Parquet.
