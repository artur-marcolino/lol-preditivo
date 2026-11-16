"""Leitor mínimo de tabelas Lua (o formato dos módulos de dados das wikis MediaWiki).

Converte algo como::

    return {
      ["Aatrox"] = { ["id"] = 266, ["role"] = {"Juggernaut"}, ["stats"] = { hp_base = 650 } },
    }

em dicionários/listas Python. Suporta strings ("..." e '...', com escapes, e [[...]]),
números (inclusive hexadecimais e notação científica), true/false/nil, comentários
(-- linha e --[[ bloco ]]), chaves ["x"], [1] e identificadores, e vírgula ou ponto e
vírgula como separador. Não executa código: expressões e funções geram erro.
"""

from __future__ import annotations

import re

_NUMBER = re.compile(r"-?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LONG_BRACKET = re.compile(r"\[(=*)\[")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "\n": "\n", "a": "\a", "b": "\b",
            "f": "\f", "v": "\v"}


class LuaParseError(ValueError):
    pass


class _Parser:
    def __init__(self, text: str):
        self.s = text
        self.i = 0

    # ------------------------------------------------------------------ utilidades
    def error(self, msg: str) -> LuaParseError:
        line = self.s.count("\n", 0, self.i) + 1
        return LuaParseError(f"{msg} (linha {line})")

    def skip(self) -> None:
        s = self.s
        while self.i < len(s):
            c = s[self.i]
            if c.isspace():
                self.i += 1
            elif s.startswith("--", self.i):
                m = _LONG_BRACKET.match(s, self.i + 2)
                if m:  # comentário de bloco --[[ ... ]]
                    end = s.find("]" + m.group(1) + "]", m.end())
                    self.i = len(s) if end < 0 else end + len(m.group(1)) + 2
                else:
                    end = s.find("\n", self.i)
                    self.i = len(s) if end < 0 else end + 1
            else:
                break

    def peek(self, token: str) -> bool:
        self.skip()
        return self.s.startswith(token, self.i)

    def expect(self, token: str) -> None:
        if not self.peek(token):
            raise self.error(f"esperava {token!r}")
        self.i += len(token)

    # ------------------------------------------------------------------ valores
    def value(self):
        self.skip()
        s, i = self.s, self.i
        if i >= len(s):
            raise self.error("fim inesperado")
        c = s[i]
        if c == "{":
            return self.table()
        if c in "\"'":
            return self.string()
        if c == "[" and _LONG_BRACKET.match(s, i):
            return self.long_string()
        m = _NUMBER.match(s, i)
        if m and (c.isdigit() or c in "-."):
            self.i = m.end()
            text = m.group(0)
            if "x" in text.lower():
                return int(text, 16)
            return float(text) if any(ch in text for ch in ".eE") else int(text)
        m = _IDENT.match(s, i)
        if m:
            word = m.group(0)
            self.i = m.end()
            if word == "true":
                return True
            if word == "false":
                return False
            if word == "nil":
                return None
            raise self.error(f"identificador inesperado {word!r} (o leitor não executa expressões)")
        raise self.error(f"caractere inesperado {c!r}")

    def string(self) -> str:
        quote = self.s[self.i]
        self.i += 1
        out = []
        while True:
            if self.i >= len(self.s):
                raise self.error("string não terminada")
            c = self.s[self.i]
            if c == quote:
                self.i += 1
                return "".join(out)
            if c == "\\":
                nxt = self.s[self.i + 1: self.i + 2]
                if nxt.isdigit():
                    m = re.match(r"\d{1,3}", self.s[self.i + 1:])
                    out.append(chr(int(m.group(0))))
                    self.i += 1 + len(m.group(0))
                    continue
                out.append(_ESCAPES.get(nxt, nxt))
                self.i += 2
                continue
            out.append(c)
            self.i += 1

    def long_string(self) -> str:
        m = _LONG_BRACKET.match(self.s, self.i)
        close = "]" + m.group(1) + "]"
        end = self.s.find(close, m.end())
        if end < 0:
            raise self.error("string longa não terminada")
        text = self.s[m.end():end]
        self.i = end + len(close)
        return text[1:] if text.startswith("\n") else text

    def table(self):
        self.expect("{")
        array: list = []
        mapping: dict = {}
        while True:
            self.skip()
            if self.peek("}"):
                self.i += 1
                break
            # [chave] = valor
            if self.s.startswith("[", self.i) and not _LONG_BRACKET.match(self.s, self.i):
                self.i += 1
                key = self.value()
                self.expect("]")
                self.expect("=")
                mapping[key] = self.value()
            else:
                m = _IDENT.match(self.s, self.i)
                after = self.s[m.end():].lstrip()[:2] if m else ""
                if m and after.startswith("=") and not after.startswith("=="):
                    self.i = m.end()
                    self.expect("=")
                    mapping[m.group(0)] = self.value()
                else:
                    array.append(self.value())
            self.skip()
            if self.peek(",") or self.peek(";"):
                self.i += 1
        if mapping and array:
            for n, v in enumerate(array, start=1):
                mapping.setdefault(n, v)
            return mapping
        return mapping if mapping else array


def loads(text: str):
    """Lê o valor retornado por um módulo Lua de dados (`return { ... }`)."""
    p = _Parser(text)
    p.skip()
    if p.s.startswith("return", p.i):
        p.i += len("return")
    value = p.value()
    p.skip()
    return value
