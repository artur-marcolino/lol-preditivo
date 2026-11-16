# lol-preditivo

Projeto recreativo de modelagem preditiva para League of Legends competitivo. O objetivo
principal é prever o resultado de partidas profissionais.

Este repositório tem duas partes:

1. **Coleta** reprodutível e incremental de várias fontes, que se atualiza sozinha conforme
   novas partidas acontecem.
2. **Banco analítico** (DuckDB) em camadas, com dimensões, fatos e variáveis **pré-partida sem
   vazamento** (cada série só "enxerga" jogos anteriores a ela), pronto para análise e modelagem.
   Documentação completa em **[docs/MODELO_DE_DADOS.md](docs/MODELO_DE_DADOS.md)**.

## Como funciona

```
 Fontes                      Coletores (src/lolpred/sources)       Armazenamento
 ─────────────────────────   ──────────────────────────────────    ───────────────────────────────
 Oracle's Elixir (Drive)  →  oracles_elixir.py ─┐
 Leaguepedia (Cargo API)  →  leaguepedia.py    ─┼→ Parquet por     → Release "data-latest" (arquivos)
 lolesports.com (API)     →  lolesports.py     ─┤  tabela/ano       → data/manifest/*.json (no git)
 Data Dragon (Riot)       →  ddragon.py        ─┘

 lolpred build-db:  raw (views) → stg (limpeza) → core (dimensões e fatos) → feat (variáveis
                    pré-partida) → mart (tabelas de modelagem)   =  data/lol.duckdb
```

- **Coleta automática no GitHub Actions** (`.github/workflows/collect.yml`): roda todo dia,
  com um job por fonte em paralelo. A internet do Actions alcança todas as fontes, e a coleta
  não depende de nenhum computador ligado.
- **Incremental**: anos já encerrados são baixados uma vez só. Em toda execução são
  recoletados apenas o ano corrente (mais o anterior em janeiro e fevereiro) e as séries
  novas. Tabelas pequenas são baixadas inteiras.
- **Carga histórica com orçamento de tempo**: a primeira coleta leva horas. Cada fonte para de
  forma limpa ao fim do orçamento (300 min por padrão) e o workflow dispara a si mesmo para
  continuar de onde parou, até 8 vezes.
- **Dados fora do git, metadados dentro**: os Parquet ficam na Release `data-latest`, que é
  sobrescrita a cada coleta. No git ficam os manifestos (`data/manifest/<fonte>.json`), com
  número de linhas, colunas, período e um *fingerprint* (hash do conteúdo) de cada arquivo.
  O histórico do git mostra, portanto, quando cada tabela mudou e quanto cresceu, sem
  inchar o repositório.
- **Snapshots mensais**: no dia 1 de cada mês é criada uma Release congelada
  (`data-AAAA-MM-DD`). Para refazer uma análise com exatamente os mesmos dados de uma data,
  use `lolpred pull --tag data-2026-12-01`.
- **Falhas isoladas**: se uma fonte sair do ar ou mudar o formato, as outras continuam, e a
  versão anterior dos dados é mantida. O erro aparece em `data/runs/<fonte>.json` e em
  `data/CATALOG.md`.

O estado atual de cada tabela está em **[data/CATALOG.md](data/CATALOG.md)** (gerado
automaticamente).

## Fontes e tabelas

