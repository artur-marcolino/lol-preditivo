# Catálogo de dados

Gerado automaticamente em 2026-11-16 10:00 UTC a partir de `data/manifest/`. Não edite à mão.

## Fontes

| Fonte | Status | Última tentativa | Último sucesso | Observação |
|---|---|---|---|---|
| ddragon | ok | 2026-11-16T03:26:18+00:00 | 2026-11-16T03:26:18+00:00 |  |
| leaguepedia | em andamento (carga histórica) | 2026-11-16T09:19:14+00:00 | 2026-11-16T09:19:14+00:00 |  |
| lolesports | ok | 2026-11-16T04:02:58+00:00 | 2026-11-16T04:02:58+00:00 |  |
| lolwiki | falhou | 2026-11-16T04:02:31+00:00 |  | Module:ChampionData/data: api.php: HTTP 403 (<html lang="en"> <head> <title>Request blocked</title> <meta name="viewport" content="width=dev… |
| meraki | ok | 2026-11-16T03:59:22+00:00 | 2026-11-16T03:59:22+00:00 |  |
| oracles_elixir | ok | 2026-11-16T03:26:29+00:00 | 2026-11-16T03:26:29+00:00 |  |

## Tabelas

| View no DuckDB | Fonte/tabela | Linhas | Colunas | Partições | Período | Tamanho | Atualizada em |
|---|---|---:|---:|---|---|---:|---|
| `raw.dd_champions` | ddragon/champions | 47,070 | 35 | única | — | 0.2 MB | 2026-11-16T02:15 |
| `raw.dd_items` | ddragon/items | 106,889 | 26 | única | — | 0.3 MB | 2026-11-16T02:16 |
| `raw.lp_leagues` | leaguepedia/Leagues | 194 | 6 | única | — | 0.0 MB | 2026-11-16T04:18 |
| `raw.lp_matchschedule` | leaguepedia/MatchSchedule | 174,177 | 74 | 2011–2026 (16) | 2011-03-14 a 2026-12-13 | 9.5 MB | 2026-11-16T06:27 |
| `raw.lp_picksandbanss7` | leaguepedia/PicksAndBansS7 | 99,104 | 55 | 2011–2026 (16) | — | 7.3 MB | 2026-11-16T08:02 |
| `raw.lp_playerredirects` | leaguepedia/PlayerRedirects | 33,463 | 4 | única | — | 1.0 MB | 2026-11-16T05:00 |
| `raw.lp_players` | leaguepedia/Players | 20,879 | 16 | única | — | 0.7 MB | 2026-11-16T04:42 |
| `raw.lp_scoreboardgames` | leaguepedia/ScoreboardGames | 138,749 | 70 | 2011–2026 (16) | 2011-06-18 a 2026-11-15 | 18.4 MB | 2026-11-16T07:22 |
| `raw.lp_teamredirects` | leaguepedia/TeamRedirects | 5,826 | 4 | única | — | 0.1 MB | 2026-11-16T04:30 |
| `raw.lp_teams` | leaguepedia/Teams | 3,581 | 24 | única | — | 0.3 MB | 2026-11-16T04:27 |
| `raw.lp_tournamentplayers` | leaguepedia/TournamentPlayers | 173,851 | 12 | 2011–2020 (10) | — | 2.7 MB | 2026-11-16T09:10 |
| `raw.lp_tournaments` | leaguepedia/Tournaments | 10,561 | 34 | única | — | 0.8 MB | 2026-11-16T04:23 |
| `raw.les_leagues` | lolesports/leagues | 51 | 7 | única | — | 0.0 MB | 2026-11-16T02:15 |
| `raw.les_match_games` | lolesports/match_games | 28,100 | 10 | única | — | 0.4 MB | 2026-11-16T02:15 |
| `raw.les_schedule` | lolesports/schedule | 11,442 | 21 | única | 2023-11-19 a 2026-11-14 | 0.2 MB | 2026-11-16T02:15 |
| `raw.les_standings_matches` | lolesports/standings_matches | 33,929 | 19 | única | — | 0.4 MB | 2026-11-16T04:02 |
| `raw.les_standings_rankings` | lolesports/standings_rankings | 4,457 | 14 | única | — | 0.1 MB | 2026-11-16T04:02 |
| `raw.les_standings_raw` | lolesports/standings_raw | 514 | 3 | única | — | 0.7 MB | 2026-11-16T04:02 |
| `raw.les_team_players` | lolesports/team_players | 5,436 | 5 | única | — | 0.1 MB | 2026-11-16T04:00 |
| `raw.les_teams` | lolesports/teams | 1,587 | 7 | única | — | 0.1 MB | 2026-11-16T02:15 |
| `raw.les_tournaments` | lolesports/tournaments | 514 | 6 | única | 2011-06-18 a 2026-11-09 | 0.0 MB | 2026-11-16T02:15 |
| `raw.mk_champions` | meraki/champions | 171 | 152 | única | — | 0.7 MB | 2026-11-16T03:59 |
| `raw.mk_champions_history` | meraki/champions_history | 171 | 153 | única | 2026-11-16 a 2026-11-16 | 0.7 MB | 2026-11-16T03:59 |
| `raw.mk_items` | meraki/items | 320 | 42 | única | — | 0.1 MB | 2026-11-16T03:59 |
| `raw.mk_items_history` | meraki/items_history | 320 | 43 | única | 2026-11-16 a 2026-11-16 | 0.1 MB | 2026-11-16T03:59 |
| `raw.oe_matches` | oracles_elixir/matches | 1,237,800 | 165 | 2014–2026 (13) | 2014-01-14 a 2026-11-14 | 205.4 MB | 2026-11-16T02:15 |

**Total:** 2,139,156 linhas, 250.2 MB em Parquet.
