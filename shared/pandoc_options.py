"""
Shared Pandoc options schema, validation, and command builder for DocuFlux.

Used by the web service (validation) and worker (command building).
The schema includes 'flag' keys needed by the worker; the web validator
simply ignores them.
"""

import re

_SHELL_METACHAR_RE = re.compile(r'[;&|$`\\<>(){}\n\r]')
_MAX_VALUE_LEN = 200

PANDOC_OPTIONS_SCHEMA = {
    'pdf_engine': {'type': 'enum', 'flag': '--pdf-engine', 'values': ['xelatex', 'lualatex', 'pdflatex', 'tectonic', 'wkhtmltopdf']},
    'toc': {'type': 'bool', 'flag': '--toc'},
    'toc_depth': {'type': 'int', 'flag': '--toc-depth', 'min': 1, 'max': 6},
    'number_sections': {'type': 'bool', 'flag': '--number-sections'},
    'highlight_style': {'type': 'enum', 'flag': '--highlight-style', 'values': ['pygments', 'tango', 'espresso', 'zenburn', 'kate', 'monochrome', 'breezedark', 'haddock']},
    'listings': {'type': 'bool', 'flag': '--listings'},
    'dpi': {'type': 'int', 'flag': '--dpi', 'min': 72, 'max': 600},
    'columns': {'type': 'int', 'flag': '--columns', 'min': 1, 'max': 200},
    'standalone': {'type': 'bool', 'flag': '--standalone'},
    'wrap': {'type': 'enum', 'flag': '--wrap', 'values': ['auto', 'none', 'preserve']},
    'strip_comments': {'type': 'bool', 'flag': '--strip-comments'},
    'shift_heading_level_by': {'type': 'int', 'flag': '--shift-heading-level-by', 'min': -5, 'max': 5},
    'variables': {'type': 'dict', 'flag': '--variable', 'allowed_keys': ['mainfont', 'CJKmainfont', 'monofont', 'fontsize', 'geometry', 'linestretch', 'margin-left', 'margin-right', 'margin-top', 'margin-bottom', 'papersize', 'documentclass']},
    'metadata': {'type': 'dict', 'flag': '--metadata', 'allowed_keys': ['title', 'author', 'date', 'lang', 'subject', 'description']},
}

PDF_DEFAULTS = {
    'pdf_engine': 'xelatex',
    'variables': {
        'mainfont': 'Noto Sans CJK SC',
        'CJKmainfont': 'Noto Sans CJK SC',
        'monofont': 'DejaVu Sans Mono',
        'geometry': 'margin=1in',
    },
}


def validate_pandoc_options(raw_opts):
    """Validate a pandoc_options dict against the whitelist.

    Returns (cleaned_dict, errors_list).  If errors is non-empty, cleaned
    should be discarded.
    """
    errors = []
    cleaned = {}
    for key, val in raw_opts.items():
        if key not in PANDOC_OPTIONS_SCHEMA:
            errors.append(f"Unknown option: {key}")
            continue
        schema = PANDOC_OPTIONS_SCHEMA[key]
        t = schema['type']

        if t == 'bool':
            if not isinstance(val, bool):
                errors.append(f"{key}: must be a boolean")
            else:
                cleaned[key] = val

        elif t == 'enum':
            if val not in schema['values']:
                errors.append(f"{key}: must be one of {schema['values']}")
            else:
                cleaned[key] = val

        elif t == 'int':
            if not isinstance(val, int) or isinstance(val, bool):
                errors.append(f"{key}: must be an integer")
            elif val < schema['min'] or val > schema['max']:
                errors.append(f"{key}: must be between {schema['min']} and {schema['max']}")
            else:
                cleaned[key] = val

        elif t == 'dict':
            if not isinstance(val, dict):
                errors.append(f"{key}: must be an object")
                continue
            allowed = set(schema['allowed_keys'])
            sub = {}
            for dk, dv in val.items():
                if dk not in allowed:
                    errors.append(f"{key}.{dk}: not an allowed key (allowed: {', '.join(sorted(allowed))})")
                    continue
                sv = str(dv)
                if len(sv) > _MAX_VALUE_LEN:
                    errors.append(f"{key}.{dk}: value too long (max {_MAX_VALUE_LEN} chars)")
                elif _SHELL_METACHAR_RE.search(sv):
                    errors.append(f"{key}.{dk}: value contains disallowed characters")
                else:
                    sub[dk] = sv
            if sub:
                cleaned[key] = sub

    return cleaned, errors


def build_pandoc_cmd(from_format, to_format, input_path, output_path, pandoc_options=None):
    """Build a Pandoc command list with validated options merged over PDF defaults."""
    cmd = ['pandoc', '-f', from_format, '-t', to_format if to_format != 'pdf' else 'pdf',
           input_path, '-o', output_path]

    # Start with PDF defaults when targeting PDF
    if to_format == 'pdf':
        effective = {k: (dict(v) if isinstance(v, dict) else v) for k, v in PDF_DEFAULTS.items()}
    else:
        effective = {}

    # Overlay user options (user values win; dicts are merged)
    if pandoc_options:
        for k, v in pandoc_options.items():
            if k in ('variables', 'metadata') and k in effective:
                effective[k] = {**effective[k], **v}
            else:
                effective[k] = v

    # Convert effective options to command-line flags
    for key, val in effective.items():
        schema = PANDOC_OPTIONS_SCHEMA[key]
        if schema['type'] == 'bool':
            if val:
                cmd.append(schema['flag'])
        elif schema['type'] in ('enum', 'int'):
            cmd.append(f"{schema['flag']}={val}")
        elif schema['type'] == 'dict':
            for dk, dv in val.items():
                cmd.extend([schema['flag'], f'{dk}={dv}'])

    return cmd
