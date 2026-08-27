"""Unit tests for the deterministic Markdown quality scorer (Story 1.1)."""

import pytest

from quality import (
    score_markdown,
    QualityReport,
    GRADE_GOOD,
    GRADE_FAIR,
    GRADE_POOR,
    MIN_WORDS_PER_PAGE,
    MAX_WORDS_PER_PAGE,
    MAX_CHARS_PER_PAGE,
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
    assert set(meta) == {"quality_grade", "quality_score", "quality_reasons", "quality_metrics"}
    assert all(isinstance(v, str) for v in meta.values())


def test_to_metadata_quality_metrics_is_json_of_the_per_dimension_metrics():
    """Story 1.3: quality_metrics carries the per-dimension breakdown as JSON,
    so API responses can surface it without a second scoring pass."""
    import json
    report = score_markdown(GOOD_MD, page_count=1)
    meta = report.to_metadata()
    parsed = json.loads(meta["quality_metrics"])
    assert parsed == report.metrics


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


# ── excess output (do-5wb) ────────────────────────────────────────────────────


def test_excess_words_per_page_flags_excess_output():
    """A degeneration loop emits far more words per page than a real document."""
    md = "# H\n\n" + ("word " * (MAX_WORDS_PER_PAGE + 500))
    report = score_markdown(md, page_count=1)
    assert "excess_output" in report.reason_codes
    assert "low_word_density" not in report.reason_codes


def test_excess_chars_per_page_flags_excess_output():
    """Long tokens can breach the character ceiling while staying under the word one."""
    long_token = "x" * 400
    words = MAX_CHARS_PER_PAGE // len(long_token) + 20
    md = "# H\n\n" + " ".join([long_token] * words)
    report = score_markdown(md, page_count=1)
    assert report.metrics["words_per_page"] <= MAX_WORDS_PER_PAGE
    assert "excess_output" in report.reason_codes


def test_excess_output_is_recorded_once_when_both_ceilings_breach():
    md = "# H\n\n" + ("supercalifragilistic " * (MAX_WORDS_PER_PAGE + 500))
    report = score_markdown(md, page_count=1)
    assert report.reason_codes.count("excess_output") == 1


def test_empty_output_suppresses_excess_output():
    report = score_markdown("   ", page_count=1)
    assert "empty_output" in report.reason_codes
    assert "excess_output" not in report.reason_codes


# ── repetition detection (do-5wb) ─────────────────────────────────────────────


def test_repeated_phrase_flags_repetitive_output():
    """A degeneration loop repeats a short cycle, so one bigram dominates."""
    md = "# H\n\n" + ("continued continued " * 300)
    report = score_markdown(md, page_count=1)
    assert "repetitive_output" in report.reason_codes


def test_repetition_below_threshold_is_not_flagged():
    """A phrase repeated on a longer cycle stays under the 0.3 bigram share."""
    md = "# H\n\n" + ("the same phrase over and over " * 200)
    report = score_markdown(md, page_count=1)
    assert "repetitive_output" not in report.reason_codes


def test_varied_prose_is_not_flagged_repetitive():
    report = score_markdown(GOOD_MD, page_count=1)
    assert "repetitive_output" not in report.reason_codes


def test_document_under_ten_words_is_not_scanned_for_repetition():
    report = score_markdown("# H\n\nsame same same", page_count=1)
    assert "repetitive_output" not in report.reason_codes


# ── real page boundaries (do-5wb) ─────────────────────────────────────────────


def test_per_page_word_counts_uses_real_page_boundaries():
    """Given actual per-page counts, empty pages are detected from them."""
    md = "# H\n\n" + ("word " * 120)
    report = score_markdown(md, page_count=4, per_page_word_counts=[120, 0, 0, 0])
    assert "high_empty_page_ratio" in report.reason_codes


def test_per_page_word_counts_with_no_empty_pages():
    md = "# H\n\n" + ("word " * 400)
    report = score_markdown(md, page_count=4, per_page_word_counts=[100, 100, 100, 100])
    assert "high_empty_page_ratio" not in report.reason_codes


def test_mismatched_per_page_counts_fall_back_to_chunking():
    """A length that disagrees with page_count must not be trusted."""
    md = "# H\n\n" + ("word " * 200)
    report = score_markdown(md, page_count=4, per_page_word_counts=[100, 100])
    assert isinstance(report, QualityReport)
