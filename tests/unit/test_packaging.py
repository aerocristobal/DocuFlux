"""Static contract tests over the build inputs (Dockerfiles, requirements files).

A fully-mocked test suite cannot observe a missing apt package or an absent `COPY` — the
code under test never runs in the image. These tests parse the build inputs instead, so
they fail in milliseconds on every PR without building anything.

Each test here corresponds to a defect that shipped to production while CI stayed green:

  * poppler-utils absent from the worker image, though pdf2image shells out to pdftoppm
  * build-variant requirements omitting pydantic-settings / boto3 / cryptography
  * shared/ importing web/, which the worker image never copies

Run standalone (no coverage plugin, no app imports):

    pytest tests/unit/test_packaging.py -q -o addopts=""
"""
import ast
import pathlib
import re
import sys

import pytest

pytestmark = pytest.mark.packaging

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Python import name -> the distribution that provides it.
# A name missing from this table fails the test on purpose: that forces the table to be
# updated when a new dependency appears, instead of silently rotting into a snapshot.
IMPORT_TO_DIST = {
    'PIL': 'Pillow',
    'boto3': 'boto3',
    'celery': 'celery',
    'cryptography': 'cryptography',
    'flask': 'flask',
    'flask_cors': 'flask-cors',
    'flask_compress': 'flask-compress',
    'flask_limiter': 'flask-limiter',
    'flask_socketio': 'flask-socketio',
    'flask_wtf': 'flask-wtf',
    'eventlet': 'eventlet',
    'gunicorn': 'gunicorn',
    'llama_cpp': 'llama-cpp-python',
    'magic': 'python-magic',
    'markupsafe': 'markupsafe',
    'marker': 'marker-pdf',
    'pdf2image': 'pdf2image',
    'prometheus_client': 'prometheus-client',
    'prometheus_flask_exporter': 'prometheus-flask-exporter',
    'pydantic': 'pydantic',
    'pydantic_settings': 'pydantic-settings',
    'pypdfium2': 'pypdfium2',
    'pytesseract': 'pytesseract',
    'redis': 'redis',
    'requests': 'requests',
    'torch': 'torch',
    'werkzeug': 'werkzeug',
    'wtforms': 'flask-wtf',
}

# Distributions that arrive transitively, so declaring the parent is sufficient.
SATISFIED_BY = {
    'Pillow': {'marker-pdf'},
    'pypdfium2': {'marker-pdf'},
    'torch': {'marker-pdf'},
    'werkzeug': {'flask'},
    'markupsafe': {'flask'},
    'pydantic': {'pydantic-settings'},
    'wtforms': {'flask-wtf'},
}

# Marker and everything it drags in are deliberately absent from the CPU image
# (see the note in worker/requirements-false.txt). Excuse them only when the variant
# genuinely ships no Marker.
MARKER_STACK = {'marker-pdf', 'torch', 'pypdfium2', 'Pillow'}

# Python import name -> the apt package providing the binary it shells out to.
BINARY_PACKAGES = {
    'pdf2image': 'poppler-utils',   # provides pdftoppm / pdftocairo
    'pytesseract': 'tesseract-ocr',  # provides the tesseract binary
}


# ── parsing helpers ───────────────────────────────────────────────────────────


def _python_files(*relative_paths):
    """Every .py file under the given repo-relative paths, excluding caches."""
    out = []
    for rel in relative_paths:
        path = REPO_ROOT / rel
        if path.is_file():
            out.append(path)
        else:
            out.extend(p for p in path.rglob('*.py') if '__pycache__' not in p.parts)
    return out


def _first_party_names(*package_dirs):
    """Module names importable as first-party, computed from the tree rather than hardcoded.

    shared/ is copied flat into /app in both images, so `import storage` resolves to
    shared/storage.py — every stem under these directories is a first-party name.
    """
    names = {'config', 'tasks', 'web', 'worker', 'shared', 'tests'}
    for rel in package_dirs:
        for p in (REPO_ROOT / rel).rglob('*.py'):
            if '__pycache__' not in p.parts:
                names.add(p.stem)
    return names


