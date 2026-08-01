"""Bind the conversion capability specs.

Directory-level binding, so a .feature added under capabilities/conversion/ is collected
automatically and can never sit there silently unbound. A scenario whose steps are not
implemented fails loudly with StepDefinitionNotFoundError, which is the behaviour we
want from a spec.
"""
from pytest_bdd import scenarios

scenarios('conversion')
