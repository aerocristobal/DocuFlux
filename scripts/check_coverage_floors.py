#!/usr/bin/env python3
"""Enforce a per-module coverage floor, not just a whole-project average.

A single project-wide `fail_under` lets a well-covered module subsidise an untested
one: worker/tasks/conversion.py alone is ~580 statements, enough to hide a new module
with no tests at all. This reads the per-file numbers coverage.py already emits and
checks each one against its own floor.

Usage:
    python scripts/check_coverage_floors.py [coverage.json]
    python scripts/check_coverage_floors.py --emit-floors   # regenerate the table

Floors live in pyproject.toml:

    [tool.docuflux.coverage]
    default_floor = 60          # applied to any file not listed below

    [tool.docuflux.coverage.floors]
    "shared/quality.py" = 100

Raising a floor is deliberate: run --emit-floors after adding tests and paste the
result back. Lowering one should show up in review as exactly that.
"""
import argparse
import json
import pathlib
import sys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / 'pyproject.toml'
DEFAULT_REPORT = REPO_ROOT / 'coverage.json'
# Written by tests/conftest.py::pytest_sessionfinish; see validate_run().
MANIFEST_NAME = 'coverage-run.json'

# Floors are rounded down to a multiple of this so routine refactoring does not trip
# them, while a real drop still does.
FLOOR_GRANULARITY = 5


def load_config():
    with open(PYPROJECT, 'rb') as fh:
        data = tomllib.load(fh)
    section = data.get('tool', {}).get('docuflux', {}).get('coverage', {})
    return (
        section.get('default_floor', 0),
        section.get('floors', {}),
        section.get('total_floor', 0),
    )


def validate_run(report_path):
    """Check the run that produced this report was complete and passing.

    Returns a list of reasons the report must not be enforced; empty means it is
    trustworthy.

    Coverage is written to a fixed path, so the report alone cannot say whether it came
    from the whole suite or from `pytest -m unit`, nor whether that run passed. Both
    matter: a partial report produces breaches that are not real, and a stale one from
    an aborted run gates on nothing while looking green. tests/conftest.py writes a
    manifest alongside the report recording exactly that.
    """
    manifest_path = report_path.parent / MANIFEST_NAME
    if not manifest_path.exists():
        return [
            f"{MANIFEST_NAME} not found next to {report_path.name}. It is written by "
            f"tests/conftest.py at the end of a run — re-run the suite, or pass "
            f"--skip-run-check to enforce against a report from elsewhere (a CI artifact)."
        ]

    try:
        with open(manifest_path) as fh:
            manifest = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"could not read {MANIFEST_NAME}: {exc}"]

    reasons = []
    if not manifest.get('full_run'):
        filters = manifest.get('filters', {})
        applied = ', '.join(
            f"{k}={v!r}" for k, v in filters.items() if v and k not in ('args', 'testpaths')
        ) or f"args={filters.get('args')!r}"
        reasons.append(
            f"the report came from a filtered run ({applied}), which covers less of the "
            f"tree by construction. Floors are only meaningful against a full run."
        )
    if manifest.get('exit_status') != 0:
        reasons.append(
            f"the run exited {manifest.get('exit_status')} with "
            f"{manifest.get('failed', '?')} failure(s); its coverage is not trustworthy."
        )
    return reasons


def load_measurements(report_path):
    if not report_path.exists():
        sys.exit(
            f"error: {report_path} not found. Run the suite first — pytest.ini writes it "
            f"via --cov-report=json:coverage.json."
        )
    with open(report_path) as fh:
        data = json.load(fh)
    return {
        path: entry['summary']['percent_covered']
        for path, entry in data['files'].items()
    }, data['totals']['percent_covered']


def floor_for(path, default_floor, floors):
    return floors.get(path, default_floor)


def emit_floors(measured):
    """Print a floors table derived from what the suite actually achieves."""
    print('[tool.docuflux.coverage.floors]')
    for path in sorted(measured):
        floor = int(measured[path] // FLOOR_GRANULARITY) * FLOOR_GRANULARITY
        print(f'"{path}" = {floor}')


def check(measured, total, default_floor, floors, total_floor=0):
    rows = []
    for path in sorted(measured):
        actual = measured[path]
        floor = floor_for(path, default_floor, floors)
        rows.append((path, actual, floor, actual + 1e-9 >= floor))

    width = max((len(r[0]) for r in rows), default=40)
    print(f"{'module'.ljust(width)}  {'actual':>7}  {'floor':>6}  status")
    print('-' * (width + 25))
    for path, actual, floor, ok in rows:
        print(f"{path.ljust(width)}  {actual:6.1f}%  {floor:5d}%  {'ok' if ok else 'BELOW FLOOR'}")

    breaches = [r for r in rows if not r[3]]
    stale = sorted(set(floors) - set(measured))
    total_ok = total + 1e-9 >= total_floor

    print()
    print(f"{len(rows)} modules measured, total {total:.2f}% (floor {total_floor}%)")
    if stale:
        print(f"warning: {len(stale)} floor entries no longer measured (deleted or renamed?):")
        for path in stale:
            print(f"  {path}")

    if breaches or not total_ok:
        if breaches:
            print(f"\nFAIL: {len(breaches)} module(s) below floor")
            for path, actual, floor, _ok in breaches:
                print(f"  {path}: {actual:.1f}% < {floor}%")
        if not total_ok:
            print(f"\nFAIL: total coverage {total:.2f}% is below the {total_floor}% floor")
        return 1

    print("\nOK: every module meets its floor")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('report', nargs='?', default=str(DEFAULT_REPORT),
                        help='coverage.json path (default: repo root)')
    parser.add_argument('--emit-floors', action='store_true',
                        help='print a floors table from the current measurements')
    parser.add_argument('--skip-run-check', action='store_true',
                        help='enforce even without a run manifest (e.g. a CI artifact)')
    args = parser.parse_args(argv)

    report_path = pathlib.Path(args.report)
    measured, total = load_measurements(report_path)

    if args.emit_floors:
        emit_floors(measured)
        return 0

    if not args.skip_run_check:
        problems = validate_run(report_path)
        if problems:
            print("refusing to enforce coverage floors:")
            for reason in problems:
                print(f"  - {reason}")
            return 1

    default_floor, floors, total_floor = load_config()
    return check(measured, total, default_floor, floors, total_floor)


if __name__ == '__main__':
    sys.exit(main())
