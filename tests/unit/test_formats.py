"""Behavioural tests for shared/formats.py.

The FORMATS list is the canonical registry both tiers read. Several formats share an
extension, so `detect_format_from_extension` resolves by *first match in list order* —
which makes the ordering of FORMATS load-bearing. These tests pin the resolutions that
users actually depend on, so reordering the list fails loudly instead of silently
changing which engine runs.
"""
import pytest

from formats import FORMATS, detect_format_from_extension, validate_format

pytestmark = pytest.mark.unit


class TestExtensionDetection:

    def test_md_resolves_to_markdown_not_gfm(self):
        """'markdown' and 'gfm' both claim .md; list order picks markdown."""
        assert detect_format_from_extension('.md') == 'markdown'

    def test_pdf_resolves_to_plain_pdf_not_a_marker_variant(self):
        """Four formats claim .pdf. The first is 'pdf', the Pandoc output format.

        Note this is Output Only, so callers auto-detecting an *input* format from a
        .pdf upload get a format they cannot convert from — /api/v1/convert compensates
        by resolving the engine separately.
        """
        assert detect_format_from_extension('.pdf') == 'pdf'

    def test_epub_resolves_to_epub3_not_epub2(self):
        assert detect_format_from_extension('.epub') == 'epub3'

    @pytest.mark.parametrize('ext,expected', [
        ('.html', 'html'),
        ('.docx', 'docx'),
        ('.odt', 'odt'),
        ('.rtf', 'rtf'),
        ('.tex', 'latex'),
        ('.ipynb', 'ipynb'),
        ('.rst', 'rst'),
        ('.adoc', 'asciidoc'),
    ])
    def test_unambiguous_extensions(self, ext, expected):
        assert detect_format_from_extension(ext) == expected

    def test_leading_dot_is_optional(self):
        assert detect_format_from_extension('md') == detect_format_from_extension('.md')

    def test_detection_is_case_insensitive(self):
        assert detect_format_from_extension('.MD') == 'markdown'
        assert detect_format_from_extension('.DocX') == 'docx'

    def test_unknown_extension_returns_none(self):
        assert detect_format_from_extension('.xyz') is None

    def test_empty_extension_returns_none(self):
        assert detect_format_from_extension('') is None


class TestRegistryIntegrity:

    REQUIRED_KEYS = {'name', 'key', 'direction', 'extension', 'category', 'mime_types'}
    VALID_DIRECTIONS = {'Both', 'Input Only', 'Output Only'}

    def test_every_entry_has_the_required_keys(self):
        for fmt in FORMATS:
            missing = self.REQUIRED_KEYS - set(fmt)
            assert not missing, f"{fmt.get('key')} is missing {missing}"

    def test_every_direction_is_valid(self):
        for fmt in FORMATS:
            assert fmt['direction'] in self.VALID_DIRECTIONS, (
                f"{fmt['key']} has unknown direction {fmt['direction']!r}"
            )

    def test_format_keys_are_unique(self):
        keys = [f['key'] for f in FORMATS]
        assert len(keys) == len(set(keys)), "duplicate format key in FORMATS"

    def test_every_extension_starts_with_a_dot(self):
        for fmt in FORMATS:
            assert fmt['extension'].startswith('.'), f"{fmt['key']}: {fmt['extension']}"

    def test_every_entry_declares_at_least_one_mime_type(self):
        for fmt in FORMATS:
            assert fmt['mime_types'], f"{fmt['key']} declares no mime types"

    def test_every_extension_is_detectable(self):
        """Detection must return *some* key for every registered extension."""
        for fmt in FORMATS:
            assert detect_format_from_extension(fmt['extension']) is not None

    def test_pdf_engine_variants_are_input_only(self):
        """The Marker/hybrid/OCR pseudo-formats are inputs; only 'pdf' is an output."""
        for key in ('pdf_marker', 'pdf_hybrid', 'pdf_marker_slm', 'pdf_ocr'):
            entry = next(f for f in FORMATS if f['key'] == key)
            assert entry['direction'] == 'Input Only'


class TestValidateFormat:

    def test_accepts_an_allowed_format(self):
        assert validate_format('markdown', ['markdown', 'html']) == (True, None)

    def test_rejects_a_format_outside_the_allowed_list(self):
        ok, err = validate_format('docx', ['markdown', 'html'])
        assert ok is False
        assert 'docx' in err

    def test_error_message_lists_the_allowed_formats(self):
        _ok, err = validate_format('docx', ['markdown', 'html'])
        assert 'markdown' in err and 'html' in err

    @pytest.mark.parametrize('value', ['', None])
    def test_missing_format_is_reported_as_required(self, value):
        ok, err = validate_format(value, ['markdown'])
        assert ok is False
        assert 'required' in err.lower()
