"""Bind the OCR lifecycle capability specs. See tests/bdd/test_conversion_bdd.py.

Scenarios are tagged @pending until their steps land; tests/conftest.py xfails those.
"""
from pytest_bdd import scenarios

scenarios('ocr_lifecycle')
