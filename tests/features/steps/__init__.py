"""Step definitions for the executable capability specs in tests/features/capabilities.

Modules here are named `*_steps.py`, which does not match pytest.ini's
`python_files = test_*.py`, so pytest never collects them as test modules. They are
pulled into scope by star-importing them in tests/bdd/conftest.py — pytest-bdd's
given/when/then decorators produce fixtures, and fixtures are discovered from the
conftest regardless of where they were defined.
"""
