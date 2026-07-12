"""Tests for shared/table_postprocess.py (Story 1.4)."""

import os

from table_postprocess import normalize_tables
from quality import score_markdown

_SAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "samples")


WELL_FORMED = (
    "# Doc\n\n"
    "| Name | Age |\n"
    "| --- | --- |\n"
    "| Alice | 30 |\n"
    "| Bob | 25 |\n"
)

SHORT_HEADER = (
    "| Name |\n"
    "| --- | --- |\n"
    "| Alice | 30 |\n"
    "| Bob | 25 |\n"
)

MERGED_CELL_BODY = (
    "| Name | Age |\n"
    "| --- | --- |\n"
    "| Alice | 30 | extra | stuff |\n"
    "| Bob | 25 |\n"
)

SHORT_BODY_ROW = (
    "| Name | Age | City |\n"
    "| --- | --- | --- |\n"
    "| Alice | 30 |\n"
)

ALIGNMENT_TABLE = (
    "| Left | Center | Right |\n"
    "| :--- | :---: | ---: |\n"
    "| a | b | c |\n"
)

NO_TABLE = "Just some plain text.\n\nAnother paragraph, no pipes at all.\n"


def test_well_formed_table_passes_through_unchanged():
    result = normalize_tables(WELL_FORMED)
    assert result.tables_found == 1
    assert result.tables_repaired == 0
    assert result.tables_unrepairable == 0
    assert "| Alice | 30 |" in result.text
    assert "| Bob | 25 |" in result.text


def test_no_tables_leaves_text_untouched():
    result = normalize_tables(NO_TABLE)
    assert result.tables_found == 0
    assert result.text == NO_TABLE.rstrip('\n')
    assert "Just some plain text." in result.text
    assert "Another paragraph" in result.text


def test_short_header_gets_padded():
    result = normalize_tables(SHORT_HEADER)
    assert result.tables_found == 1
    assert result.tables_repaired == 1
    lines = result.text.splitlines()
    header_line = lines[0]
    assert header_line.count('|') == 3  # 2 columns -> 3 pipes
    # Original single header cell preserved, second cell padded empty.
    assert 'Name' in header_line


def test_merged_cell_body_row_gets_folded_into_last_column():
    result = normalize_tables(MERGED_CELL_BODY)
    assert result.tables_found == 1
    assert result.tables_repaired == 1
    lines = result.text.splitlines()
    alice_row = next(l for l in lines if 'Alice' in l)
    cells = [c.strip() for c in alice_row.strip('|').split('|')]
    assert len(cells) == 2  # folded back to the table's 2 columns
    assert cells[0] == 'Alice'
    assert 'extra' in cells[1] and 'stuff' in cells[1]  # overflow preserved, not dropped


def test_short_body_row_gets_padded_with_empty_cells():
    result = normalize_tables(SHORT_BODY_ROW)
    lines = result.text.splitlines()
    alice_row = next(l for l in lines if 'Alice' in l)
    cells = [c.strip() for c in alice_row.strip('|').split('|')]
    assert len(cells) == 3
    assert cells == ['Alice', '30', '']


def test_alignment_markers_preserved():
    result = normalize_tables(ALIGNMENT_TABLE)
    lines = result.text.splitlines()
    sep_line = lines[1]
    cells = [c.strip() for c in sep_line.strip('|').split('|')]
    assert cells == [':---', ':---:', '---:']


def test_unrepairable_table_left_untouched_and_counted():
    garbage = (
        "|  |  |\n"
        "| --- | --- | --- |\n"
        "| x | y |\n"
    )
    result = normalize_tables(garbage)
    assert result.tables_found == 1
    assert result.tables_unrepairable == 1
    assert result.tables_repaired == 0
    assert result.text == garbage.rstrip('\n')  # untouched (modulo trailing newline)


def test_repair_clears_the_malformed_tables_quality_reason():
    """Integration with Story 1.1: after repair, quality.py's own detector
    no longer flags the (now well-formed) table as malformed."""
    before = score_markdown(SHORT_HEADER)
    assert "malformed_tables" in before.reason_codes

    result = normalize_tables(SHORT_HEADER)
    after = score_markdown(result.text)
    assert "malformed_tables" not in after.reason_codes


def test_unrepairable_table_still_flagged_by_quality_scorer_after_postprocess():
    garbage = (
        "|  |  |\n"
        "| --- | --- | --- |\n"
        "| x | y |\n"
    )
    result = normalize_tables(garbage)
    after = score_markdown(result.text)
    assert "malformed_tables" in after.reason_codes


def test_multiple_tables_in_one_document():
    doc = WELL_FORMED + "\n\n" + SHORT_HEADER
    result = normalize_tables(doc)
    assert result.tables_found == 2
    assert result.tables_repaired == 1


class TestTableHeavySampleRoundTrip:
    """Story 1.4 acceptance criteria: round-trip tests on a table-heavy
    sample under tests/samples/, not just the small per-case fixtures above."""

    def _load(self):
        path = os.path.join(_SAMPLES_DIR, "table_heavy.md")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_finds_every_table_in_the_sample(self):
        result = normalize_tables(self._load())
        # 6 tables: well-formed, short-header, merged-cell, short-body,
        # alignment, unrepairable garbage. The alignment table's columns
        # already match (3/3/3) so it needs no repair, just reformatting.
        assert result.tables_found == 6
        assert result.tables_unrepairable == 1
        assert result.tables_repaired == 3  # short-header, merged-cell, short-body

    def test_prose_between_tables_is_preserved(self):
        result = normalize_tables(self._load())
        assert "Some prose between tables" in result.text
        assert "More trailing prose after the last table" in result.text

    def test_result_is_idempotent(self):
        """A second pass over already-repaired output must not find anything
        left to change — repair converges rather than oscillating."""
        once = normalize_tables(self._load())
        twice = normalize_tables(once.text)
        assert twice.text == once.text
        assert twice.tables_repaired == 0
        assert twice.tables_unrepairable == once.tables_unrepairable

    def test_repaired_tables_no_longer_trip_the_quality_scorer(self):
        """Every repairable table's malformed_tables count should drop; only
        the genuinely unrepairable one remains flagged."""
        before = score_markdown(self._load())
        after_text = normalize_tables(self._load()).text
        after = score_markdown(after_text)
        assert after.metrics["malformed_tables"] < before.metrics["malformed_tables"]
        assert "malformed_tables" in after.reason_codes  # garbage table still flags it
