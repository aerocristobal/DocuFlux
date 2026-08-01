"""Tests for scripts/check_coverage_floors.py.

This script gates CI. If it silently passed — a bad path, a swallowed exception, an
exit code of 0 on breach — the per-module floors would be decoration. So it gets the
same treatment as the code it guards.
"""
import importlib.util
import json
import pathlib

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_checker():
    """Load the script by path; scripts/ is not an importable package."""
    path = REPO_ROOT / 'scripts' / 'check_coverage_floors.py'
    spec = importlib.util.spec_from_file_location('check_coverage_floors', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def write_report(tmp_path, files, total=80.0, manifest=True, **manifest_overrides):
    report = {
        'files': {p: {'summary': {'percent_covered': pct}} for p, pct in files.items()},
        'totals': {'percent_covered': total},
    }
    path = tmp_path / 'coverage.json'
    path.write_text(json.dumps(report))
    if manifest:
        write_manifest(tmp_path, **manifest_overrides)
    return path


def write_manifest(tmp_path, full_run=True, exit_status=0, filters=None, failed=0):
    """The manifest tests/conftest.py writes at the end of a run."""
    payload = {
        'full_run': full_run,
        'exit_status': exit_status,
        'filters': filters or {'markexpr': '', 'keyword': '', 'args': ['tests'],
                               'testpaths': ['tests'], 'ignore': [], 'deselect': [],
                               'last_failed': False},
        'collected': 980,
        'failed': failed,
        'finished_at': 0.0,
    }
    (tmp_path / checker.MANIFEST_NAME).write_text(json.dumps(payload))


class TestExitCodes:
    """CI keys on these; everything else in the script is presentation."""

    def test_passes_when_every_module_meets_its_floor(self, tmp_path, capsys):
        report = write_report(tmp_path, {'a.py': 90.0, 'b.py': 75.0})
        rc = checker.main([str(report)])
        assert rc == 0
        assert 'OK' in capsys.readouterr().out

    def test_fails_when_a_module_is_below_its_floor(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(checker, 'load_config', lambda: (60, {'a.py': 90}, 0))
        report = write_report(tmp_path, {'a.py': 71.0})
        rc = checker.main([str(report)])
        assert rc == 1
        assert 'BELOW FLOOR' in capsys.readouterr().out

    def test_fails_for_an_unlisted_module_under_the_default_floor(self, tmp_path,
                                                                  monkeypatch):
        """A new module arriving with no tests must not slip through unlisted."""
        monkeypatch.setattr(checker, 'load_config', lambda: (60, {}, 0))
        report = write_report(tmp_path, {'brand_new.py': 12.0})
        assert checker.main([str(report)]) == 1

    def test_passes_for_an_unlisted_module_above_the_default_floor(self, tmp_path,
                                                                   monkeypatch):
        monkeypatch.setattr(checker, 'load_config', lambda: (60, {}, 0))
        report = write_report(tmp_path, {'brand_new.py': 88.0})
        assert checker.main([str(report)]) == 0

    def test_exits_nonzero_when_the_report_is_missing(self):
        """A missing report means the suite never ran; that must not read as success."""
        with pytest.raises(SystemExit) as exc:
            checker.main(['/nonexistent/coverage.json'])
        assert exc.value.code != 0

    def test_reports_every_breach_not_just_the_first(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(checker, 'load_config', lambda: (60, {'a.py': 90, 'b.py': 90}, 0))
        report = write_report(tmp_path, {'a.py': 10.0, 'b.py': 20.0})
        assert checker.main([str(report)]) == 1
        out = capsys.readouterr().out
        assert '2 module(s) below floor' in out
        assert 'a.py' in out and 'b.py' in out


class TestBoundaries:

    def test_exactly_at_the_floor_passes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(checker, 'load_config', lambda: (60, {'a.py': 75}, 0))
        report = write_report(tmp_path, {'a.py': 75.0})
        assert checker.main([str(report)]) == 0

    def test_a_hair_under_the_floor_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(checker, 'load_config', lambda: (60, {'a.py': 75}, 0))
        report = write_report(tmp_path, {'a.py': 74.9})
        assert checker.main([str(report)]) == 1

    def test_a_hundred_percent_floor_is_enforceable(self, tmp_path, monkeypatch):
        """Small fully-covered modules are pinned at 100, so one missed line trips."""
        monkeypatch.setattr(checker, 'load_config', lambda: (60, {'a.py': 100}, 0))
        assert checker.main([str(write_report(tmp_path, {'a.py': 100.0}))]) == 0
        assert checker.main([str(write_report(tmp_path, {'a.py': 99.4}))]) == 1


class TestEmitFloors:

    def test_rounds_down_to_a_multiple_of_five(self, tmp_path, capsys):
        report = write_report(tmp_path, {'a.py': 97.2, 'b.py': 61.0})
        checker.main([str(report), '--emit-floors'])
        out = capsys.readouterr().out
        assert '"a.py" = 95' in out
        assert '"b.py" = 60' in out

    def test_full_coverage_emits_a_hundred(self, tmp_path, capsys):
        checker.main([str(write_report(tmp_path, {'a.py': 100.0})), '--emit-floors'])
        assert '"a.py" = 100' in capsys.readouterr().out

    def test_emitted_floors_are_never_above_what_was_measured(self, tmp_path, capsys):
        """Otherwise regenerating the table would immediately fail the check."""
        measured = {'a.py': 83.3, 'b.py': 12.7, 'c.py': 99.9}
        checker.main([str(write_report(tmp_path, measured)), '--emit-floors'])
        for line in capsys.readouterr().out.splitlines():
            if '=' not in line or line.startswith('['):
                continue
            name, floor = line.split(' = ')
            assert int(floor) <= measured[name.strip('"')]

    def test_emitted_table_is_valid_toml_the_checker_can_read_back(self, tmp_path, capsys):
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover
            import tomli as tomllib
        checker.main([str(write_report(tmp_path, {'a.py': 97.2})), '--emit-floors'])
        parsed = tomllib.loads(capsys.readouterr().out)
        assert parsed['tool']['docuflux']['coverage']['floors']['a.py'] == 95


class TestStaleEntries:

    def test_warns_about_a_floor_for_a_module_that_no_longer_exists(self, tmp_path,
                                                                    capsys, monkeypatch):
        """A rename that silently drops a module's floor is how coverage quietly rots."""
        monkeypatch.setattr(checker, 'load_config', lambda: (60, {'deleted.py': 90}, 0))
        report = write_report(tmp_path, {'a.py': 95.0})
        rc = checker.main([str(report)])
        out = capsys.readouterr().out
        assert 'no longer measured' in out
        assert 'deleted.py' in out
        assert rc == 0, "a stale entry is a warning, not a build break"


class TestProjectTotal:
    """The project total is enforced here rather than as coverage.py's fail_under.

    A fail_under in .coveragerc also fires on partial runs — `pytest -m unit` covers
    less of the tree by construction — so it failed selective runs that the marker
    taxonomy exists to make useful.
    """

    def test_total_below_its_floor_fails(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(checker, 'load_config', lambda: (0, {}, 77))
        report = write_report(tmp_path, {'a.py': 100.0}, total=70.0)
        assert checker.main([str(report)]) == 1
        assert 'below the 77% floor' in capsys.readouterr().out

    def test_total_at_its_floor_passes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(checker, 'load_config', lambda: (0, {}, 77))
        report = write_report(tmp_path, {'a.py': 100.0}, total=77.0)
        assert checker.main([str(report)]) == 0

    def test_a_healthy_total_cannot_rescue_a_module_below_its_floor(self, tmp_path,
                                                                    monkeypatch):
        """The whole point: a big well-covered module must not subsidise an empty one."""
        monkeypatch.setattr(checker, 'load_config', lambda: (60, {'a.py': 90}, 50))
        report = write_report(tmp_path, {'a.py': 10.0}, total=95.0)
        assert checker.main([str(report)]) == 1


class TestRunValidation:
    """The report cannot say how it was produced; the manifest can.

    Enforcing against a filtered report produces breaches that are not real, and
    enforcing against one from a failed or aborted run gates on nothing while looking
    green. Both are refused rather than guessed at from file timestamps.
    """

    def test_a_full_passing_run_is_accepted(self, tmp_path):
        report = write_report(tmp_path, {'a.py': 95.0})
        assert checker.validate_run(report) == []

    def test_a_filtered_run_is_refused(self, tmp_path, capsys):
        report = write_report(tmp_path, {'a.py': 10.0}, full_run=False,
                              filters={'markexpr': 'unit', 'keyword': '', 'args': ['tests'],
                                       'testpaths': ['tests'], 'ignore': [], 'deselect': [],
                                       'last_failed': False})
        assert checker.main([str(report)]) == 1
        out = capsys.readouterr().out
        assert 'refusing to enforce' in out
        assert "markexpr='unit'" in out

    def test_the_refusal_is_not_reported_as_a_coverage_breach(self, tmp_path, capsys):
        """A partial run must not look like real missing coverage."""
        report = write_report(tmp_path, {'a.py': 10.0}, full_run=False)
        checker.main([str(report)])
        out = capsys.readouterr().out
        assert 'BELOW FLOOR' not in out

    def test_a_failing_run_is_refused(self, tmp_path, capsys):
        report = write_report(tmp_path, {'a.py': 95.0}, exit_status=1, failed=3)
        assert checker.main([str(report)]) == 1
        assert '3 failure(s)' in capsys.readouterr().out

    def test_a_missing_manifest_is_refused(self, tmp_path, capsys):
        """Silently enforcing an unverifiable report is how a gate becomes decoration."""
        report = write_report(tmp_path, {'a.py': 95.0}, manifest=False)
        assert checker.main([str(report)]) == 1
        assert checker.MANIFEST_NAME in capsys.readouterr().out

    def test_skip_run_check_allows_an_unverifiable_report(self, tmp_path):
        """For a coverage.json pulled from a CI artifact, with no manifest beside it."""
        report = write_report(tmp_path, {'a.py': 95.0}, manifest=False)
        assert checker.main([str(report), '--skip-run-check']) == 0

    def test_a_corrupt_manifest_is_refused(self, tmp_path, capsys):
        report = write_report(tmp_path, {'a.py': 95.0}, manifest=False)
        (tmp_path / checker.MANIFEST_NAME).write_text('{not json')
        assert checker.main([str(report)]) == 1
        assert 'could not read' in capsys.readouterr().out

    def test_emit_floors_does_not_require_a_manifest(self, tmp_path, capsys):
        """Regenerating the table is a read-only convenience, not a gate."""
        report = write_report(tmp_path, {'a.py': 97.2}, manifest=False)
        assert checker.main([str(report), '--emit-floors']) == 0
        assert '"a.py" = 95' in capsys.readouterr().out


class TestManifestContract:
    """The manifest the checker reads is the one tests/conftest.py actually writes."""

    def test_conftest_writes_the_filename_the_checker_looks_for(self):
        import tests.conftest as conftest
        assert conftest.COVERAGE_RUN_MANIFEST == checker.MANIFEST_NAME

    def test_a_bare_run_is_classified_as_full(self):
        import tests.conftest as conftest

        class _Opt:
            markexpr = keyword = ''
            ignore = deselect = None
            lf = False

        class _Cfg:
            option = _Opt()
            args = ['tests']

            def getini(self, name):
                return ['tests']

        filters = conftest._run_filters(_Cfg())
        assert filters['args'] == filters['testpaths']
        assert not filters['markexpr'] and not filters['keyword']

    def test_a_marker_filtered_run_is_classified_as_partial(self):
        import tests.conftest as conftest

        class _Opt:
            markexpr = 'unit'
            keyword = ''
            ignore = deselect = None
            lf = False

        class _Cfg:
            option = _Opt()
            args = ['tests']

            def getini(self, name):
                return ['tests']

        assert conftest._run_filters(_Cfg())['markexpr'] == 'unit'

    def test_the_real_run_produced_a_full_run_manifest(self):
        """Guards the whole mechanism end to end: whatever invocation produced this
        session wrote a manifest the checker can read."""
        manifest_path = REPO_ROOT / checker.MANIFEST_NAME
        if not manifest_path.exists():
            pytest.skip('no manifest yet — first run of the session writes it at finish')
        payload = json.loads(manifest_path.read_text())
        assert set(payload) >= {'full_run', 'exit_status', 'filters', 'failed'}


class TestRealConfig:
    """The committed table must stay usable, not just the synthetic cases above."""

    def test_pyproject_config_parses(self):
        default_floor, floors, total_floor = checker.load_config()
        assert isinstance(default_floor, int)
        assert floors, "no floors configured in pyproject.toml"
        assert 0 < total_floor <= 100, "total_floor must be set for the gate to mean anything"

    def test_every_configured_floor_is_a_sane_percentage(self):
        _default, floors, _total = checker.load_config()
        for path, floor in floors.items():
            assert 0 <= floor <= 100, f"{path} has an impossible floor: {floor}"

    def test_coveragerc_does_not_also_enforce_a_total(self):
        """Two gates is what broke `pytest -m unit`; keep enforcement in one place."""
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(REPO_ROOT / '.coveragerc')
        assert cfg.getint('report', 'fail_under', fallback=0) == 0, (
            "fail_under in .coveragerc fires on partial runs; the total floor belongs "
            "in pyproject.toml where scripts/check_coverage_floors.py enforces it"
        )
