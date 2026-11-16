"""Registro das fontes de dados. Para adicionar uma fonte nova, crie um módulo com uma
função `collect(ctx) -> SourceResult` e inclua-o aqui."""

from . import ddragon, leaguepedia, lolesports, oracles_elixir

SOURCES = {
    "oracles_elixir": oracles_elixir.collect,
    "leaguepedia": leaguepedia.collect,
    "lolesports": lolesports.collect,
    "ddragon": ddragon.collect,
}
