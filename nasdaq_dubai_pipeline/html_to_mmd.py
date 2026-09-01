"""
HTML -> embedding-markdown (.mmd) conversion for Nasdaq Dubai's machine-readable
disclosures (the `body` field on /apps/sso/source/detail — LSEG-RNS-style HTML,
mainly seen for UK PLC issuers like Hikma Pharmaceuticals).

Per new_ingestion_guidelines.md's .mmd contract, every real table must appear
twice: once as inline pipe-markdown, once as a raw <table> HTML block.

RNS HTML almost always wraps the entire announcement body in an outer <table>
used purely for page layout (one row, one wide cell) — not real tabular data.
Genuine data tables are nested inside. We treat a <table> as "real" if any of
its rows has 2+ cells; pure single-cell wrapper tables are unwrapped and
recursed into rather than emitted as tables. False positives here (treating a
layout table as data) are a harmless no-op downstream — the embedding
pipeline's BERT table classifier drops anything that isn't a recognised
financial table type. False negatives (missing a real table) are the failure
mode this heuristic is written to avoid.
"""
from __future__ import annotations

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as _markdownify


def _is_wrapper_table(table: Tag) -> bool:
    rows = table.find_all("tr", recursive=True)
    top_level_rows = [r for r in rows if r.find_parent("table") is table]
    if not top_level_rows:
        return True
    return all(len(r.find_all(["td", "th"], recursive=False)) <= 1 for r in top_level_rows)


def _find_real_tables(root) -> list[Tag]:
    """Depth-first: unwrap layout tables, collect genuine (multi-column) tables.

    `find_parent` walks the *whole* tree regardless of the search root, so a
    table nested two layers deep under a wrapper has a nearest-ancestor that
    is neither `root` nor `None` — that's fine, it means some *other* table
    between it and `root` will recurse into it in its own call. Only skip
    tables whose nearest ancestor is a table other than `root` itself.
    """
    real: list[Tag] = []
    for table in root.find_all("table", recursive=True):
        nearest = table.find_parent("table")
        if nearest is not None and nearest is not root:
            continue
        if _is_wrapper_table(table):
            real.extend(_find_real_tables(table))
        else:
            real.append(table)
    return real


def _unwrap_layout_table(table: Tag) -> None:
    """Replace a single-cell wrapper <table> with its cell's inner content,
    so markdownify doesn't render plain prose inside spurious pipe-table syntax."""
    top_level_rows = [r for r in table.find_all("tr", recursive=True) if r.find_parent("table") is table]
    contents: list = []
    for row in top_level_rows:
        for cell in row.find_all(["td", "th"], recursive=False):
            contents.extend(list(cell.contents))
    if contents:
        table.replace_with(*contents)
    else:
        table.decompose()


def html_body_to_mmd(html: str, title: str | None = None) -> str:
    """Convert a Nasdaq Dubai disclosure's HTML `body` into one .mmd page."""
    soup = BeautifulSoup(html or "", "html.parser")
    real_tables = _find_real_tables(soup)

    # markdownify escapes underscores/asterisks as emphasis markers, so a
    # marker containing them (e.g. "[[TABLE_0]]") won't round-trip intact —
    # use plain alphanumerics only.
    def _marker(idx: int) -> str:
        return f"ZZZTABLEMARKERZZZ{idx}ZZZ"

    for idx, table in enumerate(real_tables):
        table.replace_with(soup.new_string(f"\n{_marker(idx)}\n"))

    # Everything still a <table> at this point is a layout wrapper (all real
    # tables were already pulled out above) — unwrap so its prose renders as
    # plain text instead of a spurious single-cell pipe table.
    for wrapper in soup.find_all("table"):
        _unwrap_layout_table(wrapper)

    text_md = _markdownify(str(soup), heading_style="ATX").strip()

    parts = [f"## Page 1", ""]
    if title:
        parts.append(f"# {title}")
        parts.append("")

    remaining = text_md
    for idx, table in enumerate(real_tables):
        before, _, remaining = remaining.partition(_marker(idx))
        if before.strip():
            parts.append(before.strip())
            parts.append("")

    if remaining.strip():
        parts.append(remaining.strip())
        parts.append("")

    for table in real_tables:
        table_html = str(table)
        table_md = _markdownify(table_html).strip()
        if table_md:
            parts.append(table_md)
            parts.append("")
        parts.append(table_html)
        parts.append("")

    return "\n".join(parts).strip() + "\n"
