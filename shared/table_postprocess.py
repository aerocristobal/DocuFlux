"""Table post-processing and normalization for converted Markdown (Story 1.4).

Marker/Pandoc output on layout-heavy PDFs frequently produces tables with
inconsistent column counts — a header row shorter than its separator, body
rows with an extra "merged-cell" column where a pipe character inside a
cell got split into a false column boundary, or misaligned separator
markers. This module repairs the common, unambiguous cases and leaves
genuinely ambiguous tables untouched (never destructively guesses), so
callers can surface those via shared/quality.py's malformed_tables reason
code instead of silently shipping broken structure.

Uses the same table-recognition pattern as shared/quality.py's
_malformed_table_count (a separator row immediately preceded by a header
row containing a pipe) so "what counts as a table" is consistent between
scoring and repair.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from quality import _TABLE_SEP_RE


@dataclass
class TablePostprocessResult:
    """Result of normalize_tables(): the repaired text plus counts."""

    text: str
    tables_found: int = 0
    tables_repaired: int = 0
    tables_unrepairable: int = 0


def _split_row(row: str) -> List[str]:
    """Split a Markdown table row into cell strings, dropping outer pipes."""
    s = row.strip()
    if s.startswith('|'):
        s = s[1:]
    if s.endswith('|'):
        s = s[:-1]
    return [c.strip() for c in s.split('|')]


def _format_row(cells: List[str]) -> str:
    return '| ' + ' | '.join(cells) + ' |'


def _alignment_of(sep_cell: str) -> str:
    """Return the canonical separator token for one column: ---, :---,
    ---:, or :---:, based on the original cell's colon markers."""
    s = sep_cell.strip()
    left = s.startswith(':')
    right = s.endswith(':')
    if left and right:
        return ':---:'
    if right:
        return '---:'
    if left:
        return ':---'
    return '---'


def _fit_row(cells: List[str], col_count: int) -> List[str]:
    """Pad a short row with empty trailing cells. Repair a long row (the
    merged-cell artifact) by folding its overflow cells into the last
    expected column, space-joined, rather than dropping content."""
    if col_count <= 0:
        return cells
    if len(cells) < col_count:
        return cells + [''] * (col_count - len(cells))
    if len(cells) > col_count:
        head = cells[:col_count - 1]
        overflow = [c for c in cells[col_count - 1:] if c]
        return head + [' '.join(overflow)]
    return cells


def normalize_tables(markdown: str) -> TablePostprocessResult:
    """Find every Markdown table, normalize alignment/header/body column
    counts, and report what was found/repaired/left alone.

    A table is left completely untouched — and counted as unrepairable —
    when its header row has no non-empty cells at all. That's not a
    ragged-column defect padding/merging can fix; it signals genuinely
    nonsensical structure (e.g. OCR garbage), so quality.py's own
    malformed_tables detection continues to catch it on the post-processed
    text rather than this module inventing a second, separate signal.
    """
    lines = markdown.splitlines()
    out_lines: List[str] = []
    tables_found = 0
    tables_repaired = 0
    tables_unrepairable = 0

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        is_table_start = (
            i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]) and '|' in line
        )
        if not is_table_start:
            out_lines.append(line)
            i += 1
            continue

        tables_found += 1
        header_line = line
        sep_line = lines[i + 1]
        sep_cells = _split_row(sep_line)
        col_count = len(sep_cells)
        header_cells = _split_row(header_line)

        body_start = i + 2
        j = body_start
        body_rows: List[List[str]] = []
        while j < n and '|' in lines[j] and lines[j].strip():
            body_rows.append(_split_row(lines[j]))
            j += 1

        if not any(c.strip() for c in header_cells):
            # No real header content at all — padding/merging can't fix
            # nonsensical structure. Leave untouched; quality.py's
            # malformed_tables check will still catch it on this text.
            tables_unrepairable += 1
            out_lines.extend(lines[i:j])
            i = j
            continue

        needed_repair = (
            len(header_cells) != col_count
            or any(len(row) != col_count for row in body_rows)
        )

        new_header = _fit_row(header_cells, col_count)
        new_sep = [_alignment_of(c) for c in sep_cells]
        new_body = [_fit_row(row, col_count) for row in body_rows]

        out_lines.append(_format_row(new_header))
        out_lines.append(_format_row(new_sep))
        out_lines.extend(_format_row(row) for row in new_body)

        if needed_repair:
            tables_repaired += 1

        i = j

    return TablePostprocessResult(
        text='\n'.join(out_lines),
        tables_found=tables_found,
        tables_repaired=tables_repaired,
        tables_unrepairable=tables_unrepairable,
    )