def _top_level_imports(paths):
    """Top-level module name of every import in `paths`, including function-body imports.

    Uses ast.walk rather than iterating tree.body: pdf2image, pytesseract, marker and
    torch are all imported *inside* function bodies, which is exactly how they escaped
    notice in the first place.
    """
    found = {}
    for path in paths:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.setdefault(alias.name.split('.')[0], set()).add(path)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.setdefault(node.module.split('.')[0], set()).add(path)
    return found


def _third_party_imports(source_paths, first_party):
    """Third-party import names -> the files importing them."""
    return {
        name: files
        for name, files in _top_level_imports(source_paths).items()
        if name not in sys.stdlib_module_names and name not in first_party
    }


def _declared_distributions(requirements_path):
    """Normalised distribution names declared in a requirements file."""
    declared = set()
    for raw in requirements_path.read_text(encoding='utf-8').splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line or line.startswith('-'):
            continue
        name = re.split(r'[<>=!~\[;]', line, maxsplit=1)[0].strip()
        if name:
            declared.add(name.lower().replace('_', '-'))
    return declared


def _apt_packages(dockerfile_path):
    """Every package named in an `apt-get install` block of a Dockerfile.

    Joins backslash continuations so multi-line install blocks parse, and tokenises so
    that `poppler-utils-extra` cannot satisfy a check for `poppler-utils`.
    """
    text = dockerfile_path.read_text(encoding='utf-8').replace('\\\n', ' ')
    packages = set()
    for line in text.splitlines():
        if 'apt-get install' not in line:
            continue
        tail = line.split('apt-get install', 1)[1]
        for token in re.split(r'[\s;&|]+', tail):
            if token and not token.startswith('-') and token != '$pkgs':
                packages.add(token)
    return packages


def _satisfied(dist, declared):
    normalised = dist.lower().replace('_', '-')
    if normalised in declared:
        return True
    return any(alt.lower().replace('_', '-') in declared for alt in SATISFIED_BY.get(dist, ()))


# ── Bug 1: system binaries backing Python packages ────────────────────────────


class TestSystemBinaries:
    """The worker image must install the binaries its Python dependencies shell out to."""

    def test_worker_dockerfile_installs_poppler_utils(self):
        """pdf2image shells out to pdftoppm; without poppler-utils engine=ocr dies at runtime."""
        packages = _apt_packages(REPO_ROOT / 'worker' / 'Dockerfile')
        assert 'poppler-utils' in packages, (
            "worker/Dockerfile does not install poppler-utils, but "
            "worker/tasks/conversion.py calls pdf2image.convert_from_path, which shells "
            "out to pdftoppm. The engine=ocr path fails in the container with "
            "PDFInfoNotInstalledError. CI installs poppler-utils separately, which masks this."
        )

    def test_every_shellout_dependency_has_an_apt_package(self):
        """Generalises the above: any binary-backed library needs its apt package."""
        first_party = _first_party_names('worker', 'shared')
        imported = _third_party_imports(_python_files('worker'), first_party)
        packages = _apt_packages(REPO_ROOT / 'worker' / 'Dockerfile')

        missing = {
            apt_pkg: sorted(str(p.relative_to(REPO_ROOT)) for p in imported[import_name])
            for import_name, apt_pkg in BINARY_PACKAGES.items()
            if import_name in imported and apt_pkg not in packages
        }
        assert not missing, (
            f"worker/Dockerfile is missing apt packages for binary-backed imports: {missing}"
        )


# ── Bug 2: requirements variants must cover real imports ──────────────────────


