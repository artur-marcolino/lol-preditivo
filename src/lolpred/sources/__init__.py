"""Registro das fontes de dados. Para adicionar uma fonte nova, crie um módulo com uma
função `collect(ctx) -> SourceResult` e inclua-o aqui.

Fontes opcionais (precisam de chave; só rodam no CI se o segredo existir):
pandascore (PANDASCORE_TOKEN), oddspapi (ODDSPAPI_KEY) e esports_earnings (ESPORTSEARNINGS_KEY).
"""

from . import (ddragon, esports_earnings, esportsbench, leaguepedia, leaguepedia_gcd, lolesports, lolwiki,
               meraki, oddspapi, oracles_elixir, pandascore, pandaskill, riot_gcd)

SOURCES = {
    "oracles_elixir": oracles_elixir.collect,
    "leaguepedia": leaguepedia.collect,
    "lolesports": lolesports.collect,
    "ddragon": ddragon.collect,
    "meraki": meraki.collect,
    "lolwiki": lolwiki.collect,
    "pandascore": pandascore.collect,
    "oddspapi": oddspapi.collect,
    "riot_gcd": riot_gcd.collect,
    "leaguepedia_gcd": leaguepedia_gcd.collect,
    "pandaskill": pandaskill.collect,
    "esportsbench": esportsbench.collect,
    "esports_earnings": esports_earnings.collect,
}
