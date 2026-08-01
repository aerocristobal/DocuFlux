"""Behavioural tests for shared/pandoc_options.py.

`validate_pandoc_options` is the whitelist standing between user-supplied JSON and a
Pandoc argv list. `build_pandoc_cmd` has partial coverage in test_worker.py
(TestBuildPandocCmd); the focus here is the validator and the defaults-merge semantics.
"""
import pytest

from pandoc_options import (
    PANDOC_OPTIONS_SCHEMA,
    PDF_DEFAULTS,
    build_pandoc_cmd,
    validate_pandoc_options,
)

pytestmark = pytest.mark.unit


class TestValidatorAcceptsWellFormedOptions:

    def test_bool_option(self):
        cleaned, errors = validate_pandoc_options({'toc': True})
        assert errors == []
        assert cleaned == {'toc': True}

    def test_enum_option(self):
        cleaned, errors = validate_pandoc_options({'pdf_engine': 'lualatex'})
        assert errors == []
        assert cleaned == {'pdf_engine': 'lualatex'}

    def test_int_option_within_range(self):
        cleaned, errors = validate_pandoc_options({'toc_depth': 3})
        assert errors == []
        assert cleaned == {'toc_depth': 3}

    def test_int_boundaries_are_inclusive(self):
        cleaned, errors = validate_pandoc_options({'dpi': 72})
        assert errors == [] and cleaned == {'dpi': 72}
        cleaned, errors = validate_pandoc_options({'dpi': 600})
        assert errors == [] and cleaned == {'dpi': 600}

    def test_negative_int_within_range_is_allowed(self):
        cleaned, errors = validate_pandoc_options({'shift_heading_level_by': -3})
        assert errors == []
        assert cleaned == {'shift_heading_level_by': -3}

    def test_dict_option_with_allowed_keys(self):
        cleaned, errors = validate_pandoc_options(
            {'variables': {'mainfont': 'Noto Sans', 'geometry': 'margin=2in'}}
        )
        assert errors == []
        assert cleaned['variables'] == {'mainfont': 'Noto Sans', 'geometry': 'margin=2in'}

    def test_dict_values_are_coerced_to_strings(self):
        cleaned, _errors = validate_pandoc_options({'variables': {'fontsize': 12}})
        assert cleaned['variables']['fontsize'] == '12'

    def test_empty_options_are_valid(self):
        assert validate_pandoc_options({}) == ({}, [])


class TestValidatorRejectsBadInput:

    def test_unknown_option_is_rejected(self):
        cleaned, errors = validate_pandoc_options({'exec_command': 'rm -rf /'})
        assert cleaned == {}
        assert any('Unknown option' in e for e in errors)

    def test_wrong_type_for_bool(self):
        _cleaned, errors = validate_pandoc_options({'toc': 'yes'})
        assert any('boolean' in e for e in errors)

    def test_enum_value_outside_the_whitelist(self):
        _cleaned, errors = validate_pandoc_options({'pdf_engine': 'evil-engine'})
        assert any('must be one of' in e for e in errors)

    def test_int_out_of_range(self):
        _cleaned, errors = validate_pandoc_options({'toc_depth': 99})
        assert any('between' in e for e in errors)

    def test_bool_is_not_accepted_as_an_int(self):
        """isinstance(True, int) is True in Python; the validator excludes bools."""
        _cleaned, errors = validate_pandoc_options({'dpi': True})
        assert any('integer' in e for e in errors)

    def test_non_dict_for_a_dict_option(self):
        _cleaned, errors = validate_pandoc_options({'variables': 'mainfont=Noto'})
        assert any('object' in e for e in errors)

    def test_disallowed_dict_key_is_rejected(self):
        _cleaned, errors = validate_pandoc_options({'variables': {'include-in-header': 'x'}})
        assert any('not an allowed key' in e for e in errors)

    def test_overlong_dict_value_is_rejected(self):
        _cleaned, errors = validate_pandoc_options({'variables': {'mainfont': 'A' * 201}})
        assert any('too long' in e for e in errors)

    @pytest.mark.parametrize('payload', [
        'Noto; rm -rf /',
        'Noto && curl evil.test',
        'Noto | sh',
        '$(whoami)',
        '`id`',
        'Noto\nmalicious',
        'a>b',
    ])
    def test_shell_metacharacters_in_dict_values_are_rejected(self, payload):
        """Values reach an argv list, but the guard is defence in depth."""
        _cleaned, errors = validate_pandoc_options({'variables': {'mainfont': payload}})
        assert any('disallowed characters' in e for e in errors), payload

    def test_valid_options_survive_alongside_an_invalid_one(self):
        """cleaned is only safe to use when errors is empty — pin that it is partial."""
        cleaned, errors = validate_pandoc_options({'toc': True, 'toc_depth': 99})
        assert errors
        assert cleaned == {'toc': True}


