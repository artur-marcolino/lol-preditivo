# Catálogo de dados

Gerado automaticamente em 2026-11-16 04:29 UTC a partir de `data/manifest/`. Não edite à mão.

## Fontes

| Fonte | Status | Última tentativa | Último sucesso | Observação |
|---|---|---|---|---|
| ddragon | ok | 2026-11-16T03:26:18+00:00 | 2026-11-16T03:26:18+00:00 |  |
| leaguepedia | falhou | 2026-11-16T03:24:52+00:00 |  | Leagues [all]: cargoquery-invalidfieldalias: Error: Field alias "_pageName" starts with an underscore (_). This is not allowed in Cargo API … |
| lolesports | ok | 2026-11-16T04:02:58+00:00 | 2026-11-16T04:02:58+00:00 |  |
| lolwiki | falhou | 2026-11-16T04:02:31+00:00 |  | Module:ChampionData/data: api.php: HTTP 403 (<html lang="en"> <head> <title>Request blocked</title> <meta name="viewport" content="width=dev… |
| meraki | ok | 2026-11-16T03:59:22+00:00 | 2026-11-16T03:59:22+00:00 |  |
| oracles_elixir | ok | 2026-11-16T03:26:29+00:00 | 2026-11-16T03:26:29+00:00 |  |

## Tabelas

| View no DuckDB | Fonte/tabela | Linhas | Colunas | Partições | Período | Tamanho | Atualizada em |
|---|---|---:|---:|---|---|---:|---|
| `raw.dd_champions` | ddragon/champions | 47,070 | 35 | única | — | 0.2 MB | 2026-11-16T02:15 |
| `raw.dd_items` | ddragon/items | 106,889 | 26 | única | — | 0.3 MB | 2026-11-16T02:16 |
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

**Total:** 1,478,771 linhas, 209.4 MB em Parquet.