| Fonte | Tabelas (view no DuckDB) | O que tem | Atualização |
|---|---|---|---|
| [Oracle's Elixir](https://oracleselixir.com) | `oe_matches` | Uma linha por jogador e duas por time em cada jogo (coluna `position = 'team'`). Draft, lado, patch, objetivos, KDA, dano, visão, ouro/XP/CS aos 10, 15, 20 e 25 min. De 2014 em diante, dezenas de ligas. | Diária (ano corrente) |
| [Leaguepedia](https://lol.fandom.com) | `lp_matchschedule`, `lp_scoreboardgames`, `lp_scoreboardplayers`, `lp_picksandbanss7`, `lp_tournaments`, `lp_tournamentplayers`, `lp_teams`, `lp_teamredirects`, `lp_players`, `lp_playerredirects`, `lp_leagues` | Calendário com partidas futuras, formato das séries (Bo1/3/5), placares, elencos por torneio, ordem do draft, nomes antigos de times e jogadores (redirects), tier dos torneios. | Diária (ano corrente) |
| [lolesports.com](https://lolesports.com) (API não oficial) | `les_leagues`, `les_tournaments`, `les_schedule`, `les_match_games`, `les_teams`, `les_team_players` | Calendário oficial (passado e futuro), mapas de cada série com lado azul/vermelho e `game_id` (chave do feed de estatísticas ao vivo). | Diária |
| [Data Dragon](https://developer.riotgames.com/docs/lol#data-dragon) (Riot) | `dd_champions`, `dd_items` | Atributos de campeões e itens em cada patch: classe, recurso, atributos base, notas de ataque/defesa/magia/dificuldade, preço de itens. | A cada patch novo |

Os dados são guardados **fiéis à fonte**: colunas de texto ficam como texto (a Leaguepedia
devolve tudo como string, por exemplo). A conversão de tipos e a limpeza ficam para a etapa
de preparação dos modelos.

## Uso local

```bash
git clone https://github.com/zLanchus/lol-preditivo.git
cd lol-preditivo
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

lolpred pull        # baixa os Parquet da Release
lolpred build-db    # monta data/lol.duckdb (raw, stg, core, feat, mart) em menos de 1 min
lolpred check-db    # verificações de integridade e de vazamento temporal
lolpred status      # mostra o catálogo
```

Consultando:

```python
import duckdb
con = duckdb.connect("data/lol.duckdb", read_only=True)

# uma linha por série, com variáveis dos dois times calculadas antes da série
df = con.sql("""
    SELECT * FROM mart.pregame_series
    WHERE is_clean AND league_tier <= 2 AND season >= 2018
""").df()
```

Também dá para ler os Parquet direto com `pandas.read_parquet("data/processed/oracles_elixir/matches/")`.

Coletando localmente (útil para testar uma mudança num coletor):

```bash
lolpred collect -s ddragon                      # uma fonte
lolpred collect -s oracles_elixir --years 2025  # só um ano
lolpred collect --full --max-minutes 60         # tudo, parando em 1 h
pytest -q                                       # testes (rodam offline, com respostas simuladas)
```

## Coleta sob demanda e configurações opcionais

Na aba **Actions > Coleta de dados > Run workflow** dá para escolher fontes, anos, recoleta
completa e orçamento de tempo.

Segredos opcionais (Settings > Secrets and variables > Actions):

| Segredo | Para quê |
|---|---|
| `LEAGUEPEDIA_USER`, `LEAGUEPEDIA_PASSWORD` | [Bot password](https://lol.fandom.com/wiki/Special:BotPasswords) da Fandom. O acesso anônimo à Leaguepedia tem limite baixo de requisições; com login a carga histórica fica bem mais rápida. |
| `GOOGLE_API_KEY` | Chave da Google Drive API. Rota alternativa quando o Drive responde "Quota exceeded" nos arquivos do Oracle's Elixir (acontece em dias de muita procura). |

## Estrutura

```
src/lolpred/
  sources/            um módulo por fonte, cada um com collect(ctx) -> SourceResult
  storage.py          gravação em Parquet, fingerprint e manifestos
  release.py          publicação/download pela Release do GitHub e snapshots
  db.py               monta data/lol.duckdb
  sql/                camadas do banco analítico (stg, core, feat, mart)
  warehouse/          etapas em Python: linhagem de times, Elo, tabelas de modelagem, verificações
  seeds/              tabelas mantidas à mão: tier/região das ligas, correções de linhagem
  catalog.py          gera data/CATALOG.md
  cli.py              comando `lolpred`
tests/                testes offline (respostas simuladas e um mini-campeonato sintético)
docs/                 documentação do modelo de dados
data/
  manifest/           metadados versionados de cada arquivo (no git)
  runs/               log da última coleta de cada fonte (no git)
  CATALOG.md          resumo legível (no git)
  processed/          Parquet (fora do git; vem da Release)
```

Para adicionar uma fonte nova: crie `src/lolpred/sources/<fonte>.py` com
`collect(ctx) -> SourceResult`, registre em `sources/__init__.py` e inclua o nome na lista
`all` do job `plan` do workflow.

## Créditos e termos de uso das fontes

- **Oracle's Elixir**: dados compilados por Tim Sevenhuysen e distribuídos gratuitamente
  para analistas e fãs. Se o projeto for útil, considere apoiar o
  [Patreon](https://www.patreon.com/oracleselixir).
- **Leaguepedia**: conteúdo sob licença [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/).
  Se os dados forem redistribuídos, mantenha a atribuição e a mesma licença.
- **lolesports.com**: API interna e não documentada do site oficial, usada com a chave pública
  que o próprio site expõe. Pode mudar sem aviso.
- **Data Dragon**: dados estáticos públicos da Riot Games. Este projeto não é endossado pela
  Riot Games e não reflete as opiniões da Riot Games ou de qualquer pessoa oficialmente
  envolvida na produção ou gestão de League of Legends.

A coleta é feita em ritmo moderado (pausas entre requisições, retentativas com espera
crescente) para não sobrecarregar nenhuma fonte.

## Próximos passos

- [x] Banco analítico em camadas com variáveis pré-partida sem vazamento (Oracle's Elixir).
- [ ] Integrar Leaguepedia e lolesports ao core: calendário futuro e nomes alternativos de times.
- [ ] Baseline pré-jogo: Elo + lado do mapa, avaliado com Brier score e log-loss em validação temporal.
- [ ] Feed de estatísticas ao vivo (livestats) a partir de `les_match_games.game_id`, para o modelo de probabilidade de vitória durante o jogo.
- [ ] Fontes extras: histórico de soloQ dos jogadores (Riot API, exige chave) e odds históricas como régua de comparação.
