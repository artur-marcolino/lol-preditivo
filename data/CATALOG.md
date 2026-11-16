# Catálogo de dados

Gerado automaticamente em 2026-11-16 03:56 UTC a partir de `data/manifest/`. Não edite à mão.

## Fontes

| Fonte | Status | Última tentativa | Último sucesso | Observação |
|---|---|---|---|---|
| ddragon | ok | 2026-11-16T03:26:18+00:00 | 2026-11-16T03:26:18+00:00 |  |
| leaguepedia | falhou | 2026-11-16T03:24:52+00:00 |  | Leagues [all]: cargoquery-invalidfieldalias: Error: Field alias "_pageName" starts with an underscore (_). This is not allowed in Cargo API … |
| lolesports | ok | 2026-11-16T03:27:24+00:00 | 2026-11-16T03:27:24+00:00 |  |
| oracles_elixir | ok | 2026-11-16T03:26:29+00:00 | 2026-11-16T03:26:29+00:00 |  |

## Tabelas

| View no DuckDB | Fonte/tabela | Linhas | Colunas | Partições | Período | Tamanho | Atualizada em |
|---|---|---:|---:|---|---|---:|---|
| `raw.dd_champions` | ddragon/champions | 47,070 | 35 | única | — | 0.2 MB | 2026-11-16T02:15 |
| `raw.dd_items` | ddragon/items | 106,889 | 26 | única | — | 0.3 MB | 2026-11-16T02:16 |
| `raw.les_leagues` | lolesports/leagues | 51 | 7 | única | — | 0.0 MB | 2026-11-16T02:15 |
| `raw.les_match_games` | lolesports/match_games | 28,100 | 10 | única | — | 0.4 MB | 2026-11-16T02:15 |
| `raw.les_schedule` | lolesports/schedule | 11,442 | 21 | única | 2023-11-19 a 2026-11-14 | 0.2 MB | 2026-11-16T02:15 |
| `raw.les_team_players` | lolesports/team_players | 5,436 | 5 | única | — | 0.1 MB | 2026-11-16T03:27 |
| `raw.les_teams` | lolesports/teams | 1,587 | 7 | única | — | 0.1 MB | 2026-11-16T02:15 |
| `raw.les_tournaments` | lolesports/tournaments | 514 | 6 | única | 2011-06-18 a 2026-11-09 | 0.0 MB | 2026-11-16T02:15 |
| `raw.oe_matches` | oracles_elixir/matches | 1,237,800 | 165 | 2014–2026 (13) | 2014-01-14 a 2026-11-14 | 205.4 MB | 2026-11-16T02:15 |

**Total:** 1,438,889 linhas, 206.7 MB em Parquet.
