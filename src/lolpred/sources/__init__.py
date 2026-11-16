"""Registro das fontes de dados. Para adicionar uma fonte nova, crie um módulo com uma
função `collect(ctx) -> SourceResult` e inclua-o aqui.

Fontes opcionais (precisam de chave; só rodam no CI se o segredo existir):
pandascore (PANDASCORE_TOKEN) e oddspapi (ODDSPAPI_KEY).
"""

from . import ddragon, leaguepedia, lolesports, lolwiki, meraki, oddspapi, oracles_elixir, pandascore

SOURCES = {
    "oracles_elixir": oracles_elixir.collect,
    "leaguepedia": leaguepedia.collect,
    "lolesports": lolesports.collect,
    "ddragon": ddragon.collect,
    "meraki": meraki.collect,
    "lolwiki": lolwiki.collect,
    "pandascore": pandascore.collect,
    "oddspapi": oddspapi.collect,
}
