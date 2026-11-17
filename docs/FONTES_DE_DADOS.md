# Fontes de dados: o que usamos e o que ficou de fora

Resultado da varredura de fontes gratuitas (novembro de 2026). Critérios: gratuita, com
atualização recorrente, sem complexidade desproporcional e com termos de uso compatíveis
com um repositório público (os dados coletados ficam numa Release pública).

## Em uso (coleta automática diária)

| Fonte | O que traz | Observações |
|---|---|---|
| **Oracle's Elixir** | Estatísticas de todos os jogos profissionais desde 2014 (jogador e time) | Base principal do banco analítico. |
| **Leaguepedia** (Cargo API) | Calendário com jogos futuros, placares, draft, elencos por torneio, nomes antigos de times/jogadores, dados de jogadores | Funciona sem login. Com um *bot password* da Fandom (opcional) os limites ficam maiores. |
| **lolesports.com** (API não oficial) | Ligas, torneios, calendário oficial passado e futuro, mapas de cada série, times e elencos, **classificação e chaveamento de cada etapa** (`standings_*`) | A API não é documentada e pode mudar; a resposta bruta das classificações fica guardada em `standings_raw`. |
| **Data Dragon** (Riot) | Atributos de campeões e itens em cada patch | Oficial. |
| **Meraki Analytics** (lolstaticdata) | Classes detalhadas dos campeões (JUGGERNAUT, BURST...), posições, notas de dano/resistência/controle/mobilidade/utilidade, dados de itens | **Desatualizado**: parou de ser atualizado no patch 25.15 (meados de 2025); campeões lançados depois não aparecem. As classes e notas dos 171 campeões que existem continuam úteis, porque mudam pouco. |

### Dados de jogadores (adicionadas em 16/11/2026)

| Fonte | O que traz | Observações |
|---|---|---|
| **GCD da Riot** (planilha pública ligada ao site competitiveops.riotgames.com) | Contratos vigentes por região (AME, CN, EMEA, KR, LCP): liga, time, nick, posição, fim de contrato, residência, status | Só mostra o estado atual; guardamos o histórico a partir de 16/11/2026. A planilha traz nome civil e e-mail de contato dos times, que **não** são guardados. As cores das linhas (reserva, inativo, "two-way") se perdem no CSV. |
| **Arquivo do GCD na Leaguepedia** (`Archive:Global_Contract_Database/<região>/<data>`) | Cópia diária do GCD desde 2018, por região (inclui regiões que já não existem: BR, CIS, EU, JPN, LAN...) | Cerca de 33 mil páginas; a primeira carga leva algumas rodadas. Nomes civis e contatos também são descartados. |
| **PandaSkill** (repositório aberto da PandaScore, mesmo conteúdo do IEEE DataPort, DOI 10.21227/5evv-jk25) | Estatísticas de fim de jogo por jogador (28 campos), eventos de abate e os ratings PandaSkill, 37 mil jogos de set/2019 a set/2024 | Não precisa da assinatura do IEEE: os arquivos estão no GitHub (Git LFS). O rating vem em duas versões, antes e depois de cada jogo; para variáveis pré-partida, só as colunas `_before`. |
| **EsportsBench** (Hugging Face) | Resultados de LoL desde 2011 em nível de time, com cortes de treino/teste por versão | Derivado da Leaguepedia, então não traz partidas novas; serve para comparar nosso Elo com a literatura de sistemas de rating. |

## Opcionais (precisam de conta gratuita; rodam só se o segredo existir)

