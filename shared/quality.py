"""Deterministic quality scoring for converted Markdown (Story 1.1).

Detects degraded conversions *before* users see them by scoring the produced
Markdown on a handful of cheap, deterministic signals:

* word density per page          -> ``low_word_density``
* heading-structure presence     -> ``no_headings``
* table well-formedness          -> ``malformed_tables``
* garbage-character ratio        -> ``high_garbage_ratio``
* empty-page ratio               -> ``high_empty_page_ratio`` / ``empty_output``

The scorer is fully deterministic (no randomness, no I/O, no model calls) so
its output is reproducible and unit-testable. It returns a :class:`QualityReport`
carrying a coarse ``grade`` (``good`` / ``fair`` / ``poor``), a numeric
``score`` in ``[0, 100]``, machine-readable ``reason_codes``, and the raw
``metrics`` it computed.

Reason codes (stable, machine-readable):
    empty_output            no usable text at all
    low_word_density        too few words per page on average
    no_headings             document has no Markdown headings
    malformed_tables        one or more Markdown tables are malformed
    high_garbage_ratio      too many non-printable / replacement characters
    high_empty_page_ratio   too many pages produced little or no text

Grades:
    good   score >= 75 and no blocking reason codes
    fair   score >= 45
    poor   otherwise (or any blocking reason code present)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


# --- Tunable thresholds (kept as module constants for documentation/tests) ---
MIN_WORDS_PER_PAGE = 50          # below this average -> low_word_density
MAX_GARBAGE_RATIO = 0.10         # >10% garbage chars -> high_garbage_ratio
MAX_EMPTY_PAGE_RATIO = 0.50      # >50% near-empty pages -> high_empty_page_ratio
EMPTY_PAGE_WORD_THRESHOLD = 5    # a page with < this many words counts as empty

GRADE_GOOD = "good"
GRADE_FAIR = "fair"
GRADE_POOR = "poor"

# Reason codes that force a "poor" grade regardless of numeric score.
BLOCKING_REASON_CODES = frozenset({"empty_output"})

# Characters considered "garbage": C0/C1 control chars (except tab/newline) and
# the Unicode replacement character produced by bad decodes.
_GARBAGE_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffd]")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S", re.MULTILINE)
_WORD_RE = re.compile(r"\S+")
# A Markdown table separator row, e.g. "| --- | :---: |", "| - | - |", "---|---".
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")


@dataclass
class QualityReport:
    """Structured, serializable result of scoring converted Markdown."""

    grade: str
    score: int
    reason_codes: List[str] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_metadata(self) -> Dict[str, str]:
        """Flatten into string values suitable for a Redis hash (job metadata)."""
        return {
            "quality_grade": self.grade,
            "quality_score": str(self.score),
            "quality_reasons": ",".join(self.reason_codes),
        }


def _count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _garbage_ratio(text: str) -> float:
    if not text:
        return 0.0
    garbage = len(_GARBAGE_RE.findall(text))
    return garbage / len(text)


def _has_headings(text: str) -> bool:
    return _HEADING_RE.search(text) is not None


def _malformed_table_count(text: str) -> int:
    """Count Markdown tables whose rows have inconsistent column counts.

    A Markdown table is recognised by a separator row (``|---|---|``) preceded
    by a header row. A table is "malformed" if the header, separator, and body
    rows do not all share the same column count.
    """
    lines = text.splitlines()
    malformed = 0
    i = 0
    n = len(lines)
    while i < n:
        if _TABLE_SEP_RE.match(lines[i]) and i > 0 and "|" in lines[i - 1]:
            header = lines[i - 1]
            sep = lines[i]
            cols = _col_count(sep)
            if _col_count(header) != cols:
                malformed += 1
            else:
                # check contiguous body rows
                j = i + 1
                body_bad = False
                while j < n and "|" in lines[j] and lines[j].strip():
                    if _col_count(lines[j]) != cols:
                        body_bad = True
                        break
                    j += 1
                if body_bad:
                    malformed += 1
                i = j
                continue
        i += 1
    return malformed


def _col_count(row: str) -> int:
    """Number of cells in a Markdown table row (ignoring leading/trailing pipes)."""
    stripped = row.strip()
    stripped = stripped.removeprefix("|") if hasattr(str, "removeprefix") else stripped.lstrip("|")
    stripped = stripped[:-1] if stripped.endswith("|") else stripped
    return len([c for c in stripped.split("|")])


def score_markdown(markdown: str, page_count: Optional[int] = None) -> QualityReport:
    """Score converted Markdown and return a :class:`QualityReport`.

    Args:
        markdown: The converted Markdown text.
        page_count: Number of source pages, if known (used for word density and
            empty-page ratio). When ``None`` or ``< 1`` it is treated as 1.
    """
    text = markdown or ""
    pages = page_count if (page_count and page_count >= 1) else 1

    total_words = _count_words(text)
    words_per_page = total_words / pages
    garbage = _garbage_ratio(text)
    has_headings = _has_headings(text)
    malformed_tables = _malformed_table_count(text)

    # Empty-page ratio: split the doc into `pages` equal word-chunks and count
    # how many fall below the empty threshold. Deterministic given the inputs.
    words = _WORD_RE.findall(text)
    if pages > 1:
        per = max(1, len(words) // pages)
        chunks = [words[k:k + per] for k in range(0, max(len(words), 1), per)][:pages]
        empty_pages = sum(1 for c in chunks if len(c) < EMPTY_PAGE_WORD_THRESHOLD)
        # account for missing chunks (fewer chunks than pages => empty pages)
        empty_pages += max(0, pages - len(chunks))
        empty_page_ratio = empty_pages / pages
    else:
        empty_page_ratio = 1.0 if total_words < EMPTY_PAGE_WORD_THRESHOLD else 0.0

    metrics = {
        "total_words": float(total_words),
        "page_count": float(pages),
        "words_per_page": round(words_per_page, 2),
        "garbage_ratio": round(garbage, 4),
        "has_headings": 1.0 if has_headings else 0.0,
        "malformed_tables": float(malformed_tables),
        "empty_page_ratio": round(empty_page_ratio, 4),
    }

    reason_codes: List[str] = []
    score = 100

    if total_words == 0 or not text.strip():
        reason_codes.append("empty_output")
        score = 0

    if words_per_page < MIN_WORDS_PER_PAGE and "empty_output" not in reason_codes:
        reason_codes.append("low_word_density")
        score -= 40

    if not has_headings:
        reason_codes.append("no_headings")
        score -= 15

    if malformed_tables > 0:
        reason_codes.append("malformed_tables")
        score -= 20

    if garbage > MAX_GARBAGE_RATIO:
        reason_codes.append("high_garbage_ratio")
        score -= 25

    if empty_page_ratio > MAX_EMPTY_PAGE_RATIO and "empty_output" not in reason_codes:
        reason_codes.append("high_empty_page_ratio")
        score -= 20

    score = max(0, min(100, score))

    blocking = any(rc in BLOCKING_REASON_CODES for rc in reason_codes)
    if blocking or score < 45:
        grade = GRADE_POOR
    elif score < 75:
        grade = GRADE_FAIR
    else:
        grade = GRADE_GOOD

    return QualityReport(grade=grade, score=score, reason_codes=reason_codes, metrics=metrics)