class TestBuildCommandDefaults:

    def test_pdf_output_applies_pdf_defaults(self):
        cmd = build_pandoc_cmd('markdown', 'pdf', 'in.md', 'out.pdf')
        assert '--pdf-engine=xelatex' in cmd
        assert '--variable' in cmd
        assert f"mainfont={PDF_DEFAULTS['variables']['mainfont']}" in cmd

    def test_non_pdf_output_applies_no_defaults(self):
        cmd = build_pandoc_cmd('markdown', 'html', 'in.md', 'out.html')
        assert '--pdf-engine=xelatex' not in cmd
        assert '--variable' not in cmd

    def test_user_option_overrides_a_pdf_default(self):
        cmd = build_pandoc_cmd('markdown', 'pdf', 'in.md', 'out.pdf',
                               {'pdf_engine': 'lualatex'})
        assert '--pdf-engine=lualatex' in cmd
        assert '--pdf-engine=xelatex' not in cmd

    def test_user_variables_merge_with_defaults_rather_than_replacing_them(self):
        cmd = build_pandoc_cmd('markdown', 'pdf', 'in.md', 'out.pdf',
                               {'variables': {'fontsize': '12pt'}})
        assert 'fontsize=12pt' in cmd
        assert f"mainfont={PDF_DEFAULTS['variables']['mainfont']}" in cmd

    def test_building_does_not_mutate_the_shared_defaults(self):
        """PDF_DEFAULTS is module-level state; a merge must not leak into later calls."""
        before = {k: (dict(v) if isinstance(v, dict) else v) for k, v in PDF_DEFAULTS.items()}
        build_pandoc_cmd('markdown', 'pdf', 'in.md', 'out.pdf',
                         {'variables': {'fontsize': '12pt'}})
        assert PDF_DEFAULTS == before

    def test_false_bool_emits_no_flag(self):
        cmd = build_pandoc_cmd('markdown', 'html', 'in.md', 'out.html', {'toc': False})
        assert '--toc' not in cmd

    def test_true_bool_emits_its_flag(self):
        cmd = build_pandoc_cmd('markdown', 'html', 'in.md', 'out.html', {'toc': True})
        assert '--toc' in cmd

    def test_command_is_an_argv_list_with_no_shell_string(self):
        cmd = build_pandoc_cmd('markdown', 'html', 'in.md', 'out.html')
        assert cmd[0] == 'pandoc'
        assert all(isinstance(part, str) for part in cmd)
        assert cmd[:5] == ['pandoc', '-f', 'markdown', '-t', 'html']

    def test_every_schema_flag_is_reachable_from_the_builder(self):
        """Guards against a schema entry whose type the builder cannot render."""
        for key, schema in PANDOC_OPTIONS_SCHEMA.items():
            assert schema['type'] in ('bool', 'enum', 'int', 'dict'), key
            assert schema['flag'].startswith('--'), key