| Fonte | O que traz | Como ativar |
|---|---|---|
| **PandaScore** | Tier de cada torneio (S/A/B/C/D), jogadores com nacionalidade e data de nascimento, uma segunda base de resultados e partidas futuras | Crie conta em pandascore.co (plano gratuito "Fixtures", 1.000 req/hora), copie o token e salve como segredo `PANDASCORE_TOKEN`. Os termos proíbem uso para apostas. |
| **Esports Earnings** | Premiação e colocação de times e jogadores em ~3 mil torneios de LoL (GameId 164), com data | Crie conta em esportsearnings.com, pegue a chave na página "API Stats" da área de desenvolvedor e salve como segredo `ESPORTSEARNINGS_KEY`. A API aceita 1 requisição por segundo; a primeira carga leva algumas horas. |
| **OddsPapi** | Histórico de odds de casas de aposta por partida, como **régua de comparação** para os modelos | Crie conta em oddspapi.io (plano gratuito: 250 requisições/mês, histórico incluso) e salve como segredo `ODDSPAPI_KEY`. A coleta usa no máximo ~8 chamadas por dia e 240 por mês. |

Os dois coletores foram escritos a partir da documentação pública e testados com respostas
simuladas; a primeira execução real, depois de configurar a chave, é o teste definitivo. As
respostas do OddsPapi são guardadas brutas e interpretadas depois, quando virmos o formato real.

**Como salvar um segredo:** no GitHub, repositório → Settings → Secrets and variables → Actions →
New repository secret.

## Manual (não roda no GitHub Actions)

| Fonte | Motivo |
|---|---|
| **LoL Wiki** (wiki.leagueoflegends.com, Weird Gloop) | Teria os dados de campeões mais atuais (data de lançamento, último patch alterado, classes). Mas a wiki bloqueia requisições vindas de servidores na nuvem ("Request blocked"), por causa de robôs de IA que sobrecarregam o site. Contornar o bloqueio (fingir ser navegador, usar proxy) não seria correto. O coletor existe e funciona a partir de uma conexão doméstica: `lolpred collect -s lolwiki`. |

## Avaliadas e descartadas

| Fonte | Por que ficou de fora |
|---|---|
| **API de estatísticas da LPL** (lpl.qq.com / tjstats) | Cobriria a lacuna de estatísticas da LPL/LDL, mas os dados de partida só saem com um token de acesso tirado das páginas, a API não é documentada e só parte dela funciona. Usar o token fora do site é contornar o controle de acesso. Parte da lacuna já é coberta pela Leaguepedia (`ScoreboardPlayers`) e pelo PandaSkill. |
| **lolpros.gg / trackingthepros** (jogador → contas de soloQ) | O mapeamento só tem valor junto com o histórico de soloQ, que exige chave da Riot API (as chaves pessoais expiram a cada 24 h; a de produto exige aprovação). Fica para quando houver essa chave. |
| **OP.GG** (inclusive o servidor MCP gratuito) | Os termos permitem só uso pessoal e proíbem repassar os dados a terceiros; o nosso repositório e a Release são públicos. |
| **gol.gg** | Site comunitário mantido por Patreon, sem API; raspar o site em massa e republicar os dados não é apropriado. |
| **u.gg, Mobalytics, lolalytics, Blitz** (meta da soloQ) | Sem API pública; os termos restringem raspagem e redistribuição. |
| **GRID Open Access** | O acesso gratuito cobre CS2 e Dota 2; LoL só em hackathons ou para times profissionais. |
| **Riot API** (histórico de soloQ dos profissionais) | Exige chave pessoal (registro de produto) e um mapeamento confiável conta → jogador, que não existe numa fonte aberta. Pode entrar no futuro. |
| **Feed de estatísticas ao vivo do lolesports** (livestats) | Gratuito, mas cada chamada traz só ~10 segundos de jogo: montar a série de um jogo exige dezenas de chamadas e o Oracle's Elixir já traz os marcos de 10/15/20/25 min. Fica para quando formos modelar probabilidade de vitória *durante* o jogo. |
| **Liquipedia** | A API estruturada (LPDB) exige cadastro; a API da wiki exige interpretar o texto das páginas. A Leaguepedia cobre o mesmo conteúdo. |
| **The Odds API, odds-api.io, OpticOdds** | Planos gratuitos sem histórico, pausados ou inexistentes. |
| **Sites de notas de atualização** (patch notes) | Sem dados estruturados com licença clara; mudanças de atributos base saem do Data Dragon, comparando patches. |
