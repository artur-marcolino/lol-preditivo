"""Linha de comando.

Exemplos:
    lolpred pull                                  # baixa os dados já coletados (Release)
    lolpred collect                               # coleta incremental de todas as fontes
    lolpred collect -s oracles_elixir --years 2024 2025
    lolpred collect --full --max-minutes 300      # recoleta tudo, parando em 5 h
    lolpred build-db                              # monta data/lol.duckdb
    lolpred status                                # mostra o catálogo
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import catalog, collect, db, release
from .sources import SOURCES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lolpred", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("collect", help="coleta dados das fontes")
    p.add_argument("-s", "--source", action="append", choices=list(SOURCES), help="fonte (pode repetir); padrão: todas")
    p.add_argument("--full", action="store_true", help="recoleta inclusive anos fechados")
    p.add_argument("--years", type=int, nargs="+", help="só estes anos (partições anuais)")
    p.add_argument("--max-minutes", type=float, help="orçamento de tempo; para de forma limpa ao atingir")

    sub.add_parser("build-db", help="cria/atualiza data/lol.duckdb com views sobre os Parquet")
    sub.add_parser("status", help="gera e mostra data/CATALOG.md")

    p = sub.add_parser("pull", help="baixa os Parquet da Release do GitHub")
    p.add_argument("--tag", default=release.TAG, help="release (padrão: data-latest; ou uma snapshot)")
    p.add_argument("--pattern", help="filtro de arquivos, ex.: 'oracles_elixir__*'")

    p = sub.add_parser("publish", help="envia os arquivos alterados para a Release (usado pelo CI)")
    p.add_argument("-s", "--source", action="append", choices=list(SOURCES), help="fonte (padrão: todas)")
    p.add_argument("--snapshot", help="também cria uma release congelada com este nome")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    if args.cmd == "collect":
        report = collect.run(args.source, full=args.full, years=args.years, max_minutes=args.max_minutes)
        catalog.write()
        print(json.dumps({k: v["status"] for k, v in report.items()}, indent=2))
        return 1 if all(v["status"] == "failed" for v in report.values()) else 0
    if args.cmd == "build-db":
        for name in db.build():
            print(name)
    elif args.cmd == "status":
        print(catalog.write())
    elif args.cmd == "pull":
        release.pull(args.tag, args.pattern)
    elif args.cmd == "publish":
        print(f"{len(release.publish(args.source, args.snapshot))} arquivos enviados")
    return 0


if __name__ == "__main__":
    sys.exit(main())
