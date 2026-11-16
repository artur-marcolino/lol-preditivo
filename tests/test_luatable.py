import pytest

from lolpred.luatable import LuaParseError, loads

SAMPLE = r'''
-- <pre>
-- Comentário de linha
--[[ comentário
de bloco ]]
return {
  ["Aatrox"] = {
    ["id"]       = 266,
    ["apiname"]  = "Aatrox",
    ["title"]    = "the Darkin Blade",
    ["role"]     = {"Juggernaut"},
    ["client_positions"] = {"Top", },
    ["date"]     = "2013-06-13",
    ["changes"]  = 'V26.09',
    ["rangetype"]= "Melee",
    ["stats"]    = {
      hp_base   = 650,
      hp_lvl    = 114,
      as_ratio  = 0.651,
      neg       = -5,
      sci       = 1e3,
      hex       = 0x1F,
      ["aram"]  = { dmg_dealt = 1.05; dmg_taken = 0.95 },
    },
    ["skill_i"]  = { [1] = "Deathbringer Stance" },
    ["nickname"] = "Darkin \"Blade\"\tX",
    ["lore"]     = [[Linha 1
Linha 2]],
    ["active"]   = true,
    ["removed"]  = false,
    ["missing"]  = nil,
  },
  ["Ahri"] = { ["id"] = 103, ["role"] = {"Burst", "Assassin"} },
}
-- </pre>
'''


def test_parses_wiki_style_module():
    data = loads(SAMPLE)
    a = data["Aatrox"]
    assert a["id"] == 266 and a["role"] == ["Juggernaut"] and a["client_positions"] == ["Top"]
    assert a["stats"]["hp_base"] == 650 and a["stats"]["as_ratio"] == pytest.approx(0.651)
    assert a["stats"]["neg"] == -5 and a["stats"]["sci"] == 1000.0 and a["stats"]["hex"] == 31
    assert a["stats"]["aram"] == {"dmg_dealt": 1.05, "dmg_taken": 0.95}
    assert a["skill_i"] == {1: "Deathbringer Stance"}
    assert a["nickname"] == 'Darkin "Blade"\tX'
    assert a["lore"] == "Linha 1\nLinha 2"
    assert a["active"] is True and a["removed"] is False and a["missing"] is None
    assert data["Ahri"]["role"] == ["Burst", "Assassin"]


def test_refuses_code():
    with pytest.raises(LuaParseError):
        loads("return { x = some_function() }")
