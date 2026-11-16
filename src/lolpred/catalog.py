"""Gera data/CATALOG.md: um resumo legível do manifesto, atualizado a cada coleta."""

from __future__ import annotations

from collections import defaultdict

from .context import now_utc
from .db import view_name
from .storage import data_dir, load_manifest, manifest_sources

STATUS_LABEL = {"ok": "ok", "partial": "parcial (com erros)", "incomplete": "em andamento (carga histórica)",
                "deferred": "adiado (limite temporário da fonte)", "failed": "falhou"}


def _mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"


def render() -> str:
    manifests = {src: load_manifest(src) for src in manifest_sources()}
    lines = ["# Catálogo de dados", "",
             f"Gerado automaticamente em {now_utc():%Y-%m-%d %H:%M} UTC a partir de `data/manifest/`. "
             "Não edite à mão.", "", "## Fontes", "",
             "| Fonte | Status | Última tentativa | Último sucesso | Observação |", "|---|---|---|---|---|"]
    for name, m in manifests.items():
        s = m.get("status", {})
        msg = (s.get("message") or "").replace("|", "/")
        msg = msg[:140] + ("…" if len(msg) > 140 else "")
        lines.append(f"| {name} | {STATUS_LABEL.get(s.get('status'), s.get('status'))} | {s.get('last_attempt', '')} "
                     f"| {s.get('last_success', '')} | {msg} |")

    tables: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for m in manifests.values():
        for key, meta in m.get("datasets", {}).items():
            source, table, part = key.split("/")
            tables[(source, table)].append({**meta, "partition": part})

    lines += ["", "## Tabelas", "",
              "| View no DuckDB | Fonte/tabela | Linhas | Colunas | Partições | Período | Tamanho | Atualizada em |",
              "|---|---|---:|---:|---|---|---:|---|"]
    total_rows = total_bytes = 0
    for (source, table), parts in sorted(tables.items()):
        rows = sum(p["rows"] for p in parts)
        size = sum(p.get("bytes", 0) for p in parts)
        total_rows += rows
        total_bytes += size
        names = sorted(p["partition"] for p in parts)
        part_label = "única" if names == ["all"] else (f"{names[0]}–{names[-1]} ({len(names)})" if len(names) > 1 else names[0])
        dmins = [p["date_min"] for p in parts if p.get("date_min")]
        dmaxs = [p["date_max"] for p in parts if p.get("date_max")]
        period = f"{min(dmins)[:10]} a {max(dmaxs)[:10]}" if dmins and dmaxs else "—"
        cols = max(p["columns"] for p in parts)
        updated = max(p.get("updated_at", "") for p in parts)
        lines.append(f"| `raw.{view_name(source, table)}` | {source}/{table} | {rows:,} | {cols} | {part_label} "
                     f"| {period} | {_mb(size)} | {updated[:16]} |")
    lines += ["", f"**Total:** {total_rows:,} linhas, {_mb(total_bytes)} em Parquet.", ""]
    return "\n".join(lines)


def write() -> str:
    text = render()
    (data_dir() / "CATALOG.md").write_text(text, encoding="utf-8")
    return text