class TestRequirementsCoverImports:
    """Every distribution imported by the shipped code must be declared by the image."""

    @pytest.mark.parametrize('variant', ['worker/requirements-true.txt',
                                         'worker/requirements-false.txt'])
    def test_worker_variant_declares_every_import(self, variant):
        first_party = _first_party_names('worker', 'shared')
        imported = _third_party_imports(
            _python_files('worker', 'shared', 'config.py'), first_party
        )

        unmapped = sorted(set(imported) - set(IMPORT_TO_DIST))
        assert not unmapped, (
            f"Imports with no entry in IMPORT_TO_DIST: {unmapped}. "
            "Add them so this test keeps tracking reality."
        )

        declared = _declared_distributions(REPO_ROOT / variant)
        ships_marker = 'marker-pdf' in declared

        missing = {}
        for import_name, files in imported.items():
            dist = IMPORT_TO_DIST[import_name]
            if _satisfied(dist, declared):
                continue
            if dist in MARKER_STACK and not ships_marker:
                continue  # CPU image deliberately ships no Marker stack
            missing[dist] = sorted(str(p.relative_to(REPO_ROOT)) for p in files)[:3]

        assert not missing, (
            f"{variant} does not declare distributions the image's code imports: {missing}"
        )

    def test_web_requirements_declare_every_import(self):
        """Mirror check for the web image; passes today, guards the reverse regression."""
        first_party = _first_party_names('web', 'shared')
        imported = _third_party_imports(
            _python_files('web', 'shared', 'config.py'), first_party
        )
        unmapped = sorted(set(imported) - set(IMPORT_TO_DIST))
        assert not unmapped, f"Imports with no entry in IMPORT_TO_DIST: {unmapped}"

        declared = _declared_distributions(REPO_ROOT / 'web' / 'requirements.txt')
        missing = {
            IMPORT_TO_DIST[name]: sorted(str(p.relative_to(REPO_ROOT)) for p in files)[:3]
            for name, files in imported.items()
            if not _satisfied(IMPORT_TO_DIST[name], declared)
            and IMPORT_TO_DIST[name] not in MARKER_STACK
        }
        assert not missing, (
            f"web/requirements.txt does not declare: {missing}"
        )

    def test_dockerfile_requirements_files_exist(self):
        """Every requirements file a Dockerfile COPYs must exist on disk."""
        dockerfile = (REPO_ROOT / 'worker' / 'Dockerfile').read_text(encoding='utf-8')
        for build_gpu in ('true', 'false'):
            expected = REPO_ROOT / 'worker' / f'requirements-{build_gpu}.txt'
            assert expected.is_file(), f"{expected} is COPYed by worker/Dockerfile but missing"
        assert 'requirements-${BUILD_GPU}.txt' in dockerfile


# ── Bug 4: shared/ must not import anything the worker image lacks ────────────


def test_shared_modules_do_not_import_web():
    """shared/ is copied into the worker image; web/ is not.

    `shared/job_metadata.py` imported `web.validation` inside fire_webhook(). In the
    worker container that raises ModuleNotFoundError, which the function's own
    `except Exception` swallows and logs as "Webhook delivery failed" — so every webhook
    silently failed in production. Tests never saw it because pytest.ini puts web/ on
    sys.path.

    The invariant is "the worker image contains everything shared/ imports", so a
    `COPY web/` in the Dockerfile would also satisfy this.
    """
    dockerfile = (REPO_ROOT / 'worker' / 'Dockerfile').read_text(encoding='utf-8')
    worker_image_has_web = re.search(r'^COPY\s+web/', dockerfile, re.MULTILINE) is not None

    offenders = {
        str(path.relative_to(REPO_ROOT))
        for name, paths in _top_level_imports(_python_files('shared')).items()
        if name == 'web'
        for path in paths
    }

    assert worker_image_has_web or not offenders, (
        f"These shared/ modules import the web package, which worker/Dockerfile never "
        f"COPYs into the image: {sorted(offenders)}. Move the shared code into shared/, "
        f"or make the worker image ship web/."
    )
