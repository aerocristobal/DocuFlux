"""Unit tests for the deterministic Markdown quality scorer (Story 1.1)."""

import pytest

from quality import (
    score_markdown,
    QualityReport,
    GRADE_GOOD,
    GRADE_FAIR,
    GRADE_POOR,
    MIN_WORDS_PER_PAGE,
)


# A well-formed, content-rich document (good).
GOOD_MD = """# Title

## Section One

""" + ("This is a sentence with plenty of words to exceed the density threshold. " * 20) + """

## Section Two

| Name | Value |
| ---- | ----- |
| a    | 1     |
| b    | 2     |

""" + ("More substantive prose follows here across the page. " * 20)


def test_good_document_scores_good():
    report = score_markdown(GOOD_MD, page_count=1)
    assert isinstance(report, QualityReport)
    assert report.grade == GRADE_GOOD
    assert report.reason_codes == []
    assert report.score >= 75


def test_scanned_pdf_three_words_per_page_is_poor():
    """Acceptance scenario: scanned PDF via Pandoc yields ~3 words/page ->
    grade 'poor' with reason 'low_word_density'."""
    # 10 pages, ~3 words each = far below MIN_WORDS_PER_PAGE.
    md = "foo bar baz\n" * 10
    report = score_markdown(md, page_count=10)
    assert report.grade == GRADE_POOR
    assert "low_word_density" in report.reason_codes
    assert report.metrics["words_per_page"] < MIN_WORDS_PER_PAGE


def test_empty_output_is_poor_with_empty_output_code():
    report = score_markdown("", page_count=1)
    assert report.grade == GRADE_POOR
    assert "empty_output" in report.reason_codes
    assert report.score == 0


def test_whitespace_only_is_empty_output():
    report = score_markdown("   \n\t  \n", page_count=1)
    assert "empty_output" in report.reason_codes


def test_no_headings_flagged():
    md = ("word " * 200)  # dense, but no headings
    report = score_markdown(md, page_count=1)
    assert "no_headings" in report.reason_codes


def test_malformed_table_flagged():
    # Header has 2 columns, body row has 3 -> malformed.
    md = "# H\n\n" + ("filler text here " * 60) + "\n\n| A | B |\n| - | - |\n| 1 | 2 | 3 |\n"
    report = score_markdown(md, page_count=1)
    assert "malformed_tables" in report.reason_codes


def test_well_formed_table_not_flagged():
    md = "# H\n\n" + ("filler text here " * 60) + "\n\n| A | B |\n| - | - |\n| 1 | 2 |\n"
    report = score_markdown(md, page_count=1)
    assert "malformed_tables" not in report.reason_codes


def test_high_garbage_ratio_flagged():
    md = "# H\n\n" + ("word " * 100) + ("\ufffd" * 200)
    report = score_markdown(md, page_count=1)
    assert "high_garbage_ratio" in report.reason_codes


def test_deterministic_repeatable():
    a = score_markdown(GOOD_MD, page_count=3)
    b = score_markdown(GOOD_MD, page_count=3)
    assert a.to_dict() == b.to_dict()


def test_to_metadata_is_string_valued():
    report = score_markdown(GOOD_MD, page_count=1)
    meta = report.to_metadata()
    assert set(meta) == {"quality_grade", "quality_score", "quality_reasons"}
    assert all(isinstance(v, str) for v in meta.values())


def test_page_count_none_defaults_to_one():
    md = "word " * 200
    r_none = score_markdown(md, page_count=None)
    r_one = score_markdown(md, page_count=1)
    assert r_none.metrics["page_count"] == 1.0
    assert r_none.score == r_one.score


@pytest.mark.parametrize("pages,words_each,expect_low", [
    (1, 100, False),   # dense single page
    (5, 3, True),      # sparse multi-page
    (2, 200, False),   # dense multi-page
])
def test_word_density_thresholds(pages, words_each, expect_low):
    md = ("# H\n\n") + ("\n".join(" ".join(["w"] * words_each) for _ in range(pages)))
    report = score_markdown(md, page_count=pages)
    assert ("low_word_density" in report.reason_codes) == expect_low
